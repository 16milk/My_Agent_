"""APScheduler 集成：从数据库加载 cron 任务。"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from croniter import croniter

from app.config import get_settings
from app.db import get_session_factory
from app.repo_tasks import list_tasks
from app.tasks.executor import execute_scheduled_task

logger = logging.getLogger("my_agent")

_scheduler: AsyncIOScheduler | None = None


def validate_cron(expr: str) -> None:
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError("cron 须为 5 段：分 时 日 月 周，例如 0 8 * * *")
    if not croniter.is_valid(expr):
        raise ValueError("无效的 cron 表达式")


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler


async def reload_scheduler_jobs() -> None:
    global _scheduler
    if _scheduler is None:
        return
    settings = get_settings()
    _scheduler.remove_all_jobs()
    if not settings.scheduler_enabled:
        return

    factory = get_session_factory()
    async with factory() as db:
        tasks = await list_tasks(db)

    tz = settings.scheduler_timezone
    for t in tasks:
        if not t.enabled:
            continue
        try:
            validate_cron(t.cron)
            trigger = CronTrigger.from_crontab(t.cron, timezone=tz)

            async def _job(tid: int = t.id) -> None:
                await execute_scheduled_task(tid)

            _scheduler.add_job(
                _job,
                trigger=trigger,
                id=f"task_{t.id}",
                replace_existing=True,
                name=t.name,
            )
            logger.info("scheduled task #%s %s cron=%s", t.id, t.name, t.cron)
        except Exception:
            logger.exception("skip invalid task #%s", t.id)


def start_scheduler() -> AsyncIOScheduler | None:
    global _scheduler
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("scheduler disabled")
        return None
    if _scheduler is not None:
        return _scheduler
    _scheduler = AsyncIOScheduler(timezone=settings.scheduler_timezone)
    _scheduler.start()
    logger.info("scheduler started tz=%s", settings.scheduler_timezone)
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("scheduler stopped")
