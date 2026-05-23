from datetime import datetime, timezone

from sqlalchemy import select, update

from app.auth import hash_api_key
from app.models import ApiKeyModel


async def verify_db_api_key(db, raw_key: str) -> bool:
    h = hash_api_key(raw_key)
    stmt = select(ApiKeyModel).where(
        ApiKeyModel.key_hash == h, ApiKeyModel.enabled.is_(True)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def touch_api_key_usage(db, raw_key: str) -> None:
    h = hash_api_key(raw_key)
    await db.execute(
        update(ApiKeyModel)
        .where(ApiKeyModel.key_hash == h)
        .values(last_used_at=datetime.now(timezone.utc))
    )


async def create_api_key(db, name: str) -> tuple[ApiKeyModel, str]:
    from app.auth import generate_api_key

    raw = generate_api_key()
    row = ApiKeyModel(
        name=name,
        key_hash=hash_api_key(raw),
        key_prefix=raw[:12] + "…",
        enabled=True,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row, raw


async def list_api_keys(db) -> list[ApiKeyModel]:
    stmt = select(ApiKeyModel).order_by(ApiKeyModel.id.desc())
    return list((await db.execute(stmt)).scalars().all())


async def set_api_key_enabled(db, key_id: int, enabled: bool) -> bool:
    result = await db.execute(
        update(ApiKeyModel)
        .where(ApiKeyModel.id == key_id)
        .values(enabled=enabled)
    )
    return result.rowcount > 0


async def delete_api_key(db, key_id: int) -> bool:
    row = await db.get(ApiKeyModel, key_id)
    if row is None:
        return False
    await db.delete(row)
    return True
