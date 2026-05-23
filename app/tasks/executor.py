"""定时任务执行器。"""

from __future__ import annotations

import json
import logging

from app.agent import complete_with_tools, tools_for_request
from app.config import get_settings
from app.db import get_session_factory
from app.memory.context import after_assistant_reply, prepare_chat_context
from app.memory.indexer import index_folder
from app.memory.summary import maybe_compress_session
from app import repo
from app.repo_tasks import (
    create_task_run,
    finish_task_run,
    get_task,
    mark_task_run,
)
from app.request_settings import effective_settings
from app.schemas import ChatRequest
from app.usage import UsageAccumulator

logger = logging.getLogger("my_agent")


async def execute_scheduled_task(task_id: int) -> dict:
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as db:
        task = await get_task(db, task_id)
        if task is None:
            return {"ok": False, "error": "task not found"}
        if not task.enabled:
            return {"ok": False, "error": "task disabled"}

        run = await create_task_run(db, task_id)
        await db.commit()
        run_id = run.id

    try:
        result = await _run_task_body(task_id, settings)
        async with factory() as db:
            await finish_task_run(db, run_id, status="ok", result=result)
            await mark_task_run(db, task_id, status="ok", error=None)
            await db.commit()
        return {"ok": True, **result}
    except Exception as e:
        logger.exception("scheduled task failed id=%s", task_id)
        async with factory() as db:
            await finish_task_run(db, run_id, status="error", error=str(e))
            await mark_task_run(db, task_id, status="error", error=str(e))
            await db.commit()
        return {"ok": False, "error": str(e)}


async def _run_task_body(task_id: int, settings) -> dict:
    factory = get_session_factory()
    async with factory() as db:
        task = await get_task(db, task_id)
        if task is None:
            raise RuntimeError("task not found")
        payload = json.loads(task.payload_json or "{}")
        ttype = task.task_type

        if ttype == "index_folder":
            path = payload.get("path", "docs")
            recursive = bool(payload.get("recursive", True))
            result = await index_folder(db, settings, path, recursive=recursive)
            await db.commit()
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "index failed"))
            return result

        if ttype == "summarize_sessions":
            limit = int(payload.get("limit", 20))
            rows = await repo.list_sessions(db, limit=limit)
            updated = 0
            for s in rows:
                sm = await maybe_compress_session(db, settings, s)
                if sm.get("updated"):
                    updated += 1
            await db.commit()
            return {"sessions_checked": len(rows), "summaries_updated": updated}

        if ttype == "agent_prompt":
            message = (payload.get("message") or "").strip()
            if not message:
                raise RuntimeError("payload.message 不能为空")
            if not settings.openai_api_key.strip():
                raise RuntimeError("OPENAI_API_KEY 未配置")

            session_id = payload.get("session_id")
            if session_id:
                if await repo.get_session(db, session_id) is None:
                    raise RuntimeError(f"session not found: {session_id}")
            else:
                row = await repo.create_session(
                    db,
                    title=payload.get("title") or "定时任务",
                    persona_id=payload.get("persona_id"),
                )
                session_id = row.id

            body = ChatRequest(
                message=message,
                use_tools=bool(payload.get("use_tools", False)),
                use_session_summary=bool(payload.get("use_session_summary", True)),
                use_long_term_memory=bool(payload.get("use_long_term_memory", True)),
                model=payload.get("model"),
                persona_id=payload.get("persona_id"),
            )
            usage = UsageAccumulator()
            await repo.add_message(db, session_id, "user", message)
            await db.flush()
            row = await repo.get_session(db, session_id)
            if row is None:
                raise RuntimeError("session missing after create")
            eff = effective_settings(settings, body)
            messages, _ = await prepare_chat_context(
                db,
                eff,
                row,
                message,
                use_session_summary=body.use_session_summary,
                use_long_term_memory=body.use_long_term_memory,
                usage=usage,
            )
            await db.commit()

            tools = tools_for_request(settings, body.use_tools)
            reply = await complete_with_tools(eff, messages, tools, usage)

            async with factory() as db2:
                await repo.add_message(db2, session_id, "assistant", reply)
                await after_assistant_reply(
                    db2,
                    settings,
                    session_id,
                    message,
                    reply,
                    use_long_term_memory=body.use_long_term_memory,
                )
                await db2.commit()

            return {
                "session_id": session_id,
                "reply_chars": len(reply),
                "reply_preview": reply[:200],
            }

        raise RuntimeError(f"unknown task_type: {ttype}")
