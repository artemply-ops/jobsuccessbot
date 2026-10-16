"""Адаптер LLM через OpenRouter (OpenAI-совместимый API). Единая точка вызова модели.

Правила:
- во внешнюю модель уходит только обезличенный текст; перед вызовом страхуемся has_pii();
- ответ строго по JSON-схеме (response_format json_schema, strict) и валидируется Pydantic;
- provider.require_parameters: OpenRouter шлёт запрос только тем провайдерам, кто поддерживает схему;
- модель меняется в .env (LLM_MODEL_MAIN / LLM_MODEL_SCORING) без правки кода.
"""

import copy
import json
import logging
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.services.pii import has_pii

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class PIILeakError(LLMError):
    pass


class AINotConnected(LLMError):
    """ИИ ещё не подключён (нет ключа). Сценарии показывают заглушку и идут дальше."""

    def __init__(self, what: str = "Этот шаг"):
        super().__init__(f"{what} работает на ИИ, а ИИ пока не подключён.")


def available() -> bool:
    s = get_settings()
    return s.llm_enabled and bool(s.llm_api_key)


def strict_schema(model: type[BaseModel]) -> dict:
    """JSON Schema Pydantic -> строгий вариант: все поля обязательны, лишние запрещены, без default/title."""
    schema = copy.deepcopy(model.model_json_schema())

    def fix(node):
        if isinstance(node, dict):
            node.pop("default", None)
            node.pop("title", None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)

    fix(schema)
    return schema


_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        s = get_settings()
        proxy = s.llm_proxy_url or None
        if proxy and proxy.startswith("socks5://"):  # DNS на стороне прокси: иначе часть хостов не открывается
            proxy = "socks5h://" + proxy[len("socks5://"):]
        _client = httpx.AsyncClient(
            base_url=s.llm_base_url, timeout=httpx.Timeout(300, connect=20), proxy=proxy,
            headers={"HTTP-Referer": s.public_url, "X-Title": "JobPilot",
                     **({"Authorization": f"Bearer {s.llm_api_key}"} if s.llm_api_key else {})})
    return _client


async def structured(
    *,
    schema: type[T],
    system: str,
    user: str,
    model: str | None = None,
    effort: str = "medium",
    max_tokens: int = 12000,  # OpenRouter резервирует оплату под максимум: не завышать
) -> T:
    s = get_settings()
    if not available():
        raise AINotConnected()
    if has_pii(user):
        raise PIILeakError("В тексте для LLM остались контакты. Вызов заблокирован.")

    body = {
        "model": model or s.llm_model_main,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": schema.__name__, "strict": True, "schema": strict_schema(schema)}},
        "provider": {"require_parameters": True},
        "reasoning": {"effort": effort},
    }
    last_err: Exception | None = None
    for attempt in range(2):  # одна повторная попытка, если модель нарушила схему
        try:
            r = await client().post("/chat/completions", json=body)
        except httpx.HTTPError as e:
            raise LLMError(f"Нет соединения с LLM API: {type(e).__name__}") from e
        if r.status_code == 401:
            raise LLMError("LLM API отклонил ключ (401)")
        if r.status_code == 402:
            log.error("LLM 402: %s", r.text[:300])
            raise LLMError("На балансе OpenRouter не хватает средств на этот запрос (402). Пополните баланс.")
        if r.status_code == 429:
            raise LLMError("Превышен лимит запросов к LLM, повторите позже")
        if r.status_code >= 400:
            log.error("LLM API %s: %s", r.status_code, r.text[:500])
            raise LLMError(f"Ошибка LLM API: {r.status_code}")
        data = r.json()
        if "error" in data:
            log.error("LLM API error: %s", str(data["error"])[:500])
            raise LLMError("Ошибка LLM API")
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise LLMError("Ответ модели обрезан по лимиту токенов")
        content = choice["message"].get("content") or ""
        try:
            return schema.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError) as e:
            last_err = e
            log.warning("LLM ответ не по схеме (попытка %s): %s", attempt + 1, str(e)[:300])
    raise LLMError("Модель вернула ответ не по схеме") from last_err
