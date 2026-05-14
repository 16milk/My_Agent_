# My_Agent_ 开发记录

本文档按阶段记录实现内容、决策与回顾要点，便于后期查阅。更新时请加上日期与简短标题。

---

## 2026-05-14 — 第二周：工具调用与安全边界

### 目标（周计划对照）

- 统一 **OpenAI tools** 形态：`read_file`（必选）、`http_get`（仅当配置 HTTP 白名单后注册）。
- **安全**：读文件路径必须在允许根目录内（默认项目根）；禁止 `..` 与绝对路径；文件与 HTTP 响应大小上限；HTTP 超时与连接限制。
- **多轮工具循环**：模型返回 `tool_calls` 时执行工具、将 `tool` 消息写回上下文，直到产出自然语言或达到 `AGENT_MAX_TOOL_ROUNDS`。
- **流式体验**：工具轮次内部用非流式 `chat.completions`；最终答复在无 `tool_calls` 时优先复用已有 `content` 分块推送，否则 `stream + tool_choice=none` 真流式输出。
- 请求体 `use_tools` 可关闭工具，回退第一周纯对话路径。

### 已实现内容

| 模块 | 说明 |
|------|------|
| `requirements.txt` | 增加 `httpx` |
| `app/config.py` | `TOOL_FILE_ROOTS`、`TOOL_HTTP_ALLOWED_HOSTS`、超时与字节上限、`AGENT_MAX_TOOL_ROUNDS` |
| `app/tools_schema.py` | 根据配置生成 `tools` JSON schema |
| `app/tools_exec.py` | `read_file` / `http_get` 执行与结构化 JSON 结果 |
| `app/agent.py` | `complete_with_tools`、`stream_with_tools`、`tools_for_request` |
| `app/schemas.py` | `ChatRequest.use_tools` |
| `app/main.py` | `/chat` 与 `/chat/stream` 接入 agent；NDJSON 增加 `tool_call`、`tool_result`、`meta.use_tools` |
| `static/index.html` | 「启用工具」勾选框；展示工具调用与结果摘要 |
| `.env.example` | 第二周相关环境变量说明 |

### NDJSON 流式补充事件

- `tool_call`：`id`、`name`、`arguments`（原始 JSON 字符串）。
- `tool_result`：`id`、`name`、`content`（工具返回 JSON 字符串，前端截断展示）。

### 决策与备注

- **历史库不存 tool 轨迹**：数据库仍只存 user/assistant 文本，多轮工具上下文仅在一次请求内有效；跨请求「可审计工具链」留待后续（如单独审计表或 JSON 消息列）。
- **http_get 默认不注册**：`TOOL_HTTP_ALLOWED_HOSTS` 为空时不把该工具交给模型，减少误用；需要时显式配置主机或 `*`（任意主机，慎用）。

### API 演进说明

- `POST /api/sessions/{id}/chat` 与 `chat/stream` 的请求体现在支持 `use_tools`（默认 `true`）；关闭时等价于第一周纯对话。

### 本地验证（第二周）

- 勾选「启用工具」后提问「请读取 docs/development-log.md 前几段」应出现 `tool_call` / `tool_result` 后再输出总结。

### 已知限制

- 未做工具并发度全局限流（仅 httpx 客户端连接限制）。
- 未对模型侧 `tool_calls` 做 JSON Schema 二次校验（依赖模型输出）。

---

## 2026-05-13 — 第一周：骨架、会话与流式对话（第一部分）

### 目标（周计划对照）

- 仓库与应用目录结构、依赖与配置（`.env` / `.env.example`）。
- 最小对话 HTTP API，支持**流式输出**（NDJSON，便于 `fetch` + `ReadableStream` 消费）。
- **会话 id**、消息持久化（SQLite + SQLAlchemy asyncio）、清空本会话历史。
- 基础可观测：`X-Request-ID`、请求耗时日志（middleware）、流式结束事件中的 `elapsed_ms`。
- 里程碑：本地一条命令启动后，浏览器内多轮对话可用。

### 已实现内容

| 模块 | 说明 |
|------|------|
| `requirements.txt` | FastAPI、uvicorn、pydantic-settings、openai、SQLAlchemy async + aiosqlite |
| `app/config.py` | `OPENAI_*`、`DATABASE_PATH`、`SYSTEM_PROMPT`、`LOG_LEVEL` 等 |
| `app/db.py` | 异步引擎、会话工厂；SQLite 连接启用 `PRAGMA foreign_keys=ON` |
| `app/models.py` | `sessions`、`messages` 表 ORM |
| `app/repo.py` | 创建会话、查询、追加消息、清空消息、删除会话 |
| `app/llm.py` | OpenAI 兼容客户端；非流式与流式 `chat.completions` |
| `app/schemas.py` | Pydantic 请求/响应模型 |
| `app/main.py` | `/health`、`/api/sessions*`、`/api/.../chat`、`/api/.../chat/stream`、`/` 静态页 |
| `static/index.html` | 极简 Web UI：新建会话、清空历史、流式展示、本地保存 `session_id` |

### API 摘要

- `POST /api/sessions` → 创建会话，返回 `id`。
- `GET /api/sessions/{id}/messages` → 拉取历史（用于刷新页面恢复上下文）。
- `DELETE /api/sessions/{id}/messages` → 清空该会话下消息。
- `DELETE /api/sessions/{id}` → 删除会话（级联删消息）。
- `POST /api/sessions/{id}/chat` → 非流式整段回复（便于脚本调试）。
- `POST /api/sessions/{id}/chat/stream` → **NDJSON** 流：`meta` → 多条 `delta` → `done` 或 `error`。

### 本地运行

```bash
cd /Users/ykxcai/Desktop/My_Agent_
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY（可选 OPENAI_BASE_URL / OPENAI_MODEL）
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 本地验证（开发机）

- `GET /health` → `{"status":"ok"}`
- `POST /api/sessions` → 返回 `id` 与 `created_at`

### 决策与备注

- **NDJSON 而非 EventSource**：EventSource 仅支持 GET，长用户输入用 POST + 流式响应更合适。
- **流式路径上的 DB**：先提交用户消息再拉模型流，避免长时间占用数据库连接；助手完整回复在流结束后写入。
- **周后续迭代预留**：工具调用、记忆压缩、鉴权等未纳入本周范围，避免范围蔓延。

### 已知限制（留待后续周）

- 无用户登录，会话 id 知道即可访问（个人本地使用可接受）。
- 无 token 计费统计与速率限制。
- 错误信息直接透传，生产环境需脱敏与错误码体系。

---

（后续每次开发完成后，在上方追加新章节，或按周拆分 `docs/week-XX.md` 并在本文件建立索引。）
