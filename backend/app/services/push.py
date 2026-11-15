"""Web Push: уведомление в браузер, даже когда вкладка и браузер закрыты (система показывает его сама).

Пуш идёт через сервис производителя браузера (Google, Mozilla, Apple), содержимое шифруется ключами подписки.
В тексте только общая фраза («Документы готовы»), без имени, вакансии и других ПДн.
Ключи VAPID: VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY в .env (scripts/gen_vapid.py). Пусто: пуши выключены.
"""

import asyncio
import json
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import PushSubscription, User

log = logging.getLogger(__name__)


def available() -> bool:
    s = get_settings()
    return bool(s.vapid_public_key and s.vapid_private_key)


async def subscribe(session: AsyncSession, user: User, sub: dict) -> None:
    endpoint = sub.get("endpoint") or ""
    keys = sub.get("keys") or {}
    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth") or len(endpoint) > 1024:
        raise ValueError("Некорректная подписка браузера")
    row = (await session.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint))).scalar_one_or_none()
    if row is None:
        session.add(PushSubscription(user_id=user.id, endpoint=endpoint, p256dh=keys["p256dh"], auth=keys["auth"]))
    else:  # тот же браузер, другой кабинет (вышел и вошёл): подписка переезжает
        row.user_id, row.p256dh, row.auth = user.id, keys["p256dh"], keys["auth"]
    await session.flush()


async def unsubscribe(session: AsyncSession, user: User, endpoint: str) -> None:
    await session.execute(delete(PushSubscription).where(PushSubscription.user_id == user.id,
                                                         PushSubscription.endpoint == endpoint))


def _send_one(sub: PushSubscription, payload: str) -> int | None:
    """Возвращает HTTP-код ошибки сервиса пушей или None, если ушло."""
    from pywebpush import WebPushException, webpush

    s = get_settings()
    try:
        webpush({"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}, payload,
                vapid_private_key=s.vapid_private_key,
                vapid_claims={"sub": f"mailto:{s.support_email}" if s.support_email else s.public_url},
                ttl=3600, timeout=10)
        return None
    except WebPushException as e:
        return e.response.status_code if e.response is not None else 0
    except Exception:  # noqa: BLE001  сеть, таймаут: пуш необязателен
        return 0


async def send(session: AsyncSession, user_id: int, title: str, body: str, url: str = "/make") -> int:
    """Пуш на все браузеры пользователя. Протухшие подписки (404/410) удаляются. Возвращает число доставленных."""
    if not available():
        return 0
    subs = (await session.execute(select(PushSubscription).where(PushSubscription.user_id == user_id))).scalars().all()
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": "jobpilot"}, ensure_ascii=False)
    sent = 0
    for sub in subs:
        code = await asyncio.to_thread(_send_one, sub, payload)
        if code is None:
            sent += 1
        elif code in (404, 410):
            await session.delete(sub)
        else:
            log.info("push failed: %s", code)
    await session.commit()
    return sent
