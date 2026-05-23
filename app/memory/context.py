from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.llm import build_chat_messages
from app.models import SessionModel
from app import repo
from app.memory.summary import maybe_compress_session
from app.memory.vector import add_memory_chunk, search_memories


def _format_memory_block(items: list[dict]) -> str:
    if not items:
        return ""
    lines = ["## 相关长期记忆（向量检索，供参考）"]
    for i, it in enumerate(items, 1):
        preview = it["content"].replace("\n", " ")
        if len(preview) > 280:
            preview = preview[:280] + "…"
        lines.append(f"{i}. [相似度 {it['score']}] {preview}")
    lines.append("若与当前问题无关可忽略。")
    return "\n".join(lines)


def _build_system_prompt(
    settings: Settings,
    session: SessionModel,
    memory_items: list[dict],
) -> str:
    parts = [settings.resolved_system_prompt()]
    if session.summary and session.summary.strip():
        parts.append("## 本会话较早轮次摘要\n" + session.summary.strip())
    mem_block = _format_memory_block(memory_items)
    if mem_block:
        parts.append(mem_block)
    return "\n\n".join(parts)


async def prepare_chat_context(
    db: AsyncSession,
    settings: Settings,
    session: SessionModel,
    user_message: str,
    *,
    use_session_summary: bool,
    use_long_term_memory: bool,
) -> tuple[list[dict], dict]:
    meta: dict = {
        "summary_updated": False,
        "summary_chars": len(session.summary or ""),
        "memories": [],
    }

    if use_session_summary and settings.memory_session_summary:
        sm = await maybe_compress_session(db, settings, session)
        meta["summary_updated"] = sm.get("updated", False)
        meta["summary_chars"] = sm.get("chars", meta["summary_chars"])

    msgs = await repo.list_messages(db, session.id)
    dialog = [m for m in msgs if m.role in ("user", "assistant")]

    memory_items: list[dict] = []
    if use_long_term_memory and settings.memory_long_term:
        memory_items = await search_memories(db, settings, user_message)
        meta["memories"] = memory_items

    up_to = session.summary_up_to_message_id or 0
    recent = [m for m in dialog if m.id > up_to]
    if recent and recent[-1].role == "user":
        recent = recent[:-1]
    keep = settings.memory_keep_recent_messages
    history = [(m.role, m.content) for m in recent[-keep:]]

    system = _build_system_prompt(settings, session, memory_items)
    messages = build_chat_messages(system, history, user_message)
    return messages, meta


async def after_assistant_reply(
    db: AsyncSession,
    settings: Settings,
    session_id: str,
    user_message: str,
    assistant_reply: str,
    *,
    use_long_term_memory: bool,
) -> None:
    if not use_long_term_memory or not settings.memory_long_term:
        return
    content = f"用户：{user_message.strip()}\n助手：{assistant_reply.strip()}"
    await add_memory_chunk(
        db, settings, content, session_id=session_id, source="chat"
    )
