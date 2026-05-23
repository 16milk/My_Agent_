from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection


def migrate_sqlite_schema(sync_conn: Connection) -> None:
    """为已有 SQLite 库补齐新增列/表（create_all 不会 ALTER 旧表）。"""
    insp = inspect(sync_conn)
    if insp.has_table("sessions"):
        cols = {c["name"] for c in insp.get_columns("sessions")}
        if "summary" not in cols:
            sync_conn.execute(text("ALTER TABLE sessions ADD COLUMN summary TEXT"))
        if "summary_up_to_message_id" not in cols:
            sync_conn.execute(
                text(
                    "ALTER TABLE sessions ADD COLUMN summary_up_to_message_id INTEGER"
                )
            )
        if "title" not in cols:
            sync_conn.execute(text("ALTER TABLE sessions ADD COLUMN title TEXT"))
        if "persona_id" not in cols:
            sync_conn.execute(
                text("ALTER TABLE sessions ADD COLUMN persona_id TEXT")
            )
        if "updated_at" not in cols:
            sync_conn.execute(
                text("ALTER TABLE sessions ADD COLUMN updated_at DATETIME")
            )
            sync_conn.execute(
                text(
                    "UPDATE sessions SET updated_at = created_at "
                    "WHERE updated_at IS NULL"
                )
            )
        sync_conn.execute(
            text("UPDATE sessions SET title = '新对话' WHERE title IS NULL")
        )
