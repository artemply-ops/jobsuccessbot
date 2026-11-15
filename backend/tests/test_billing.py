import datetime
import json

import httpx
import pytest

from app.config import get_settings
from app.models import BillingUsage, Contact, LlmUsage, Payment, User
from app.services import billing, intake, llm, tailor
from app.services.profile import save_basic


@pytest.fixture
def billing_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "billing_enabled", True)
    monkeypatch.setattr(get_settings(), "admin_tg_ids", [777])


async def _user(session, tg_id: int) -> User:
    u = User(tg_id=tg_id, consent_at=datetime.datetime.now())
    session.add(u)
    await session.flush()
    return u


def test_prices_margin_above_60_percent():
    """Цель владельца 15.11.2026: маржа выше 60% при ожидаемом расходе (docs/ECONOMICS.md)."""
    fees = 0.103  # эквайринг ~4,27% + УСН 6% (владелец ИП, 15.11.2026)
    cost = {"revision": 4.2, "package": 11.6}  # ₽, живой прогон 14.11 при курсе 100 и комиссии OpenRouter
    for p in billing.PRODUCTS.values():
        expected = sum(cost[a] * n for a, n in p.quota.items())
        if p.days:  # тариф: ожидаем, что используют не больше половины лимита
            expected *= 0.5
        assert (p.price_rub * (1 - fees) - expected) / p.price_rub > 0.6, p.sku
        worst = sum((15.8 if a == "package" else 4.2) * n for a, n in p.quota.items())
        assert p.price_rub * (1 - fees) - worst > 0, f"{p.sku} в минус при полном лимите"
    assert billing.PRODUCTS["week"].price_rub < 349 and billing.PRODUCTS["month"].price_rub < 1049  # дешевле «Сопровода»


async def test_free_start_then_paywall_and_promo(session, billing_on):
    user = await _user(session, 9101)
    assert await billing.require(session, user, None) is None  # перегенерация по покрытию не списывается
    g = await billing.require(session, user, "package")
    assert g.source == "free"
    await billing.consume(session, user, "package")
    await billing.consume(session, user, "revision")
    for action in ("package", "revision"):
        with pytest.raises(billing.PaymentRequired) as e:
            await billing.require(session, user, action)
        assert e.value.action == action
    info = await billing.summary(session, user)
    assert info["left"] == {"package": 0, "revision": 0} and info["products"][0]["price_stars"] >= 1

    pc = await billing.create_promo(session, "week", uses=1, days=5, code="friends-1")
    assert pc.code == "FRIENDS-1"
    await billing.redeem(session, user, "friends-1")
    info = await billing.summary(session, user)
    assert info["left"] == {"package": 6, "revision": 15} and info["plan"]["sku"] == "week"
    with pytest.raises(billing.BillingError, match="уже"):
        await billing.redeem(session, user, "FRIENDS-1")
    other = await _user(session, 9102)
    with pytest.raises(billing.BillingError, match="использован"):
        await billing.redeem(session, other, "FRIENDS-1")


async def test_expired_plan_and_order_of_grants(session, billing_on):
    user = await _user(session, 9103)
    await billing.require(session, user, "package")  # создаёт бесплатный грант
    week = await billing.grant(session, user, "week", "admin")
    assert (await billing.require(session, user, "package")).id == week.id  # сначала тратится грант со сроком
    week.valid_to = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    await session.flush()
    assert (await billing.require(session, user, "package")).source == "free"  # истёкший не считается


async def test_admin_unlimited(session, billing_on):
    admin = await _user(session, 777)
    for _ in range(3):
        await billing.consume(session, admin, "package")
    assert await billing.can(session, admin, "package")
    assert (await billing.summary(session, admin))["admin"] is True


