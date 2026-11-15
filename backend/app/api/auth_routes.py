"""Вход на сайт: гость, код на почту, Telegram-виджет, Яндекс ID, одноразовая ссылка из бота.

Все способы ведут в один кабинет (services/accounts.py). Если человек уже вошёл (гость, Mini App), новый способ
входа привязывается к его кабинету. Mini App входит сам по initData (api/routes.py: /auth/telegram-webapp).
"""

import logging
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthError, current_user, issue_token, optional_user, source_hint, verify_login_widget
from app.config import get_settings
from app.db import get_session
from app.models import User
from app.services import accounts, mailer

log = logging.getLogger("auth")
router = APIRouter(prefix="/api/auth")


def _err(e: Exception) -> HTTPException:
    return HTTPException(400, str(e))


def _ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0]


@router.post("/guest")
async def guest(request: Request, session: AsyncSession = Depends(get_session)):
    """Начать на сайте без входа. Документы гость собирает после входа, всё загруженное переедет в кабинет."""
    try:
        user = await accounts.create_guest(session, _ip(request), source_hint(request))
    except accounts.AccountError as e:
        raise HTTPException(429, str(e))
    await session.commit()
    return {"token": issue_token(user)}


class EmailIn(BaseModel):
    email: str


class CodeIn(BaseModel):
    email: str
    code: str


@router.post("/email/start")
async def email_start(body: EmailIn, session: AsyncSession = Depends(get_session)):
    s = get_settings()
    if not mailer.available() and s.env != "dev":
        raise HTTPException(503, "Вход по почте скоро появится. Пока войдите через Telegram")
    try:
        code = await accounts.request_code(session, body.email)
    except accounts.AccountError as e:
        raise _err(e)
    await session.commit()
    if not mailer.available():  # локальная разработка: письма не уходят, код виден в ответе
        log.warning("dev: код входа %s", code)
        return {"ok": True, "dev_code": code}
    try:
        await mailer.send(accounts.norm_email(body.email), f"Код входа в JobPilot: {code}",
                          f"Ваш код входа в JobPilot: {code}\n\nКод действует 10 минут. Если вы не входили, "
                          f"просто удалите письмо.\n\n{s.public_url}")
    except Exception:  # noqa: BLE001  адрес в лог не пишем
        log.exception("письмо с кодом не ушло")
        raise HTTPException(502, "Не удалось отправить письмо. Попробуйте ещё раз или войдите через Telegram")
    return {"ok": True}


@router.post("/email/verify")
async def email_verify(body: CodeIn, request: Request, current: User | None = Depends(optional_user),
                       session: AsyncSession = Depends(get_session)):
    try:
        email = await accounts.check_code(session, body.email, body.code)
        user = await accounts.login_identity(session, "email", email, current, source_hint(request))
    except accounts.AccountError as e:
        await session.commit()  # счётчик неверных попыток
        raise _err(e)
    await session.commit()
    return {"token": issue_token(user)}


@router.post("/telegram-login")
async def telegram_login(body: dict, request: Request, current: User | None = Depends(optional_user),
                         session: AsyncSession = Depends(get_session)):
    """Telegram Login Widget на сайте: вход или привязка Telegram к уже открытому кабинету."""
    try:
        tg = verify_login_widget(body, get_settings().telegram_bot_token)
        user = await accounts.login_telegram(session, int(tg["id"]), tg.get("username"), tg.get("first_name"),
                                             current, source_hint(request))
    except AuthError as e:
        raise HTTPException(401, f"Telegram: {e}")
    except accounts.AccountError as e:
        raise _err(e)
    await session.commit()
    return {"token": issue_token(user)}


class TokenIn(BaseModel):
    token: str


@router.post("/token")
async def token_login(body: TokenIn, current: User | None = Depends(optional_user),
                      session: AsyncSession = Depends(get_session)):
    """Одноразовая ссылка: из бота (/web) или после Яндекс ID. Гость на сайте вливается в этот кабинет."""
    try:
        user = await accounts.use_link_token(session, body.token)
        if user is None:
            raise accounts.AccountError("Ссылка устарела. Получите новую")
        if current is not None and current.id != user.id and current.guest:
            await accounts.merge(session, keep=user, drop=current)
    except accounts.AccountError as e:
        await session.commit()
        raise _err(e)
    await session.commit()
    return {"token": issue_token(user)}


@router.get("/methods")
async def methods(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return await accounts.methods(session, user)


# ---------- Яндекс ID (включается ключами YANDEX_CLIENT_ID / YANDEX_CLIENT_SECRET) ----------

def _yandex_on() -> None:
    if not get_settings().yandex_client_id:
        raise HTTPException(404, "Вход через Яндекс пока выключен")


def _redirect_uri() -> str:
    return f"{get_settings().public_url}/api/auth/yandex/callback"


@router.post("/yandex/link")
async def yandex_link(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    """Ссылка на вход через Яндекс с привязкой к текущему кабинету (браузер уходит на Яндекс без заголовка)."""
    _yandex_on()
    token = await accounts.issue_link_token(session, user, purpose="link")
    await session.commit()
    return {"url": f"/api/auth/yandex/start?link={token}"}


@router.get("/yandex/start")
async def yandex_start(link: str | None = None):
    _yandex_on()
    s = get_settings()
    state = jwt.encode({"link": link or ""}, s.jwt_secret, algorithm="HS256")  # подпись: защита от подмены
    q = urlencode({"response_type": "code", "client_id": s.yandex_client_id, "redirect_uri": _redirect_uri(),
                   "state": state})
    return RedirectResponse(f"https://oauth.yandex.ru/authorize?{q}")


@router.get("/yandex/callback")
async def yandex_callback(code: str = "", state: str = "", session: AsyncSession = Depends(get_session)):
    _yandex_on()
    s = get_settings()
    try:
        link = jwt.decode(state, s.jwt_secret, algorithms=["HS256"]).get("link") or None
        async with httpx.AsyncClient(timeout=20) as c:  # Яндекс российский: без прокси
            t = await c.post("https://oauth.yandex.ru/token", data={
                "grant_type": "authorization_code", "code": code,
                "client_id": s.yandex_client_id, "client_secret": s.yandex_client_secret})
            t.raise_for_status()
            info = await c.get("https://login.yandex.ru/info", params={"format": "json"},
                               headers={"Authorization": f"OAuth {t.json()['access_token']}"})
            info.raise_for_status()
        yid = str(info.json()["id"])
        current = await accounts.use_link_token(session, link, purpose="link") if link else None
        user = await accounts.login_identity(session, "yandex", yid, current)
        token = await accounts.issue_link_token(session, user)
        await session.commit()
    except (jwt.PyJWTError, httpx.HTTPError, KeyError, accounts.AccountError) as e:
        log.warning("yandex: %s", type(e).__name__)
        return RedirectResponse("/?login_error=yandex")
    return RedirectResponse(f"/?login={token}")
