import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import MessageModel, SessionModel, UsageLogModel


async def create_session(
    db: AsyncSession, *, title: str | None = None, persona_id: str | None = None
) -> SessionModel:
    now = datetime.now(timezone.utc)
    row = SessionModel(
        id=str(uuid.uuid4()),
        title=title or "新对话",
        persona_id=persona_id,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def get_session(db: AsyncSession, session_id: str) -> SessionModel | None:
    stmt = (
        select(SessionModel)
        .where(SessionModel.id == session_id)
        .options(selectinload(SessionModel.messages))
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def count_messages(db: AsyncSession, session_id: str) -> int:
    stmt = select(func.count()).select_from(MessageModel).where(
        MessageModel.session_id == session_id
    )
    return int((await db.execute(stmt)).scalar_one())


async def list_messages(db: AsyncSession, session_id: str) -> list[MessageModel]:
    stmt = (
        select(MessageModel)
        .where(MessageModel.session_id == session_id)
        .order_by(MessageModel.id.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def touch_session(
    db: AsyncSession,
    session_id: str,
    *,
    title_from_message: str | None = None,
    persona_id: str | None = None,
) -> None:
    values: dict = {"updated_at": datetime.now(timezone.utc)}
    if persona_id is not None:
        values["persona_id"] = persona_id
    if title_from_message:
        row = await get_session(db, session_id)
        if row and not (row.title or "").strip():
            t = title_from_message.strip().replace("\n", " ")[:80]
            values["title"] = t or "新对话"
    await db.execute(
        update(SessionModel).where(SessionModel.id == session_id).values(**values)
    )


async def list_sessions(db: AsyncSession, limit: int = 50) -> list[SessionModel]:
    stmt = (
        select(SessionModel)
        .order_by(SessionModel.updated_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def update_session_title(db: AsyncSession, session_id: str, title: str) -> bool:
    result = await db.execute(
        update(SessionModel)
        .where(SessionModel.id == session_id)
        .values(title=title[:200], updated_at=datetime.now(timezone.utc))
    )
    return result.rowcount > 0


async def add_message(
    db: AsyncSession, session_id: str, role: str, content: str
) -> MessageModel:
    row = MessageModel(session_id=session_id, role=role, content=content)
    db.add(row)
    await db.flush()
    if role == "user":
        await touch_session(db, session_id, title_from_message=content)
    else:
        await touch_session(db, session_id)
    await db.refresh(row)
    return row


async def add_usage_log(
    db: AsyncSession,
    *,
    request_id: str | None,
    session_id: str | None,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    embedding_tokens: int,
    estimated_usd: float,
    elapsed_ms: float,
    ttft_ms: float | None,
) -> UsageLogModel:
    row = UsageLogModel(
        request_id=request_id,
        session_id=session_id,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        embedding_tokens=embedding_tokens,
        estimated_usd=estimated_usd,
        elapsed_ms=elapsed_ms,
        ttft_ms=ttft_ms,
    )
    db.add(row)
    await db.flush()
    return row


async def usage_stats_summary(db: AsyncSession, limit: int = 200) -> dict:
    stmt = (
        select(UsageLogModel)
        .order_by(UsageLogModel.id.desc())
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return {
        "requests": len(rows),
        "prompt_tokens": sum(r.prompt_tokens for r in rows),
        "completion_tokens": sum(r.completion_tokens for r in rows),
        "embedding_tokens": sum(r.embedding_tokens for r in rows),
        "estimated_usd": round(sum(r.estimated_usd for r in rows), 6),
    }


async def delete_session(db: AsyncSession, session_id: str) -> bool:
    row = await get_session(db, session_id)
    if row is None:
        return False
    await db.execute(delete(SessionModel).where(SessionModel.id == session_id))
    return True


async def clear_messages(db: AsyncSession, session_id: str) -> int:
    n = await count_messages(db, session_id)
    await db.execute(delete(MessageModel).where(MessageModel.session_id == session_id))
    await db.execute(
        update(SessionModel)
        .where(SessionModel.id == session_id)
        .values(summary=None, summary_up_to_message_id=None)
    )
    return n
