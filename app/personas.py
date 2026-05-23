import json
from functools import lru_cache
from pathlib import Path

from app.config import get_settings


@lru_cache
def _load_personas_raw() -> list[dict]:
    path = get_settings().project_root / "data" / "personas.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def list_personas() -> list[dict]:
    return [
        {
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "description": p.get("description", ""),
        }
        for p in _load_personas_raw()
        if p.get("id")
    ]


def get_persona(persona_id: str) -> dict | None:
    for p in _load_personas_raw():
        if p.get("id") == persona_id:
            return p
    return None


def resolve_persona_prompt(persona_id: str | None) -> str | None:
    if not persona_id:
        return None
    p = get_persona(persona_id)
    if not p:
        return None
    prompt = (p.get("prompt") or "").strip()
    return prompt or None
