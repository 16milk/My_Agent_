"""API Key 鉴权：环境变量密钥 + 数据库哈希密钥。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import TYPE_CHECKING

from fastapi import Request
from starlette.responses import JSONResponse

from app.config import Settings, get_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

PUBLIC_PATHS = frozenset({"/", "/health", "/api/config"})


def generate_api_key() -> str:
    return f"ma_{secrets.token_urlsafe(32)}"


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def extract_api_key(request: Request, header_name: str = "X-API-Key") -> str | None:
    direct = request.headers.get(header_name)
    if direct and direct.strip():
        return direct.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token
    return None


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS


def env_keys_match(settings: Settings, raw_key: str) -> bool:
    for k in settings.resolved_env_api_keys():
        if hmac.compare_digest(k, raw_key):
            return True
    return False


async def verify_api_key(db: "AsyncSession | None", raw_key: str | None) -> bool:
    if not raw_key:
        return False
    settings = get_settings()
    if env_keys_match(settings, raw_key):
        return True
    if db is None:
        return False
    from app import repo_auth

    return await repo_auth.verify_db_api_key(db, raw_key)


async def auth_middleware(request: Request, call_next):
    settings = get_settings()
    if not settings.auth_enabled:
        request.state.api_key_valid = True
        return await call_next(request)

    if is_public_path(request.url.path):
        return await call_next(request)

    raw = extract_api_key(request, settings.auth_header_name)
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        ok = await verify_api_key(db, raw)
        if ok and raw and not env_keys_match(settings, raw):
            from app import repo_auth

            await repo_auth.touch_api_key_usage(db, raw)
            await db.commit()

    if not ok:
        return JSONResponse(
            status_code=401,
            content={"detail": "无效或缺失 API Key，请设置请求头 X-API-Key 或 Authorization: Bearer"},
        )

    request.state.api_key_valid = True
    return await call_next(request)
