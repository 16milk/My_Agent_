import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import MessageModel, SessionModel


async def create_session(db: AsyncSession) -> SessionModel:
    row = SessionModel(id=str(uuid.uuid4()))
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


async def add_message(
    db: AsyncSession, session_id: str, role: str, content: str
) -> MessageModel:
    row = MessageModel(session_id=session_id, role=role, content=content)
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


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
