import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from app.config import get_settings
from app.db import Base, get_engine, get_session_factory
from app.migrate import migrate_sqlite_schema
from app.agent import complete_with_tools, stream_with_tools, tools_for_request
from app.memory.context import after_assistant_reply, prepare_chat_context
from app.memory.vector import delete_memory_chunk, list_memory_chunks
from app import repo
from app.schemas import (
    ChatRequest,
    ChatResponse,
    MemoryItem,
    MessageItem,
    SessionCreateResponse,
    SessionInfoResponse,
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
    yield
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
    factory, session_id: str, body: ChatRequest, settings
) -> tuple[list[dict], dict]:
    use_summary, use_long_term = _memory_flags(body, settings)
    async with factory() as db:
        row = await repo.get_session(db, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        await repo.add_message(db, session_id, "user", body.message)
        await db.flush()
        messages, meta = await prepare_chat_context(
            db,
            settings,
            row,
            body.message,
            use_session_summary=use_summary,
            use_long_term_memory=use_long_term,
        )
        await db.commit()
    return messages, meta


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/sessions", response_model=SessionCreateResponse)
async def create_session():
    factory = get_session_factory()
    async with factory() as db:
        row = await repo.create_session(db)
        await db.commit()
    return SessionCreateResponse(id=row.id, created_at=row.created_at)


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
        created_at=row.created_at,
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


@app.post("/api/sessions/{session_id}/chat", response_model=ChatResponse)
async def chat(session_id: str, body: ChatRequest, request: Request):
    settings = get_settings()
    if not settings.openai_api_key.strip():
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY 未配置，请在 .env 中设置",
        )
    factory = get_session_factory()
    messages, _meta = await _build_messages_after_user_turn(
        factory, session_id, body, settings
    )
    tools = tools_for_request(settings, body.use_tools)
    reply = await complete_with_tools(settings, messages, tools)
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
    return ChatResponse(reply=reply)


async def _ndjson_line(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def _yield_context_events(meta: dict):
    if meta.get("summary_updated"):
        return {
            "type": "summary_updated",
            "chars": meta.get("summary_chars", 0),
        }
    return None


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
    messages, ctx_meta = await _build_messages_after_user_turn(
        factory, session_id, body, settings
    )
    tools = tools_for_request(settings, body.use_tools)
    _, use_long_term = _memory_flags(body, settings)
    started = time.perf_counter()

    async def gen():
        yield await _ndjson_line(
            {
                "type": "meta",
                "request_id": request_id,
                "model": settings.openai_model,
                "use_tools": body.use_tools,
                "use_session_summary": body.use_session_summary,
                "use_long_term_memory": body.use_long_term_memory,
            }
        )
        ev = _yield_context_events(ctx_meta)
        if ev:
            yield await _ndjson_line(ev)
        if ctx_meta.get("memories"):
            yield await _ndjson_line(
                {"type": "memory_retrieved", "items": ctx_meta["memories"]}
            )
        parts: list[str] = []
        try:
            async for ev in stream_with_tools(settings, messages, tools):
                yield await _ndjson_line(ev)
                if ev.get("type") == "delta" and ev.get("text"):
                    parts.append(ev["text"])
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
            yield await _ndjson_line(
                {
                    "type": "done",
                    "elapsed_ms": round(elapsed_ms, 2),
                    "chars": len(full),
                }
            )
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
