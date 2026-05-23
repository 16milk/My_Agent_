# My_Agent_ 开发记录

本文档按阶段记录实现内容、决策与回顾要点，便于后期查阅。更新时请加上日期与简短标题。

---

## 2026-05-23 — 第五周：鉴权 / API Key 与定时任务

### 目标（周计划对照）

- **API Key 鉴权**：`AUTH_ENABLED=true` 时校验 `X-API-Key` 或 `Authorization: Bearer`；公开路径仅 `/`、`/health`、`/api/config`。
- **密钥管理**：环境变量 `API_KEYS` + 数据库表 `api_keys`（SHA-256 存储，创建时仅返回一次明文）。
- **定时任务**：`scheduled_tasks` + `task_runs`；APScheduler + cron 五段式；支持手动触发。
- **任务类型**：`index_folder`、`agent_prompt`、`summarize_sessions`。

### 已实现内容

| 模块 | 说明 |
|------|------|
| `app/auth.py` | 鉴权中间件、密钥生成与哈希 |
| `app/repo_auth.py` | API Key CRUD |
| `app/repo_tasks.py` | 定时任务与运行记录 |
| `app/tasks/scheduler.py` | 启动时加载 cron，变更后 `reload` |
| `app/tasks/executor.py` | 三类任务执行逻辑 |
| `app/main.py` | `/api/keys*`、`/api/tasks*` |
| `static/index.html` | API Key 输入、定时任务面板 |
| `requirements.txt` | `apscheduler`、`croniter` |

### API 摘要

- `GET/POST /api/keys`，`DELETE /api/keys/{id}`，`PATCH /api/keys/{id}/enable?enabled=`
- `GET/POST /api/tasks`，`PATCH/DELETE /api/tasks/{id}`，`POST /api/tasks/{id}/run`，`GET /api/tasks/{id}/runs`

### 配置示例

```env
AUTH_ENABLED=true
API_KEYS=ma_your_bootstrap_key_here
SCHEDULER_ENABLED=true
SCHEDULER_TIMEZONE=Asia/Shanghai
```

### 本地验证

1. 开启鉴权后，无 Key 调用 `/api/sessions` 应 401；带 Key 正常。
2. `POST /api/tasks` 创建 `0 8 * * *` 的 `index_folder` 任务，手动 `POST .../run` 应写入 `task_runs`。
3. 前端保存 Key 后可正常聊天。

### 已知限制

- 定时任务在单进程内调度，多实例部署需外部分布式调度。
- `agent_prompt` 定时任务默认关闭工具调用，避免无人值守时误操作文件/网络。

---

## 2026-05-14 — 第四周：产品化与体验

### 目标（周计划对照）

- **多会话工作台**：列表、标题、切换、删除；首条用户消息自动生成标题。
- **模型 / 人格**：前端下拉；`ChatRequest.model` / `persona_id`；人格定义 `data/personas.json`。
- **用量透明**：`usage_logs` 表；流式 `done.usage`（token、估算 USD、TTFT）；`GET /api/usage/stats`。
- **知识库入库**：`POST /api/memory/index-folder` 批量索引允许目录下的 `.md/.txt`。
- **记忆管理 UI**：侧栏查看 / 删除记忆条目。
- **交付**：`Makefile`、`README.md`、`Dockerfile`、`docs/comparison.md`。

### 已实现内容

| 模块 | 说明 |
|------|------|
| `app/models.py` | `sessions.title/updated_at/persona_id`；`usage_logs` |
| `app/personas.py` | 加载人格模板 |
| `app/usage.py` | `UsageAccumulator`、费用粗算 |
| `app/request_settings.py` | 按请求覆盖模型与 system prompt |
| `app/memory/indexer.py` | 目录切片 + embedding 入库 |
| `app/main.py` | `/api/config`、`/api/sessions` 列表、`PATCH` 标题、`/api/usage/stats` |
| `static/index.html` | 侧栏会话、模型/人格、记忆面板、用量展示 |
| `Makefile` / `README.md` / `Dockerfile` | 一键开发与容器 |

### 本地验证

1. `make dev` 后侧边栏应出现会话列表，可新建/切换。
2. 切换模型或「编程搭档」人格后提问，行为应有差异。
3. `docs` 目录点「索引目录」，再问文档相关问题，应 `memory_retrieved`。
4. 发送消息后完成行应显示 token 与估算 `$`。

### 已知限制

- 费用为静态单价表粗算，非账单级精度。
- 会话列表暂不支持服务端分页搜索。

---

## 2026-05-14 — 第三周：会话摘要 + 长期向量记忆

### 目标（周计划对照）

- **会话摘要**：消息数超过 `MEMORY_SUMMARIZE_OVER_MESSAGES` 时，将较早轮次合并为 rolling summary，写入 `sessions.summary`；上下文仅保留摘要 + 最近 `MEMORY_KEEP_RECENT_MESSAGES` 条对话。
- **长期记忆**：每轮问答结束后将「用户 + 助手」写入 `memory_chunks`，用 OpenAI Embedding 向量化；新问题时按余弦相似度检索 Top-K，注入系统提示。
- **可开关**：请求体 `use_session_summary` / `use_long_term_memory`；总开关 `MEMORY_ENABLED=false` 时二者均关闭。
- **管理 API**：`GET /api/memory`、`DELETE /api/memory/{id}`；清空会话消息时同时清空本会话摘要字段。

### 已实现内容

| 模块 | 说明 |
|------|------|
| `app/models.py` | `sessions.summary`、`summary_up_to_message_id`；表 `memory_chunks` |
| `app/migrate.py` | SQLite 旧库 `ALTER TABLE` 补列 |
| `app/memory/embeddings.py` | Embedding API、余弦相似度 |
| `app/memory/vector.py` | 写入分片、检索、列表、删除 |
| `app/memory/summary.py` | `maybe_compress_session` 调用模型生成摘要 |
| `app/memory/context.py` | `prepare_chat_context`、`after_assistant_reply` |
| `app/main.py` | 对话路径接入记忆；流式事件 `summary_updated`、`memory_retrieved` |
| `app/schemas.py` | 记忆相关请求字段与 `MemoryItem` |
| `static/index.html` | 「会话摘要」「长期记忆」勾选框 |

### NDJSON 补充事件

- `summary_updated`：`chars`（摘要长度）。
- `memory_retrieved`：`items`（`id`、`score`、`content` 预览等）。

### 配置（`.env.example`）

- `OPENAI_EMBEDDING_MODEL`（默认 `text-embedding-3-small`）
- `MEMORY_*` 系列：开关、保留条数、触发摘要阈值、检索 Top-K、最低相似度等。

### 决策与备注

- **向量存 SQLite JSON**：个人规模够用；检索在内存中对最近 `MEMORY_SEARCH_POOL` 条做相似度排序，避免引入 Chroma 等依赖。
- **摘要与原文**：旧消息仍留在 `messages` 表便于审计，只是不再送入模型上下文。
- **长期记忆全局**：不按会话隔离检索，便于跨会话回忆；`session_id` 仅作来源标记。

### 本地验证

1. 多聊若干轮（>24 条消息）后应出现 `summary_updated`。
2. 关闭再开启「长期记忆」，问「我们之前聊过什么」类问题，应出现 `memory_retrieved`。
3. `GET /api/memory` 可查看已索引条目。

### 已知限制

- 未做 embedding 缓存或增量索引队列；高并发下 embedding 成本需自行控制。
- 未实现按目录批量导入个人知识库（可第四周做 `index_folder` 工具或脚本）。

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
