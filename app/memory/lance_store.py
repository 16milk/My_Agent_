from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import lancedb

from app.config import Settings
from app.memory.embeddings import embed_text, embedding_from_json
from app.memory.types import MemoryChunkView
from app.models import MemoryChunkModel

logger = logging.getLogger("my_agent")

TABLE_NAME = "memory_chunks"


def lance_db_path(settings: Settings) -> Path:
    path = Path(settings.lance_db_path)
    if not path.is_absolute():
        path = settings.project_root / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _connect(settings: Settings):
    return lancedb.connect(str(lance_db_path(settings)))


def _list_tables(db) -> list[str]:
    result = db.list_tables()
    if hasattr(result, "tables"):
        return list(result.tables)
    if isinstance(result, list):
        return result
    return list(db.table_names())


def _open_table(settings: Settings):
    db = _connect(settings)
    if TABLE_NAME not in _list_tables(db):
        return None
    return db.open_table(TABLE_NAME)


def _arrow_rows(table) -> list[dict]:
    arrow = table.to_arrow()
    cols = arrow.column_names
    return [
        {col: arrow[col][i].as_py() for col in cols} for i in range(arrow.num_rows)
    ]


def _next_id(table) -> int:
    try:
        rows = _arrow_rows(table)
        if not rows:
            return 1
        return max(int(r["id"]) for r in rows) + 1
    except Exception:
        return 1


def _parse_created_at(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _row_to_view(row: dict) -> MemoryChunkView:
    sid = row.get("session_id") or None
    if sid == "":
        sid = None
    return MemoryChunkView(
        id=int(row["id"]),
        content=str(row["content"]),
        source=str(row.get("source") or "chat"),
        session_id=sid,
        created_at=_parse_created_at(row.get("created_at")),
    )


def _cosine_score(distance: float) -> float:
    return max(0.0, min(1.0, 1.0 - float(distance)))


def _add_chunk_sync(
    settings: Settings,
    *,
    content: str,
    vector: list[float],
    source: str,
    session_id: str | None,
) -> MemoryChunkView:
    dim = settings.openai_embedding_dimensions
    if len(vector) != dim:
        raise ValueError(f"embedding 维度 {len(vector)} != 期望 {dim}")

    now = datetime.now(timezone.utc).isoformat()
    db = _connect(settings)
    table = _open_table(settings)
    chunk_id = _next_id(table) if table is not None else 1
    record = {
        "id": chunk_id,
        "content": content,
        "vector": [float(x) for x in vector],
        "source": source,
        "session_id": session_id or "",
        "created_at": now,
    }
    if table is None:
        db.create_table(TABLE_NAME, data=[record])
    else:
        table.add([record])

    maybe_build_index_sync(settings)
    return MemoryChunkView(
        id=chunk_id,
        content=content,
        source=source,
        session_id=session_id,
        created_at=_parse_created_at(now),
    )


def maybe_build_index_sync(settings: Settings) -> None:
    if not settings.memory_lance_build_index:
        return
    table = _open_table(settings)
    if table is None:
        return
    try:
        n = table.count_rows()
    except Exception:
        return
    if n < settings.memory_lance_index_min_rows:
        return
    try:
        table.create_index(metric="cosine", replace=True)
        logger.info("lance vector index built rows=%s", n)
    except Exception:
        logger.warning("lance index build skipped", exc_info=True)


def _search_sync(
    settings: Settings,
    query_vector: list[float],
) -> list[dict]:
    table = _open_table(settings)
    if table is None or table.count_rows() == 0:
        return []

    limit = max(settings.memory_retrieve_top_k * 5, 20)
    rows = (
        table.search(query_vector, vector_column_name="vector")
        .metric("cosine")
        .limit(limit)
        .to_list()
    )
    out: list[dict] = []
    for row in rows:
        dist = float(row.get("_distance", 1.0))
        score = round(_cosine_score(dist), 4)
        if score < settings.memory_min_score:
            continue
        view = _row_to_view(row)
        out.append(
            {
                "id": view.id,
                "score": score,
                "content": view.content,
                "session_id": view.session_id,
                "created_at": view.created_at.isoformat(),
            }
        )
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[: settings.memory_retrieve_top_k]


def _list_sync(settings: Settings, limit: int) -> list[MemoryChunkView]:
    table = _open_table(settings)
    if table is None:
        return []
    views = [_row_to_view(r) for r in _arrow_rows(table)]
    views.sort(key=lambda v: v.id, reverse=True)
    return views[:limit]


def _delete_sync(settings: Settings, chunk_id: int) -> bool:
    table = _open_table(settings)
    if table is None:
        return False
    before = table.count_rows()
    table.delete(f"id = {int(chunk_id)}")
    after = table.count_rows()
    return after < before


def _count_sync(settings: Settings) -> int:
    table = _open_table(settings)
    if table is None:
        return 0
    return int(table.count_rows())


def _migrate_sqlite_rows_sync(settings: Settings, rows: list[MemoryChunkModel]) -> int:
    records: list[dict] = []
    for row in rows:
        try:
            vec = embedding_from_json(row.embedding_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if len(vec) != settings.openai_embedding_dimensions:
            continue
        created = (
            row.created_at.isoformat()
            if row.created_at
            else datetime.now(timezone.utc).isoformat()
        )
        records.append(
            {
                "id": int(row.id),
                "content": row.content,
                "vector": [float(x) for x in vec],
                "source": row.source or "chat",
                "session_id": row.session_id or "",
                "created_at": created,
            }
        )
    if not records:
        return 0

    db = _connect(settings)
    table = _open_table(settings)
    if table is None:
        db.create_table(TABLE_NAME, data=records)
    else:
        table.add(records)
    maybe_build_index_sync(settings)
    return len(records)


async def add_chunk(
    settings: Settings,
    *,
    content: str,
    vector: list[float],
    source: str = "chat",
    session_id: str | None = None,
) -> MemoryChunkView:
    return await asyncio.to_thread(
        _add_chunk_sync,
        settings,
        content=content,
        vector=vector,
        source=source,
        session_id=session_id,
    )


async def search(
    settings: Settings,
    query_vector: list[float],
) -> list[dict]:
    return await asyncio.to_thread(_search_sync, settings, query_vector)


async def list_chunks(settings: Settings, limit: int = 20) -> list[MemoryChunkView]:
    return await asyncio.to_thread(_list_sync, settings, limit)


async def delete_chunk(settings: Settings, chunk_id: int) -> bool:
    return await asyncio.to_thread(_delete_sync, settings, chunk_id)


async def count_chunks(settings: Settings) -> int:
    return await asyncio.to_thread(_count_sync, settings)


async def migrate_sqlite_to_lance_if_needed(db, settings: Settings) -> dict:
    if settings.memory_backend != "lance":
        return {"skipped": True, "reason": "backend not lance"}

    existing = await count_chunks(settings)
    if existing > 0:
        return {"skipped": True, "reason": "lance already has data", "lance_rows": existing}

    from sqlalchemy import select

    stmt = select(MemoryChunkModel).order_by(MemoryChunkModel.id.asc())
    rows = list((await db.execute(stmt)).scalars().all())
    if not rows:
        return {"migrated": 0}

    migrated = await asyncio.to_thread(_migrate_sqlite_rows_sync, settings, rows)
    logger.info("migrated %s memory chunks from sqlite to lance", migrated)
    return {"migrated": migrated, "sqlite_rows": len(rows)}
