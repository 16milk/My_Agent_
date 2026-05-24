from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class MemoryChunkView:
    id: int
    content: str
    source: str
    session_id: str | None
    created_at: datetime
