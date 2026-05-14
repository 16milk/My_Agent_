from datetime import datetime

from pydantic import BaseModel, Field


class SessionCreateResponse(BaseModel):
    id: str
    created_at: datetime


class SessionInfoResponse(BaseModel):
    id: str
    created_at: datetime
    message_count: int


class MessageItem(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=32000)
    use_tools: bool = True


class ChatResponse(BaseModel):
    reply: str
