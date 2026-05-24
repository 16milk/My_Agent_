# My_Agent_

面向单用户的本地优先个人 Agent：流式对话、工具调用、会话摘要、**LanceDB** 长期向量记忆。

## 快速开始

```bash
make install
# 编辑 .env，填入 OPENAI_API_KEY
make dev
```

浏览器打开 http://127.0.0.1:8000/

## 功能概览

| 能力 | 说明 |
|------|------|
| 多会话 | 侧边栏列表、标题、切换与删除 |
| 模型 / 人格 | 请求级切换模型；`data/personas.json` 定义人格 |
| 工具 | `read_file`、`http_get`（需白名单） |
| 记忆 | 会话摘要 + **LanceDB** 向量检索（可回退 sqlite）；支持目录批量索引 |
| 用量 | Token 与估算费用写入 `usage_logs`，界面展示 |
| 鉴权 | 可选 `AUTH_ENABLED` + `API_KEYS` / 数据库密钥 |
| 定时任务 | Cron 调度：索引目录、自动摘要、定时提问 |

## 文档

- 开发记录：`docs/development-log.md`
- 与主流产品对比：`docs/comparison.md`
- **向量库原理与选型**：`docs/vector-databases.md`

## Docker（可选）

```bash
docker build -t my-agent .
docker run --rm -p 8000:8000 --env-file .env -v "$(pwd)/data:/app/data" my-agent
```
