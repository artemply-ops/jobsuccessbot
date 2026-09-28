"""Адаптер Claude. Единая точка вызова LLM со структурированным выводом.

Правила:
- во внешнюю модель уходит только обезличенный текст; перед вызовом страхуемся has_pii();
- ответ всегда валидируется Pydantic-схемой (structured outputs);
- на отказ модели (stop_reason == "refusal") включён серверный fallback.
"""

import logging
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from app.config import get_settings
from app.services.pii import has_pii

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class PIILeakError(LLMError):
    pass


_client: anthropic.AsyncAnthropic | None = None


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()  # ключ из ANTHROPIC_API_KEY
    return _client


async def structured(
    *,
    schema: type[T],
    system: str,
    user: str,
    model: str | None = None,
    effort: str = "medium",
    max_tokens: int = 32000,
) -> T:
    s = get_settings()
    if not s.llm_enabled:
        raise LLMError("LLM выключена настройкой LLM_ENABLED=false")
    if has_pii(user):
        raise PIILeakError("В тексте для LLM остались контакты. Вызов заблокирован.")

    try:
        async with client().messages.stream(
            model=model or s.anthropic_model_main,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config={"effort": effort},
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
            extra_body={"fallbacks": "default"},
        ) as stream:
            msg = await stream.get_final_message()
    except anthropic.RateLimitError as e:
        raise LLMError("Превышен лимит запросов к LLM, повторите позже") from e
    except anthropic.APIStatusError as e:
        log.error("LLM API error %s request_id=%s", e.status_code, getattr(e, "request_id", None))
        raise LLMError(f"Ошибка LLM API: {e.status_code}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Нет соединения с LLM API") from e

    if msg.stop_reason == "refusal":
        raise LLMError("Модель отказалась обрабатывать запрос")
    if msg.stop_reason == "max_tokens":
        raise LLMError("Ответ модели обрезан по лимиту токенов")
    parsed = msg.parsed_output
    if parsed is None:
        raise LLMError("Модель вернула ответ не по схеме")
    return parsed
