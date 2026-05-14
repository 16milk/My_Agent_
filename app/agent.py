import json
import logging
from collections.abc import AsyncIterator

from app.config import Settings
from app.llm import build_client, complete_chat, stream_chat_completion
from app.tools_exec import dispatch_tool
from app.tools_schema import openai_tools_for_settings

logger = logging.getLogger("my_agent")


def assistant_message_dict(msg) -> dict:
    d: dict = {"role": "assistant", "content": msg.content}
    if msg.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "",
                },
            }
            for tc in msg.tool_calls
        ]
    return d


async def _stream_final_answer(
    settings: Settings,
    messages: list[dict],
    tools: list[dict],
) -> AsyncIterator[str]:
    client = build_client(settings)
    kwargs: dict = {
        "model": settings.openai_model,
        "messages": messages,
        "stream": True,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "none"
    stream = await client.chat.completions.create(**kwargs)
    async for chunk in stream:
        choice = chunk.choices[0] if chunk.choices else None
        if choice and choice.delta and choice.delta.content:
            yield choice.delta.content


async def complete_with_tools(
    settings: Settings, messages: list[dict], tools: list[dict]
) -> str:
    if not tools:
        return await complete_chat(settings, messages)
    client = build_client(settings)
    rounds = 0
    while rounds < settings.agent_max_tool_rounds:
        rounds += 1
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            stream=False,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            messages.append(assistant_message_dict(msg))
            for tc in msg.tool_calls:
                name = tc.function.name
                raw = tc.function.arguments or ""
                try:
                    out = await dispatch_tool(name, raw, settings)
                except Exception as e:
                    logger.exception("tool dispatch failed name=%s", name)
                    out = json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": out}
                )
            continue
        text = (msg.content or "").strip()
        return text if text else "(模型未返回文本)"
    return "(达到工具轮次上限，已终止)"


async def stream_with_tools(
    settings: Settings, messages: list[dict], tools: list[dict]
) -> AsyncIterator[dict]:
    if not tools:
        async for delta in stream_chat_completion(settings, messages):
            yield {"type": "delta", "text": delta}
        return

    client = build_client(settings)
    rounds = 0
    while rounds < settings.agent_max_tool_rounds:
        rounds += 1
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            stream=False,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            messages.append(assistant_message_dict(msg))
            for tc in msg.tool_calls:
                name = tc.function.name
                raw = tc.function.arguments or ""
                yield {
                    "type": "tool_call",
                    "id": tc.id,
                    "name": name,
                    "arguments": raw,
                }
                try:
                    out = await dispatch_tool(name, raw, settings)
                except Exception as e:
                    logger.exception("tool dispatch failed name=%s", name)
                    out = json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
                yield {
                    "type": "tool_result",
                    "id": tc.id,
                    "name": name,
                    "content": out,
                }
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": out}
                )
            continue

        text = msg.content or ""
        if text.strip():
            step = 64
            for i in range(0, len(text), step):
                yield {"type": "delta", "text": text[i : i + step]}
            return

        async for delta in _stream_final_answer(settings, messages, tools):
            yield {"type": "delta", "text": delta}
        return

    yield {"type": "error", "message": "达到工具轮次上限，已终止"}


def tools_for_request(settings: Settings, use_tools: bool) -> list[dict]:
    if not use_tools:
        return []
    return openai_tools_for_settings(settings)
