import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from app.auth import auth_middleware
from app.config import get_settings
from app.db import Base, get_engine, get_session_factory
from app.migrate import migrate_sqlite_schema
from app.agent import complete_with_tools, stream_with_tools, tools_for_request
from app.memory.context import after_assistant_reply, prepare_chat_context
from app.memory.indexer import index_folder
from app.memory.vector import delete_memory_chunk, list_memory_chunks
from app.personas import list_personas
from app.request_settings import effective_settings
from app.usage import UsageAccumulator
from app import repo
from app import repo_auth
from app.repo_tasks import (
    TASK_TYPES,
    create_task,
    delete_task,
    get_task,
    list_task_runs,
    list_tasks,
    update_task,
)
from app.tasks.executor import execute_scheduled_task
from app.tasks.scheduler import reload_scheduler_jobs, shutdown_scheduler, start_scheduler, validate_cron
from app.schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyListItem,
    ChatRequest,
    ChatResponse,
    ConfigResponse,
    IndexFolderRequest,
    MemoryItem,
    MessageItem,
    PersonaItem,
    ScheduledTaskCreate,
    ScheduledTaskItem,
    ScheduledTaskUpdate,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionInfoResponse,
    SessionListItem,
    SessionUpdateRequest,
    TaskRunItem,
    UsageStatsResponse,
)

