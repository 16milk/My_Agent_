import json

from app.config import Settings
from app.llm import build_client


async def embed_text(settings: Settings, text: str) -> list[float]:
    client = build_client(settings)
    resp = await client.embeddings.create(
        model=settings.openai_embedding_model,
        input=text,
    )
    return list(resp.data[0].embedding)


def embedding_to_json(vec: list[float]) -> str:
    return json.dumps(vec)


def embedding_from_json(raw: str) -> list[float]:
    return json.loads(raw)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
