from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.memory.vector import add_memory_chunk

logger = logging.getLogger("my_agent")


def _is_under_roots(path: Path, roots: list[Path]) -> bool:
    rp = path.resolve()
    for root in roots:
        try:
            rp.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def _chunk_text(text: str, chunk_size: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunks.append(text[start:end])
        start = end
    return chunks


async def index_folder(
    db: AsyncSession,
    settings: Settings,
    relative_path: str,
    *,
    recursive: bool = True,
) -> dict:
    if not settings.memory_long_term:
        return {"ok": False, "error": "长期记忆未启用", "indexed": 0}

    if ".." in relative_path:
        return {"ok": False, "error": "路径不得包含 ..", "indexed": 0}

    roots = settings.resolved_tool_file_roots()
    folder = (roots[0] / relative_path).resolve()
    if not _is_under_roots(folder, roots):
        return {"ok": False, "error": "路径不在允许根目录内", "indexed": 0}
    if not folder.is_dir():
        return {"ok": False, "error": "不是目录", "indexed": 0}

    exts = settings.resolved_index_extensions()
    pattern = "**/*" if recursive else "*"
    files = [p for p in folder.glob(pattern) if p.is_file() and p.suffix.lower() in exts]

    indexed = 0
    skipped = 0
    chunk_size = settings.memory_index_chunk_chars

    for fp in files:
        try:
            raw = fp.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("skip file %s: %s", fp, e)
            skipped += 1
            continue
        rel = str(fp.relative_to(roots[0]))
        for i, piece in enumerate(_chunk_text(raw, chunk_size)):
            header = f"[文件 {rel} 片段 {i + 1}]\n"
            content = header + piece
            if len(content) > settings.memory_index_max_chars:
                content = content[: settings.memory_index_max_chars] + "…"
            row = await add_memory_chunk(
                db, settings, content, session_id=None, source="folder"
            )
            if row is None:
                skipped += 1
                break
            indexed += 1

    if settings.memory_backend.strip().lower() == "lance":
        from app.memory.lance_store import maybe_build_index_sync

        await asyncio.to_thread(maybe_build_index_sync, settings)

    return {
        "ok": True,
        "indexed": indexed,
        "skipped": skipped,
        "files": len(files),
        "path": relative_path,
    }
