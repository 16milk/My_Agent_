from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection


def migrate_sqlite_schema(sync_conn: Connection) -> None:
    """为已有 SQLite 库补齐第三周新增列/表（create_all 不会 ALTER 旧表）。"""
    insp = inspect(sync_conn)
    if not insp.has_table("sessions"):
        return
    cols = {c["name"] for c in insp.get_columns("sessions")}
    if "summary" not in cols:
        sync_conn.execute(text("ALTER TABLE sessions ADD COLUMN summary TEXT"))
    if "summary_up_to_message_id" not in cols:
        sync_conn.execute(
            text("ALTER TABLE sessions ADD COLUMN summary_up_to_message_id INTEGER")
        )