async def test_payment_is_idempotent_and_checks_amount(session, billing_on):
    user = await _user(session, 9104)
    pay = await billing.new_payment(session, user, "package", "stars")
    assert pay.currency == "XTR" and pay.amount == billing.PRODUCTS["package"].price_stars
    with pytest.raises(billing.BillingError, match="не сходится"):
        await billing.complete_payment(session, pay.id, amount=1, currency="XTR", external_id="x")
    g = await billing.complete_payment(session, pay.id, amount=pay.amount, currency="XTR", external_id="tg-1")
    assert g and g.quota == {"package": 1}
    assert await billing.complete_payment(session, pay.id, amount=pay.amount, currency="XTR", external_id="tg-1") is None
    assert (await session.get(Payment, pay.id)).status == "succeeded"
    rub = await billing.new_payment(session, user, "month", "yookassa")
    assert rub.amount == 690 and rub.currency == "RUB"
    with pytest.raises(billing.BillingError):
        await billing.new_payment(session, user, "vip", "stars")  # только по промокоду


async def test_tailor_charges_package_and_revision_and_logs_tokens(session, billing_on, monkeypatch):
    """Списание в tailor.build: пакет и правка списываются, перегенерация нет; лимит кончился до вызова модели.
    Учёт токенов: usage от OpenRouter пишется в llm_usage."""
    from sqlalchemy import func, select

    user = await _user(session, 9105)
    await save_basic(session, user, "Аналитик данных, Москва")
    session.add(Contact(user_id=user.id, full_name="Пётр Проверкин", links=[], vault={}))
    target = await intake.add_target(session, user, text="Аналитик данных в Тестовая Компания. Задачи: отчёты по продажам и метрики продукта. "
                                     "Требования: SQL, Python, опыт от 2 лет.")
    cv = {"headline": "Аналитик", "summary": "[CANDIDATE] считает метрики.", "highlights": [], "skills": ["SQL"],
          "education": [], "languages": [], "experience": [{"company": "Компания", "role": "Аналитик",
                                                          "period": "2022 — н.в.", "bullets": ["Считал метрики"]}]}
    calls = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        name = body["response_format"]["json_schema"]["name"]
        assert body["usage"] == {"include": True}
        calls.append(name)
        out = {"TailoredCV": {"cv": cv}, "FactCheck": {"flags": []},
               "DossierData": {"business_area": "", "summary": "", "mission": None, "values": [], "products": [],
                               "culture_signals": [], "tone_of_voice": "", "sources": []},
               "ReqCoverage": {"requirements": [], "keywords": []}}.get(name, {})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(out)}}],
                                         "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "cost": 0.012}})

    monkeypatch.setattr(get_settings(), "llm_api_key", "test-key")
    monkeypatch.setattr(get_settings(), "llm_enabled", True)
    monkeypatch.setattr(llm, "_client", httpx.AsyncClient(base_url="https://or.test", transport=httpx.MockTransport(handler)))
    try:
        llm.track(user.id, "package")
        await tailor.build(session, user, target, ["cv"])
        await tailor.build(session, user, target, ["cv"], instruction="Короче")
        await tailor.build(session, user, target, ["cv"], instruction="[auto]", auto=True)  # не списывается
        n = len(calls)
        with pytest.raises(billing.PaymentRequired):
            await tailor.build(session, user, target, ["cv"], instruction="Ещё")
        with pytest.raises(billing.PaymentRequired):
            await tailor.build(session, user, target, ["cv"])
        assert len(calls) == n  # без лимита модель не вызывалась
        used = (await session.execute(select(BillingUsage.action).where(BillingUsage.user_id == user.id))).scalars().all()
        assert sorted(used) == ["package", "revision"]
    finally:
        monkeypatch.setattr(llm, "_client", None)
    await session.commit()  # SQLite: отпускаем блокировку, чтобы фоновая запись учёта прошла
    await llm.drain_usage()
    rows = (await session.execute(select(LlmUsage).where(LlmUsage.user_id == user.id))).scalars().all()
    assert rows and all(r.prompt_tokens == 1000 and r.cost_usd == 0.012 for r in rows)
    assert {r.step for r in rows} >= {"TailoredCV", "FactCheck"} and rows[0].action == "package"
    total = (await session.execute(select(func.sum(LlmUsage.cost_usd)).where(LlmUsage.user_id == user.id))).scalar()
    assert total == pytest.approx(0.012 * len(rows))
