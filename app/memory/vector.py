from __future__ import annotations

import json
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.memory.embeddings import (
    cosine_similarity,
    embed_text,
    embedding_from_json,
    embedding_to_json,
)
from app.memory.types import MemoryChunkView
from app.usage import UsageAccumulator, estimate_tokens_from_text
from app.models import MemoryChunkModel

logger = logging.getLogger("my_agent")


def uses_lance(settings: Settings) -> bool:
    return settings.memory_backend.strip().lower() == "lance"


async def add_memory_chunk(
    db: AsyncSession,
    settings: Settings,
    content: str,
    *,
    session_id: str | None = None,
    source: str = "chat",
) -> MemoryChunkView | None:
    text = content.strip()
    if not text or not settings.memory_long_term:
        return None
    if len(text) > settings.memory_index_max_chars:
        text = text[: settings.memory_index_max_chars] + "…"
    try:
        vec = await embed_text(settings, text)
    except Exception:
        logger.exception("embedding failed, skip memory index")
        return None

    if uses_lance(settings):
        from app.memory import lance_store

        return await lance_store.add_chunk(
            settings,
            content=text,
            vector=vec,
            source=source,
            session_id=session_id,
        )

    row = MemoryChunkModel(
        content=text,
        embedding_json=embedding_to_json(vec),
        source=source,
        session_id=session_id,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return MemoryChunkView(
        id=row.id,
        content=row.content,
        source=row.source,
        session_id=row.session_id,
        created_at=row.created_at,
    )


async def search_memories(
    db: AsyncSession,
    settings: Settings,
    query: str,
    usage: UsageAccumulator | None = None,
) -> list[dict]:
    if not settings.memory_long_term:
        return []
    q = query.strip()
    if not q:
        return []
    try:
        q_vec = await embed_text(settings, q)
        if usage:
            usage.add_embedding_tokens(estimate_tokens_from_text(q))
    except Exception:
        logger.exception("query embedding failed")
        return []

    if uses_lance(settings):
        from app.memory import lance_store

        return await lance_store.search(settings, q_vec)

    stmt = (
        select(MemoryChunkModel)
        .order_by(MemoryChunkModel.id.desc())
        .limit(settings.memory_search_pool)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    scored: list[tuple[float, MemoryChunkModel]] = []
    for row in rows:
        try:
            vec = embedding_from_json(row.embedding_json)
        except (json.JSONDecodeError, TypeError):
            continue
        score = cosine_similarity(q_vec, vec)
        if score >= settings.memory_min_score:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[: settings.memory_retrieve_top_k]
    return [
        {
            "id": row.id,
            "score": round(score, 4),
            "content": row.content,
            "session_id": row.session_id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for score, row in top
    ]


async def list_memory_chunks(
    db: AsyncSession, settings: Settings, limit: int = 20
) -> list[MemoryChunkView]:
    if uses_lance(settings):
        from app.memory import lance_store

        return await lance_store.list_chunks(settings, limit=limit)

    stmt = (
        select(MemoryChunkModel)
        .order_by(MemoryChunkModel.id.desc())
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return [
        MemoryChunkView(
            id=r.id,
            content=r.content,
            source=r.source,
            session_id=r.session_id,
            created_at=r.created_at,
        )
        for r in rows
    ]


async def delete_memory_chunk(
    db: AsyncSession, settings: Settings, chunk_id: int
) -> bool:
    if uses_lance(settings):
        from app.memory import lance_store

        return await lance_store.delete_chunk(settings, chunk_id)

    result = await db.execute(
        delete(MemoryChunkModel).where(MemoryChunkModel.id == chunk_id)
    )
    return result.rowcount > 0
