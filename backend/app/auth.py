"""Авторизация: Telegram Mini App (initData), Telegram Login Widget на сайте, dev-вход локально."""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import User

MAX_AGE = 24 * 3600


class AuthError(ValueError):
    pass


def verify_webapp_init_data(init_data: str, bot_token: str) -> dict:
    """Проверка подписи initData Telegram Mini App."""
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", None)
    if not received:
        raise AuthError("нет подписи")
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        raise AuthError("подпись не сошлась")
    if time.time() - int(pairs.get("auth_date", 0)) > MAX_AGE:
        raise AuthError("данные устарели")
    return json.loads(pairs.get("user", "{}"))


def verify_login_widget(data: dict, bot_token: str) -> dict:
    """Проверка данных Telegram Login Widget."""
    data = dict(data)
    received = data.pop("hash", None)
    if not received:
        raise AuthError("нет подписи")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()) if v is not None)
    secret = hashlib.sha256(bot_token.encode()).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        raise AuthError("подпись не сошлась")
    if time.time() - int(data.get("auth_date", 0)) > MAX_AGE:
        raise AuthError("данные устарели")
    return data


async def upsert_tg_user(session: AsyncSession, tg_id: int, username: str | None, first_name: str | None) -> User:
    user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if user is None:
        user = User(tg_id=tg_id, username=username, first_name=first_name,
                    notify_threshold=get_settings().notify_threshold)
        session.add(user)
    else:
        user.username, user.first_name = username or user.username, first_name or user.first_name
    await session.flush()
    return user


def issue_token(user: User) -> str:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(hours=s.jwt_ttl_hours)
    return jwt.encode({"sub": str(user.id), "exp": exp}, s.jwt_secret, algorithm="HS256")


async def current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Нужна авторизация")
    try:
        payload = jwt.decode(auth[7:], get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Сессия истекла, войдите заново")
    user = await session.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(401, "Пользователь не найден")
    return user
