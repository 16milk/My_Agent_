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
