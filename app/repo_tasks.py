import json
from datetime import datetime, timezone

from sqlalchemy import delete, select, update

from app.models import ScheduledTaskModel, TaskRunModel

TASK_TYPES = frozenset({"index_folder", "agent_prompt", "summarize_sessions"})


async def create_task(
    db,
    *,
    name: str,
    task_type: str,
    cron: str,
    payload: dict,
    enabled: bool = True,
) -> ScheduledTaskModel:
    if task_type not in TASK_TYPES:
        raise ValueError(f"unsupported task_type: {task_type}")
    row = ScheduledTaskModel(
        name=name,
        task_type=task_type,
        cron=cron.strip(),
        payload_json=json.dumps(payload, ensure_ascii=False),
        enabled=enabled,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def get_task(db, task_id: int) -> ScheduledTaskModel | None:
    return await db.get(ScheduledTaskModel, task_id)


async def list_tasks(db) -> list[ScheduledTaskModel]:
    stmt = select(ScheduledTaskModel).order_by(ScheduledTaskModel.id.desc())
    return list((await db.execute(stmt)).scalars().all())


async def update_task(db, task_id: int, **fields) -> ScheduledTaskModel | None:
    row = await get_task(db, task_id)
    if row is None:
        return None
    for k, v in fields.items():
        if v is None:
            continue
        if k == "payload" and isinstance(v, dict):
            row.payload_json = json.dumps(v, ensure_ascii=False)
        elif hasattr(row, k):
            setattr(row, k, v)
    await db.flush()
    await db.refresh(row)
    return row


async def delete_task(db, task_id: int) -> bool:
    row = await get_task(db, task_id)
    if row is None:
        return False
    await db.delete(row)
    return True


async def mark_task_run(
    db,
    task_id: int,
    *,
    status: str,
    error: str | None = None,
) -> None:
    await db.execute(
        update(ScheduledTaskModel)
        .where(ScheduledTaskModel.id == task_id)
        .values(
            last_run_at=datetime.now(timezone.utc),
            last_status=status,
            last_error=error,
        )
    )


async def create_task_run(db, task_id: int) -> TaskRunModel:
    row = TaskRunModel(task_id=task_id, status="running")
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def finish_task_run(
    db,
    run_id: int,
    *,
    status: str,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    row = await db.get(TaskRunModel, run_id)
    if row is None:
        return
    row.status = status
    row.finished_at = datetime.now(timezone.utc)
    row.error = error
    if result is not None:
        row.result_json = json.dumps(result, ensure_ascii=False)
    await db.flush()


async def list_task_runs(db, task_id: int, limit: int = 20) -> list[TaskRunModel]:
    stmt = (
        select(TaskRunModel)
        .where(TaskRunModel.task_id == task_id)
        .order_by(TaskRunModel.id.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def delete_task_runs(db, task_id: int) -> None:
    await db.execute(delete(TaskRunModel).where(TaskRunModel.task_id == task_id))
