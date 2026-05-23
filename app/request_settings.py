from app.config import Settings
from app.personas import resolve_persona_prompt
from app.schemas import ChatRequest


def effective_settings(settings: Settings, body: ChatRequest) -> Settings:
    overrides: dict = {}
    if body.model and body.model.strip():
        overrides["openai_model"] = body.model.strip()
    prompt = resolve_persona_prompt(body.persona_id)
    if prompt:
        overrides["system_prompt"] = prompt
    if overrides:
        return settings.model_copy(update=overrides)
    return settings
