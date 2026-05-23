from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UsageAccumulator:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    embedding_tokens: int = 0

    def add_chat_usage(self, usage) -> None:
        if not usage:
            return
        self.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        self.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)

    def add_embedding_tokens(self, n: int) -> None:
        self.embedding_tokens += max(0, n)

    def to_dict(self, *, model: str, elapsed_ms: float, ttft_ms: float | None) -> dict:
        return {
            "model": model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "embedding_tokens": self.embedding_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens + self.embedding_tokens,
            "estimated_usd": round(estimate_cost_usd(model, self), 6),
            "elapsed_ms": round(elapsed_ms, 2),
            "ttft_ms": round(ttft_ms, 2) if ttft_ms is not None else None,
        }


# 粗略单价（USD / 1M tokens），仅用于个人可见估算
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
}
_EMBED_PRICE_PER_1M = 0.02


def estimate_cost_usd(model: str, usage: UsageAccumulator) -> float:
    key = model.lower()
    pin, pout = _MODEL_PRICING.get(key, (0.50, 1.50))
    chat = (usage.prompt_tokens * pin + usage.completion_tokens * pout) / 1_000_000
    emb = (usage.embedding_tokens * _EMBED_PRICE_PER_1M) / 1_000_000
    return chat + emb


def estimate_tokens_from_text(text: str) -> int:
    return max(1, len(text) // 4)
