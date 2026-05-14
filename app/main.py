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
from app.llm import build_chat_messages
from app.agent import complete_with_tools, stream_with_tools, tools_for_request
from app import repo
from app.schemas import (
    ChatRequest,
    ChatResponse,
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
    return SessionInfoResponse(id=row.id, created_at=row.created_at, message_count=n)


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


@app.post("/api/sessions/{session_id}/chat", response_model=ChatResponse)
async def chat(session_id: str, body: ChatRequest, request: Request):
    settings = get_settings()
    if not settings.openai_api_key.strip():
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY 未配置，请在 .env 中设置",
        )
    factory = get_session_factory()
    async with factory() as db:
        row = await repo.get_session(db, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        await repo.add_message(db, session_id, "user", body.message)
        await db.commit()
        msgs = await repo.list_messages(db, session_id)
        history: list[tuple[str, str]] = [
            (m.role, m.content) for m in msgs if m.role in ("user", "assistant")
        ]
        if history and history[-1][0] == "user":
            history = history[:-1]
        messages = build_chat_messages(
            settings.resolved_system_prompt(),
            history,
            body.message,
        )
    tools = tools_for_request(settings, body.use_tools)
    reply = await complete_with_tools(settings, messages, tools)
    async with factory() as db:
        await repo.add_message(db, session_id, "assistant", reply)
        await db.commit()
    return ChatResponse(reply=reply)


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

    async with factory() as db:
        row = await repo.get_session(db, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        await repo.add_message(db, session_id, "user", body.message)
        await db.commit()
        msgs = await repo.list_messages(db, session_id)
        history: list[tuple[str, str]] = [
            (m.role, m.content) for m in msgs if m.role in ("user", "assistant")
        ]
        if history and history[-1][0] == "user":
            history = history[:-1]
        messages = build_chat_messages(
            settings.resolved_system_prompt(),
            history,
            body.message,
        )

    tools = tools_for_request(settings, body.use_tools)
    started = time.perf_counter()

    async def gen():
        yield await _ndjson_line(
            {
                "type": "meta",
                "request_id": request_id,
                "model": settings.openai_model,
                "use_tools": body.use_tools,
            }
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
