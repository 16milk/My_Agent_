from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import Settings


def _is_under_any_root(path: Path, roots: list[Path]) -> bool:
    rp = path.resolve()
    for root in roots:
        rr = root.resolve()
        try:
            rp.relative_to(rr)
            return True
        except ValueError:
            continue
    return False


def _read_file_sync(path: Path, max_bytes: int) -> tuple[str, bool]:
    data = path.read_bytes()[:max_bytes]
    truncated = path.stat().st_size > max_bytes
    text = data.decode("utf-8", errors="replace")
    return text, truncated


async def tool_read_file(path_arg: str, settings: Settings) -> str:
    if ".." in path_arg:
        return json.dumps(
            {"ok": False, "error": "路径不得包含 .."},
            ensure_ascii=False,
        )
    raw_path = Path(path_arg)
    if raw_path.is_absolute():
        return json.dumps(
            {"ok": False, "error": "禁止使用绝对路径"},
            ensure_ascii=False,
        )
    roots = settings.resolved_tool_file_roots()
    candidate: Path | None = None
    for r in roots:
        c = (r / path_arg).resolve()
        if _is_under_any_root(c, roots) and c.is_file():
            candidate = c
            break
    if candidate is None:
        return json.dumps(
            {"ok": False, "error": "文件不存在或不在允许根目录内", "path": path_arg},
            ensure_ascii=False,
        )
    try:
        text, truncated = await asyncio.to_thread(
            _read_file_sync, candidate, settings.tool_max_file_bytes
        )
    except OSError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    return json.dumps(
        {
            "ok": True,
            "path": str(candidate),
            "truncated": truncated,
            "content": text,
        },
        ensure_ascii=False,
    )


def _host_allowed(host: str, allowed: list[str]) -> bool:
    h = host.lower()
    if "*" in allowed:
        return True
    return h in allowed


async def tool_http_get(url: str, settings: Settings) -> str:
    allowed = settings.resolved_http_allowed_hosts()
    if not allowed:
        return json.dumps(
            {"ok": False, "error": "http_get 未启用：请在 .env 配置 TOOL_HTTP_ALLOWED_HOSTS"},
            ensure_ascii=False,
        )
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return json.dumps({"ok": False, "error": "仅允许 http/https"}, ensure_ascii=False)
    if parsed.scheme == "http" and (parsed.hostname or "").lower() not in (
        "localhost",
        "127.0.0.1",
    ):
        return json.dumps(
            {"ok": False, "error": "非 HTTPS 仅允许 localhost/127.0.0.1"},
            ensure_ascii=False,
        )
    host = (parsed.hostname or "").lower()
    if not host or not _host_allowed(host, allowed):
        return json.dumps(
            {"ok": False, "error": f"主机不在白名单: {host}"},
            ensure_ascii=False,
        )
    timeout = httpx.Timeout(settings.tool_http_timeout_sec)
    limits = httpx.Limits(max_connections=5, max_keepalive_connections=2)
    max_bytes = settings.tool_http_max_bytes
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            clen = resp.headers.get("content-length")
            if clen and clen.isdigit() and int(clen) > max_bytes:
                return json.dumps(
                    {
                        "ok": False,
                        "error": f"Content-Length 超过上限 {max_bytes}",
                    },
                    ensure_ascii=False,
                )
            raw = resp.content[:max_bytes]
            truncated = len(resp.content) > max_bytes
            text = raw.decode("utf-8", errors="replace")
            return json.dumps(
                {
                    "ok": resp.is_success,
                    "status_code": resp.status_code,
                    "truncated": truncated,
                    "headers": {
                        k: v
                        for k, v in resp.headers.items()
                        if k.lower() in ("content-type", "content-length")
                    },
                    "text": text,
                },
                ensure_ascii=False,
            )
    except httpx.HTTPError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


async def dispatch_tool(name: str, arguments_json: str, settings: Settings) -> str:
    try:
        args = json.loads(arguments_json or "{}")
    except json.JSONDecodeError as e:
        return json.dumps({"ok": False, "error": f"参数 JSON 无效: {e}"}, ensure_ascii=False)
    if name == "read_file":
        return await tool_read_file(str(args.get("path", "")), settings)
    if name == "http_get":
        return await tool_http_get(str(args.get("url", "")), settings)
    return json.dumps({"ok": False, "error": f"未知工具: {name}"}, ensure_ascii=False)
