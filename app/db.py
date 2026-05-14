from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from sqlalchemy import event

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
        )

        @event.listens_for(_engine.sync_engine, "connect")
        def _sqlite_enable_fk(dbapi_connection, _connection_record):
            if settings.database_url.startswith("sqlite"):
                dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory
