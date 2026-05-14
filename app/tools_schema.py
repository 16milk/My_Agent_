from app.config import Settings


def openai_tools_for_settings(settings: Settings) -> list[dict]:
    tools: list[dict] = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "读取已允许目录内的文本文件（UTF-8，超出长度会截断）。"
                    "path 为相对项目允许根目录的路径，例如 docs/development-log.md"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "相对路径，禁止包含 .. 作目录穿越",
                        },
                    },
                    "required": ["path"],
                },
            },
        }
    ]
    hosts = settings.resolved_http_allowed_hosts()
    if hosts:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "http_get",
                    "description": (
                        "对允许主机发起 HTTPS GET（本地调试可用 HTTP）。"
                        "响应体过大时会截断；仅用于拉取公开只读资源。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "完整 URL，例如 https://example.com/path",
                            },
                        },
                        "required": ["url"],
                    },
                },
            }
        )
    return tools