logger = logging.getLogger("my_agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(migrate_sqlite_schema)
    start_scheduler()
    await reload_scheduler_jobs()
    yield
    shutdown_scheduler()
    await engine.dispose()


app = FastAPI(title="My_Agent_", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_key_auth_middleware(request: Request, call_next):
    return await auth_middleware(request, call_next)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    response: Response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "%s %s %s %.2fms",
        request_id[:8],
        request.method,
        request.url.path,
        elapsed_ms,
    )
    return response


def _memory_flags(body: ChatRequest, settings) -> tuple[bool, bool]:
    if not settings.memory_enabled:
        return False, False
    return body.use_session_summary, body.use_long_term_memory


async def _build_messages_after_user_turn(
    factory,
    session_id: str,
    body: ChatRequest,
    settings,
    usage: UsageAccumulator,
) -> tuple[list[dict], dict]:
    eff = effective_settings(settings, body)
    use_summary, use_long_term = _memory_flags(body, settings)
    async with factory() as db:
        row = await repo.get_session(db, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        await repo.add_message(db, session_id, "user", body.message)
        if body.persona_id:
            await repo.touch_session(db, session_id, persona_id=body.persona_id)
        await db.flush()
        messages, meta = await prepare_chat_context(
            db,
            eff,
            row,
            body.message,
            use_session_summary=use_summary,
            use_long_term_memory=use_long_term,
            usage=usage,
        )
        await db.commit()
    return messages, meta


async def _persist_usage(
    factory,
    *,
    request_id: str,
    session_id: str,
    model: str,
    usage: UsageAccumulator,
    elapsed_ms: float,
    ttft_ms: float | None,
) -> dict:
    u = usage.to_dict(model=model, elapsed_ms=elapsed_ms, ttft_ms=ttft_ms)
    async with factory() as db:
        await repo.add_usage_log(
            db,
            request_id=request_id,
            session_id=session_id,
            model=model,
            prompt_tokens=u["prompt_tokens"],
            completion_tokens=u["completion_tokens"],
            embedding_tokens=u["embedding_tokens"],
            estimated_usd=u["estimated_usd"],
            elapsed_ms=u["elapsed_ms"],
            ttft_ms=u["ttft_ms"],
        )
        await db.commit()
    return u


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/config", response_model=ConfigResponse)
async def get_config():
    s = get_settings()
    return ConfigResponse(
        default_model=s.openai_model,
        available_models=s.resolved_available_models,
        personas=[PersonaItem(**p) for p in list_personas()],
        memory_enabled=s.memory_enabled,
        auth_required=s.auth_enabled,
        auth_header_name=s.auth_header_name,
        scheduler_enabled=s.scheduler_enabled,
        task_types=sorted(TASK_TYPES),
        features={
            "tools": True,
            "session_summary": s.memory_session_summary,
            "long_term_memory": s.memory_long_term,
            "usage_tracking": True,
            "auth": s.auth_enabled,
            "scheduler": s.scheduler_enabled,
        },
    )


def _task_item(row) -> ScheduledTaskItem:
    return ScheduledTaskItem(
        id=row.id,
        name=row.name,
        task_type=row.task_type,
        cron=row.cron,
        payload=json.loads(row.payload_json or "{}"),
        enabled=row.enabled,
        last_run_at=row.last_run_at,
        last_status=row.last_status,
        last_error=row.last_error,
        created_at=row.created_at,
    )


@app.get("/api/keys", response_model=list[ApiKeyListItem])
async def list_api_keys():
    factory = get_session_factory()
    async with factory() as db:
        rows = await repo_auth.list_api_keys(db)
    return [
        ApiKeyListItem(
            id=r.id,
            name=r.name,
            key_prefix=r.key_prefix,
            enabled=r.enabled,
            created_at=r.created_at,
            last_used_at=r.last_used_at,
        )
        for r in rows
    ]


@app.post("/api/keys", response_model=ApiKeyCreateResponse)
async def create_api_key(body: ApiKeyCreateRequest):
    factory = get_session_factory()
    async with factory() as db:
        row, raw = await repo_auth.create_api_key(db, body.name)
        await db.commit()
    return ApiKeyCreateResponse(
        id=row.id,
        name=row.name,
        key=raw,
        key_prefix=row.key_prefix,
        created_at=row.created_at,
    )


@app.patch("/api/keys/{key_id}/enable")
async def enable_api_key(key_id: int, enabled: bool = True):
    factory = get_session_factory()
    async with factory() as db:
        ok = await repo_auth.set_api_key_enabled(db, key_id, enabled)
        await db.commit()
    if not ok:
        raise HTTPException(status_code=404, detail="key not found")
    return {"ok": True}


@app.delete("/api/keys/{key_id}", status_code=204)
async def remove_api_key(key_id: int):
    factory = get_session_factory()
    async with factory() as db:
        ok = await repo_auth.delete_api_key(db, key_id)
        await db.commit()
    if not ok:
        raise HTTPException(status_code=404, detail="key not found")
    return Response(status_code=204)


@app.get("/api/tasks", response_model=list[ScheduledTaskItem])
async def list_scheduled_tasks():
    factory = get_session_factory()
    async with factory() as db:
        rows = await list_tasks(db)
    return [_task_item(r) for r in rows]


@app.post("/api/tasks", response_model=ScheduledTaskItem)
async def create_scheduled_task(body: ScheduledTaskCreate):
    try:
        validate_cron(body.cron)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if body.task_type not in TASK_TYPES:
        raise HTTPException(status_code=400, detail=f"task_type 须为 {sorted(TASK_TYPES)}")
    factory = get_session_factory()
    async with factory() as db:
        try:
            row = await create_task(
                db,
                name=body.name,
                task_type=body.task_type,
                cron=body.cron,
                payload=body.payload,
                enabled=body.enabled,
            )
            await db.commit()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    await reload_scheduler_jobs()
    return _task_item(row)


@app.patch("/api/tasks/{task_id}", response_model=ScheduledTaskItem)
async def patch_scheduled_task(task_id: int, body: ScheduledTaskUpdate):
    if body.cron:
        try:
            validate_cron(body.cron)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    factory = get_session_factory()
    async with factory() as db:
        row = await update_task(
            db,
            task_id,
            name=body.name,
            cron=body.cron,
            payload=body.payload,
            enabled=body.enabled,
        )
        await db.commit()
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    await reload_scheduler_jobs()
    return _task_item(row)


@app.delete("/api/tasks/{task_id}", status_code=204)
async def remove_scheduled_task(task_id: int):
    factory = get_session_factory()
    async with factory() as db:
        ok = await delete_task(db, task_id)
        await db.commit()
    if not ok:
        raise HTTPException(status_code=404, detail="task not found")
    await reload_scheduler_jobs()
    return Response(status_code=204)


@app.post("/api/tasks/{task_id}/run")
async def run_scheduled_task_now(task_id: int):
    result = await execute_scheduled_task(task_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "run failed"))
    return result


@app.get("/api/tasks/{task_id}/runs", response_model=list[TaskRunItem])
async def list_scheduled_task_runs(task_id: int, limit: int = 20):
    factory = get_session_factory()
    async with factory() as db:
        if await get_task(db, task_id) is None:
            raise HTTPException(status_code=404, detail="task not found")
        rows = await list_task_runs(db, task_id, limit=limit)
    return [
        TaskRunItem(
            id=r.id,
            task_id=r.task_id,
            status=r.status,
            result=json.loads(r.result_json) if r.result_json else None,
            error=r.error,
            started_at=r.started_at,
            finished_at=r.finished_at,
        )
        for r in rows
    ]


@app.get("/api/usage/stats", response_model=UsageStatsResponse)
async def usage_stats():
    factory = get_session_factory()
    async with factory() as db:
        data = await repo.usage_stats_summary(db)
    return UsageStatsResponse(**data)


@app.get("/api/sessions", response_model=list[SessionListItem])
async def list_sessions(limit: int = 50):
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be 1-100")
    factory = get_session_factory()
    async with factory() as db:
        rows = await repo.list_sessions(db, limit=limit)
        out: list[SessionListItem] = []
        for r in rows:
            n = await repo.count_messages(db, r.id)
            out.append(
                SessionListItem(
                    id=r.id,
                    title=r.title or "新对话",
                    persona_id=r.persona_id,
                    created_at=r.created_at,
                    updated_at=r.updated_at or r.created_at,
                    message_count=n,
                )
            )
    return out


@app.post("/api/sessions", response_model=SessionCreateResponse)
async def create_session(body: SessionCreateRequest | None = None):
    body = body or SessionCreateRequest()
    factory = get_session_factory()
    async with factory() as db:
        row = await repo.create_session(
            db, title=body.title, persona_id=body.persona_id
        )
        await db.commit()
    return SessionCreateResponse(
        id=row.id, title=row.title, created_at=row.created_at
    )


@app.patch("/api/sessions/{session_id}", response_model=SessionInfoResponse)
async def patch_session(session_id: str, body: SessionUpdateRequest):
    factory = get_session_factory()
    async with factory() as db:
        row = await repo.get_session(db, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        await repo.update_session_title(db, session_id, body.title)
        await db.commit()
        row = await repo.get_session(db, session_id)
        n = await repo.count_messages(db, session_id)
    summary = row.summary or ""
    return SessionInfoResponse(
        id=row.id,
        title=row.title,
        persona_id=row.persona_id,
        created_at=row.created_at,
        updated_at=row.updated_at or row.created_at,
        message_count=n,
        has_summary=bool(summary.strip()),
        summary_chars=len(summary),
    )


@app.get("/api/sessions/{session_id}", response_model=SessionInfoResponse)
async def get_session_info(session_id: str):
    factory = get_session_factory()
    async with factory() as db:
        row = await repo.get_session(db, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        n = await repo.count_messages(db, session_id)
    summary = row.summary or ""
    return SessionInfoResponse(
        id=row.id,
        title=row.title,
        persona_id=row.persona_id,
        created_at=row.created_at,
        updated_at=row.updated_at or row.created_at,
        message_count=n,
        has_summary=bool(summary.strip()),
        summary_chars=len(summary),
    )


@app.get("/api/sessions/{session_id}/messages", response_model=list[MessageItem])
async def get_messages(session_id: str):
    factory = get_session_factory()
    async with factory() as db:
        row = await repo.get_session(db, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        msgs = await repo.list_messages(db, session_id)
    return [
        MessageItem(
            id=m.id, role=m.role, content=m.content, created_at=m.created_at
        )
        for m in msgs
    ]


@app.delete("/api/sessions/{session_id}/messages", status_code=204)
async def clear_messages(session_id: str):
    factory = get_session_factory()
    async with factory() as db:
        row = await repo.get_session(db, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        await repo.clear_messages(db, session_id)
        await db.commit()
    return Response(status_code=204)


@app.delete("/api/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str):
    factory = get_session_factory()
    async with factory() as db:
        ok = await repo.delete_session(db, session_id)
        await db.commit()
    if not ok:
        raise HTTPException(status_code=404, detail="session not found")
    return Response(status_code=204)


@app.get("/api/memory", response_model=list[MemoryItem])
async def list_memories(limit: int = 20):
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be 1-100")
    factory = get_session_factory()
    async with factory() as db:
        rows = await list_memory_chunks(db, limit=limit)
    return [
        MemoryItem(
            id=r.id,
            content=r.content,
            source=r.source,
            session_id=r.session_id,
            created_at=r.created_at,
        )
        for r in rows
    ]


@app.delete("/api/memory/{chunk_id}", status_code=204)
async def remove_memory(chunk_id: int):
    factory = get_session_factory()
    async with factory() as db:
        ok = await delete_memory_chunk(db, chunk_id)
        await db.commit()
    if not ok:
        raise HTTPException(status_code=404, detail="memory not found")
    return Response(status_code=204)


@app.post("/api/memory/index-folder")
async def memory_index_folder(body: IndexFolderRequest):
    settings = get_settings()
    if not settings.openai_api_key.strip():
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY 未配置")
    factory = get_session_factory()
    async with factory() as db:
        result = await index_folder(
            db, settings, body.path, recursive=body.recursive
        )
        await db.commit()
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "index failed"))
    return result


@app.post("/api/sessions/{session_id}/chat", response_model=ChatResponse)
async def chat(session_id: str, body: ChatRequest, request: Request):
    settings = get_settings()
    if not settings.openai_api_key.strip():
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY 未配置，请在 .env 中设置",
        )
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    factory = get_session_factory()
    usage = UsageAccumulator()
    started = time.perf_counter()
    messages, _meta = await _build_messages_after_user_turn(
        factory, session_id, body, settings, usage
    )
    eff = effective_settings(settings, body)
    tools = tools_for_request(settings, body.use_tools)
    reply = await complete_with_tools(eff, messages, tools, usage)
    elapsed_ms = (time.perf_counter() - started) * 1000
    _, use_long_term = _memory_flags(body, settings)
    async with factory() as db:
        await repo.add_message(db, session_id, "assistant", reply)
        await after_assistant_reply(
            db,
            settings,
            session_id,
            body.message,
            reply,
            use_long_term_memory=use_long_term,
        )
        await db.commit()
    u = await _persist_usage(
        factory,
        request_id=request_id,
        session_id=session_id,
        model=eff.openai_model,
        usage=usage,
        elapsed_ms=elapsed_ms,
        ttft_ms=None,
    )
    return ChatResponse(reply=reply, usage=u)


async def _ndjson_line(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


@app.post("/api/sessions/{session_id}/chat/stream")
async def chat_stream(session_id: str, body: ChatRequest, request: Request):
    settings = get_settings()
    if not settings.openai_api_key.strip():
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY 未配置，请在 .env 中设置",
        )
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    factory = get_session_factory()
    usage = UsageAccumulator()
    ctx_meta = {}
    messages = []
    eff = effective_settings(settings, body)

    async def gen():
        nonlocal ctx_meta, messages, eff
        started = time.perf_counter()
        ttft_ms: float | None = None
        first_delta = True
        try:
            messages, ctx_meta = await _build_messages_after_user_turn(
                factory, session_id, body, settings, usage
            )
            eff = effective_settings(settings, body)
            tools = tools_for_request(settings, body.use_tools)
            _, use_long_term = _memory_flags(body, settings)

            yield await _ndjson_line(
                {
                    "type": "meta",
                    "request_id": request_id,
                    "model": eff.openai_model,
                    "persona_id": body.persona_id,
                    "use_tools": body.use_tools,
                    "use_session_summary": body.use_session_summary,
                    "use_long_term_memory": body.use_long_term_memory,
                }
            )
            if ctx_meta.get("summary_updated"):
                yield await _ndjson_line(
                    {
                        "type": "summary_updated",
                        "chars": ctx_meta.get("summary_chars", 0),
                    }
                )
            if ctx_meta.get("memories"):
                yield await _ndjson_line(
                    {"type": "memory_retrieved", "items": ctx_meta["memories"]}
                )

            parts: list[str] = []
            async for ev in stream_with_tools(eff, messages, tools, usage):
                if ev.get("type") == "delta" and ev.get("text"):
                    if first_delta:
                        ttft_ms = (time.perf_counter() - started) * 1000
                        first_delta = False
                    parts.append(ev["text"])
                yield await _ndjson_line(ev)

            full = "".join(parts)
            elapsed_ms = (time.perf_counter() - started) * 1000
            async with factory() as db:
                await repo.add_message(db, session_id, "assistant", full)
                await after_assistant_reply(
                    db,
                    settings,
                    session_id,
                    body.message,
                    full,
                    use_long_term_memory=use_long_term,
                )
                await db.commit()
            u = await _persist_usage(
                factory,
                request_id=request_id,
                session_id=session_id,
                model=eff.openai_model,
                usage=usage,
                elapsed_ms=elapsed_ms,
                ttft_ms=ttft_ms,
            )
            yield await _ndjson_line(
                {
                    "type": "done",
                    "elapsed_ms": round(elapsed_ms, 2),
                    "chars": len(full),
                    "usage": u,
                }
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("stream chat failed request_id=%s", request_id)
            yield await _ndjson_line({"type": "error", "message": str(e)})

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        headers={"X-Request-ID": request_id},
    )


@app.get("/")
async def index_page():
    index = Path(__file__).resolve().parent.parent / "static" / "index.html"
    if not index.is_file():
        return {"hint": "static/index.html 缺失"}
    return FileResponse(index)
