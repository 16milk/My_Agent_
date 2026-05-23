from datetime import datetime

from pydantic import BaseModel, Field


class SessionCreateResponse(BaseModel):
    id: str
    created_at: datetime


class SessionInfoResponse(BaseModel):
    id: str
    created_at: datetime
    message_count: int
    has_summary: bool = False
    summary_chars: int = 0


class MessageItem(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=32000)
    use_tools: bool = True
    use_session_summary: bool = True
    use_long_term_memory: bool = True


class MemoryItem(BaseModel):
    id: int
    content: str
    source: str
    session_id: str | None
    created_at: datetime


class ChatResponse(BaseModel):
    reply: str
