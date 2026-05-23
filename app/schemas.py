from datetime import datetime

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    title: str | None = None
    persona_id: str | None = None


class SessionCreateResponse(BaseModel):
    id: str
    title: str | None
    created_at: datetime


class SessionListItem(BaseModel):
    id: str
    title: str | None
    persona_id: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int


class SessionUpdateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class SessionInfoResponse(BaseModel):
    id: str
    title: str | None
    persona_id: str | None
    created_at: datetime
    updated_at: datetime
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
    model: str | None = None
    persona_id: str | None = None


class MemoryItem(BaseModel):
    id: int
    content: str
    source: str
    session_id: str | None
    created_at: datetime


class IndexFolderRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=500)
    recursive: bool = True


class PersonaItem(BaseModel):
    id: str
    name: str
    description: str = ""


class ConfigResponse(BaseModel):
    default_model: str
    available_models: list[str]
    personas: list[PersonaItem]
    memory_enabled: bool
    auth_required: bool = False
    auth_header_name: str = "X-API-Key"
    scheduler_enabled: bool = True
    task_types: list[str] = Field(default_factory=list)
    features: dict[str, bool]


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class ApiKeyCreateResponse(BaseModel):
    id: int
    name: str
    key: str
    key_prefix: str
    created_at: datetime


class ApiKeyListItem(BaseModel):
    id: int
    name: str
    key_prefix: str
    enabled: bool
    created_at: datetime
    last_used_at: datetime | None


class ScheduledTaskCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    task_type: str = Field(..., min_length=1, max_length=64)
    cron: str = Field(..., min_length=9, max_length=120)
    payload: dict = Field(default_factory=dict)
    enabled: bool = True


class ScheduledTaskUpdate(BaseModel):
    name: str | None = None
    cron: str | None = None
    payload: dict | None = None
    enabled: bool | None = None


class ScheduledTaskItem(BaseModel):
    id: int
    name: str
    task_type: str
    cron: str
    payload: dict
    enabled: bool
    last_run_at: datetime | None
    last_status: str | None
    last_error: str | None
    created_at: datetime


class TaskRunItem(BaseModel):
    id: int
    task_id: int
    status: str
    result: dict | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class UsageStatsResponse(BaseModel):
    requests: int
    prompt_tokens: int
    completion_tokens: int
    embedding_tokens: int
    estimated_usd: float


class ChatResponse(BaseModel):
    reply: str
    usage: dict | None = None
