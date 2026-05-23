import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.llm import complete_chat
from app.models import MessageModel, SessionModel
from app import repo

logger = logging.getLogger("my_agent")


def _format_turn(m: MessageModel) -> str:
    label = "用户" if m.role == "user" else "助手"
    return f"{label}：{m.content}"


async def summarize_text_block(
    settings: Settings, existing_summary: str | None, block: str
) -> str:
    system = (
        "你是会话摘要助手。将对话压缩为简洁中文要点，保留：用户目标、关键事实、"
        "已做决定、待办。不要编造。输出纯文本，可用短列表。"
    )
    user_parts = []
    if existing_summary and existing_summary.strip():
        user_parts.append(f"已有摘要：\n{existing_summary.strip()}\n\n")
    user_parts.append(f"需要并入的新对话片段：\n{block}")
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "".join(user_parts)},
    ]
    return (await complete_chat(settings, messages)).strip()


async def maybe_compress_session(
    db: AsyncSession, settings: Settings, session: SessionModel
) -> dict:
    """历史过长时合并旧消息为 rolling summary。返回 {updated, chars}。"""
    if not settings.memory_session_summary:
        return {"updated": False, "chars": 0}

    msgs = await repo.list_messages(db, session.id)
    dialog = [m for m in msgs if m.role in ("user", "assistant")]
    total = len(dialog)
    if total <= settings.memory_summarize_over_messages:
        return {"updated": False, "chars": len(session.summary or "")}

    keep = settings.memory_keep_recent_messages
    tail = dialog[-keep:] if keep > 0 else []
    if not tail:
        return {"updated": False, "chars": len(session.summary or "")}

    first_tail_id = tail[0].id
    up_to = session.summary_up_to_message_id or 0
    to_summarize = [m for m in dialog if up_to < m.id < first_tail_id]
    if not to_summarize:
        return {"updated": False, "chars": len(session.summary or "")}

    block = "\n".join(_format_turn(m) for m in to_summarize)
    if len(block) > 12000:
        block = block[:12000] + "\n…（截断）"

    try:
        new_summary = await summarize_text_block(
            settings, session.summary, block
        )
    except Exception:
        logger.exception("session summarize failed session=%s", session.id)
        return {"updated": False, "chars": len(session.summary or "")}

    session.summary = new_summary
    session.summary_up_to_message_id = to_summarize[-1].id
    await db.flush()
    return {"updated": True, "chars": len(new_summary)}
