from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from app.config import Settings


def build_client(settings: Settings) -> AsyncOpenAI:
    kwargs: dict = {"api_key": settings.openai_api_key or "missing"}
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return AsyncOpenAI(**kwargs)


def build_chat_messages(
    system_prompt: str, history: list[tuple[str, str]], user_message: str
) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for role, content in history:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return messages


async def stream_chat_completion(
    settings: Settings,
    messages: list[dict],
) -> AsyncIterator[str]:
    client = build_client(settings)
    stream = await client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        stream=True,
    )
    async for chunk in stream:
        choice = chunk.choices[0] if chunk.choices else None
        if choice and choice.delta and choice.delta.content:
            yield choice.delta.content


async def complete_chat(
    settings: Settings,
    messages: list[dict],
) -> str:
    client = build_client(settings)
    resp = await client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        stream=False,
    )
    return resp.choices[0].message.content or ""
