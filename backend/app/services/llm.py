"""Адаптер LLM через OpenRouter (OpenAI-совместимый API). Единая точка вызова модели.

Правила:
- во внешнюю модель уходит только обезличенный текст; перед вызовом страхуемся has_pii();
- ответ строго по JSON-схеме (response_format json_schema, strict) и валидируется Pydantic;
- provider.require_parameters: OpenRouter шлёт запрос только тем провайдерам, кто поддерживает схему;
- модель меняется в .env (LLM_MODEL_MAIN / LLM_MODEL_SCORING) без правки кода.
"""

import asyncio
import contextvars
import copy
import json
import logging
import uuid
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


# ---------- Учёт токенов ----------
# Кто и в рамках какого действия зовёт модель. Ставится в точке входа: API (auth.current_user), фоновая задача
# (jobs.enqueue), бот (cv_flow). Одно действие пользователя = один run: по нему считается цена пакета и правки.
_track: contextvars.ContextVar[dict] = contextvars.ContextVar("llm_track", default={})
_writes: set[asyncio.Task] = set()


def track(user_id: int | None = None, action: str | None = None, run: str | None = None) -> None:
    cur = _track.get()
    _track.set({"user_id": user_id if user_id is not None else cur.get("user_id"),
                "action": action or cur.get("action"),
                "run": run or (uuid.uuid4().hex[:16] if action else cur.get("run"))})


def _usage_row(step: str, model: str, usage: dict):
    from app.models import LlmUsage

    t = _track.get()
    details = usage.get("completion_tokens_details") or {}
    return LlmUsage(user_id=t.get("user_id"), action=t.get("action"), run=t.get("run"), step=step[:64],
                    model=model[:128], prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    reasoning_tokens=int(details.get("reasoning_tokens") or 0),
                    cost_usd=float(usage.get("cost") or 0.0))


async def _save_usage(row) -> None:
    try:
        from app.db import SessionLocal

        async with SessionLocal() as s:
            s.add(row)
            await s.commit()
    except Exception as e:  # учёт не должен ломать генерацию
        log.warning("llm usage not saved: %s", type(e).__name__)


def _record(step: str, model: str, usage: dict | None) -> None:
    if not usage:
        return
    t = asyncio.create_task(_save_usage(_usage_row(step, model, usage)))  # своя сессия: не ждём и не мешаем основной
    _writes.add(t)
    t.add_done_callback(_writes.discard)


async def drain_usage() -> None:
    """Дождаться записи учёта (тесты, завершение процесса)."""
    if _writes:
        await asyncio.gather(*list(_writes), return_exceptions=True)


def available() -> bool:
    s = get_settings()
    return s.llm_enabled and bool(s.llm_api_key)


def strict_schema(model: type[BaseModel]) -> dict:
    """JSON Schema Pydantic -> строгий вариант: все поля обязательны, лишние запрещены, без default/title."""
    schema = copy.deepcopy(model.model_json_schema())

    def fix(node):
        """Убираем служебные default/title у схем, но не трогаем словари properties и $defs:
        там ключи это имена полей, и поле с именем title (название вакансии) удалять нельзя.
        Баг до 14.11.2026: поле title пропадало из схемы, модель его не возвращала, разбор падал."""
        if isinstance(node, dict):
            node.pop("default", None)
            if isinstance(node.get("title"), str):
                node.pop("title")
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for k, v in node.items():
                if k in ("properties", "$defs") and isinstance(v, dict):
                    for sub in v.values():
                        fix(sub)
                else:
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


def _content(user: str, images: list[bytes] | None):
    if not images:
        return user
    import base64

    return [{"type": "text", "text": user}] + [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(i).decode()}}
        for i in images]


async def structured(
    *,
    schema: type[T],
    system: str,
    user: str,
    model: str | None = None,
    effort: str = "medium",
    max_tokens: int = 12000,  # OpenRouter резервирует оплату под максимум: не завышать
    images: list[bytes] | None = None,  # PNG, только уже обезличенные (см. services/ocr.py)
) -> T:
    s = get_settings()
    if not available():
        raise AINotConnected()
    if has_pii(user):
        raise PIILeakError("В тексте для LLM остались контакты. Вызов заблокирован.")

    body = {
        "model": model or s.llm_model_main,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": _content(user, images)}],
        "max_tokens": max_tokens,
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": schema.__name__, "strict": True, "schema": strict_schema(schema)}},
        "provider": {"require_parameters": True},
        "reasoning": {"effort": effort},
        "usage": {"include": True},  # OpenRouter вернёт токены и стоимость: пишем в llm_usage
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
        _record(schema.__name__, body["model"], data.get("usage"))  # платим и за ответ не по схеме: учитываем все
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
