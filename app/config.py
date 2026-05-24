from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o-mini"
    system_prompt: str = ""

    database_path: str = "data/app.db"

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # 第二周：工具（路径白名单、HTTP 白名单）
    # 多个根目录用英文逗号分隔；留空表示仅允许项目根目录下文件
    tool_file_roots: str = ""
    # 允许发起 http_get 的主机名（小写），逗号分隔；含 * 表示任意主机（慎用）
    tool_http_allowed_hosts: str = ""
    tool_http_timeout_sec: float = 15.0
    tool_http_max_bytes: int = 500_000
    tool_max_file_bytes: int = 200_000
    agent_max_tool_rounds: int = 8

    # 第三周：会话摘要 + 长期向量记忆
    openai_embedding_model: str = "text-embedding-3-small"
    memory_enabled: bool = True
    memory_session_summary: bool = True
    memory_long_term: bool = True
    memory_keep_recent_messages: int = 12
    memory_summarize_over_messages: int = 24
    memory_retrieve_top_k: int = 5
    memory_min_score: float = 0.35
    memory_index_max_chars: int = 4000
    memory_search_pool: int = 500
    memory_index_extensions: str = ".md,.txt,.markdown"
    memory_index_chunk_chars: int = 1500

    # 向量存储：lance（默认）| sqlite（旧版 brute-force）
    memory_backend: str = "lance"
    lance_db_path: str = "data/lance"
    openai_embedding_dimensions: int = 1536
    memory_lance_build_index: bool = True
    memory_lance_index_min_rows: int = 256

    # 第四周：产品化
    available_models: str = "gpt-4o-mini,gpt-4o,gpt-4.1-mini"

    # 第五周：鉴权与定时任务
    auth_enabled: bool = False
    api_keys: str = ""
    auth_header_name: str = "X-API-Key"
    scheduler_enabled: bool = True
    scheduler_timezone: str = "Asia/Shanghai"

    def resolved_env_api_keys(self) -> list[str]:
        raw = self.api_keys.strip()
        if not raw:
            return []
        return [k.strip() for k in raw.split(",") if k.strip()]

    @property
    def resolved_available_models(self) -> list[str]:
        raw = self.available_models.strip()
        if not raw:
            return [self.openai_model]
        models = [m.strip() for m in raw.split(",") if m.strip()]
        if self.openai_model not in models:
            models.insert(0, self.openai_model)
        return models

    def resolved_index_extensions(self) -> set[str]:
        raw = self.memory_index_extensions.strip()
        if not raw:
            return {".md", ".txt"}
        return {e if e.startswith(".") else f".{e}" for e in raw.split(",") if e.strip()}

    @property
    def database_url(self) -> str:
        path = Path(self.database_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{path.as_posix()}"

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    def resolved_tool_file_roots(self) -> list[Path]:
        raw = self.tool_file_roots.strip()
        base = self.project_root
        if not raw:
            return [base.resolve()]
        roots: list[Path] = []
        for part in raw.split(","):
            p = Path(part.strip())
            if not part.strip():
                continue
            roots.append((base / p).resolve() if not p.is_absolute() else p.resolve())
        return roots or [base.resolve()]

    def resolved_http_allowed_hosts(self) -> list[str]:
        raw = self.tool_http_allowed_hosts.strip()
        if not raw:
            return []
        return [h.strip().lower() for h in raw.split(",") if h.strip()]

    def resolved_system_prompt(self) -> str:
        if self.system_prompt.strip():
            return self.system_prompt.strip()
        return (
            "你是一个面向单用户的个人助手，回答简洁、可执行；"
            "不确定时说明假设并给出验证方式。"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
