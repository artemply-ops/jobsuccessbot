"""Модель данных MVP.

Разделение ПДн: контакты человека (ФИО, телефон, email) лежат только в `contacts`.
В `profiles` хранится обезличенная версия, именно она уходит в LLM.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)  # id Telegram бывают больше 2^31
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notify_threshold: Mapped[int] = mapped_column(Integer, default=75)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # Гость: начал работу на сайте без входа. Скачать документы может только после входа (services/accounts.py)
    guest: Mapped[bool | None] = mapped_column(Boolean)
    # Метка первого касания: откуда пришёл (services/attribution.py). Только код ссылки, без данных человека
    source: Mapped[str | None] = mapped_column(String(64))


class Identity(Base):
    """Способ входа кроме Telegram: почта, Яндекс ID. Telegram живёт в users.tg_id.
    У одного пользователя может быть несколько способов, все ведут в один кабинет."""

    __tablename__ = "identities"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(16))  # email | yandex
    subject: Mapped[str] = mapped_column(String(256))  # почта в нижнем регистре или id Яндекса
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("provider", "subject"),)


class LoginCode(Base):
    """Одноразовый код входа по почте. Хранится только хэш кода и хэш адреса."""

    __tablename__ = "login_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    email_hash: Mapped[str] = mapped_column(String(64), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RateHit(Base):
    """Счётчик для лимитов (Т2): переживает перезапуск и работает при нескольких процессах.
    key это вид лимита и HMAC-хэш значения (например, guest:<хэш IP>), сам IP не хранится. Чистка в cleanup."""

    __tablename__ = "rate_hits"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class LoginToken(Base):
    """Одноразовая ссылка: вход на сайт из бота (/web), возврат с Яндекс ID, привязка способа входа."""

    __tablename__ = "login_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    purpose: Mapped[str] = mapped_column(String(16))  # login | link
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PushSubscription(Base):
    """Подписка браузера на Web Push (services/push.py). В пуше только «документы готовы», без ПДн."""

    __tablename__ = "push_subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    endpoint: Mapped[str] = mapped_column(String(1024), unique=True)
    p256dh: Mapped[str] = mapped_column(String(256))
    auth: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SiteTextVersion(Base):
    """Версия текстов лендинга (services/site_texts.py). Текущая это последняя, хранятся 30."""

    __tablename__ = "site_text_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Contact(Base):
    """Хранилище ПДн. Никогда не передаётся во внешние сервисы целиком."""

    __tablename__ = "contacts"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    full_name: Mapped[str | None] = mapped_column(String(256))
    email: Mapped[str | None] = mapped_column(String(256))
    phone: Mapped[str | None] = mapped_column(String(64))
    city: Mapped[str | None] = mapped_column(String(128))
    links: Mapped[list] = mapped_column(JSON, default=list)
    # Имена третьих лиц, найденные в документах (рекомендатели и т.п.): токен -> исходник
    vault: Mapped[dict] = mapped_column(JSON, default=dict)
    photo_path: Mapped[str | None] = mapped_column(String(512))  # фото для CV, в LLM не уходит


class Source(Base):
    """Исходный материал: файл, голосовое, текст, ссылка."""

    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # file | voice | text | link
    filename: Mapped[str | None] = mapped_column(String(256))
    path: Mapped[str | None] = mapped_column(String(512))
    text: Mapped[str | None] = mapped_column(Text)  # сырой текст, содержит ПДн, наружу не уходит
    status: Mapped[str] = mapped_column(String(16), default="new")  # new | parsed | error
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Profile(Base):
    __tablename__ = "profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)  # schemas.ProfileData, обезличенный
    questions: Mapped[list] = mapped_column(JSON, default=list)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("user_id", "version"),)


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    domain: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    segments: Mapped[list] = mapped_column(JSON, default=list)  # it, digital, fintech, adtech, medtech
    description: Mapped[str | None] = mapped_column(Text)
    careers_url: Mapped[str | None] = mapped_column(String(512))
    ats_type: Mapped[str | None] = mapped_column(String(32))  # greenhouse | lever | jsonld | html
    ats_token: Mapped[str | None] = mapped_column(String(128))
    # unverified -> active | no_careers | blocked | error
    status: Mapped[str] = mapped_column(String(16), default="unverified")
    status_note: Mapped[str | None] = mapped_column(Text)
    moderated: Mapped[bool] = mapped_column(Boolean, default=True)
    added_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Vacancy(Base):
    """Одна строка на уникальную вакансию. Дубли с разных площадок склеиваются по fingerprint."""

    __tablename__ = "vacancies"
    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), index=True)
    company_name: Mapped[str] = mapped_column(String(256))
    title: Mapped[str] = mapped_column(String(512))
    city: Mapped[str | None] = mapped_column(String(128))
    remote: Mapped[bool | None] = mapped_column(Boolean)
    salary_from: Mapped[int | None] = mapped_column(Integer)
    salary_to: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(8))
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(1024))
    sources: Mapped[list] = mapped_column(JSON, default=list)  # [{source, external_id, url}]
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class VacancyScore(Base):
    __tablename__ = "vacancy_scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id"), index=True)
    profile_version: Mapped[int] = mapped_column(Integer)
    prefilter: Mapped[float] = mapped_column(default=0.0)
    method: Mapped[str] = mapped_column(String(8), default="ai")  # ai | basic (без ИИ, по ключевым словам)
    score: Mapped[int | None] = mapped_column(Integer)  # None: не прошёл в топ, LLM не звали
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    pros: Mapped[list] = mapped_column(JSON, default=list)
    gaps: Mapped[list] = mapped_column(JSON, default=list)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("user_id", "vacancy_id", "profile_version"),)


class CompanyScore(Base):
    __tablename__ = "company_scores"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    profile_version: Mapped[int] = mapped_column(Integer)
    score: Mapped[int] = mapped_column(Integer)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    __table_args__ = (UniqueConstraint("user_id", "company_id", "profile_version"),)


class CompanySubscription(Base):
    __tablename__ = "company_subscriptions"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Dossier(Base):
    __tablename__ = "dossiers"
    company_key: Mapped[str] = mapped_column(String(256), primary_key=True)  # домен или нормализованное имя
    data: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Target(Base):
    """Вакансия, под которую пользователь делает CV: ссылка или текст от него самого, уточнения и ответы."""

    __tablename__ = "targets"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id"))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    company_domain: Mapped[str | None] = mapped_column(String(256))
    parsed: Mapped[dict] = mapped_column(JSON, default=dict)  # schemas.VacancyParsed
    questions: Mapped[list] = mapped_column(JSON, default=list)
    answered: Mapped[bool] = mapped_column(Boolean, default=False)
    coverage: Mapped[dict | None] = mapped_column(JSON)  # покрытие требований исходным профилем (services/coverage)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Package(Base):
    __tablename__ = "packages"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    cv: Mapped[dict] = mapped_column(JSON)  # schemas.CV, обезличенный
    cover_letter: Mapped[str] = mapped_column(Text)
    recruiter_message: Mapped[str] = mapped_column(Text)  # П5: сообщение нанимающему менеджеру
    linkedin: Mapped[dict | None] = mapped_column(JSON)  # П3: schemas.LinkedIn, обезличенный
    fact_flags: Mapped[list] = mapped_column(JSON, default=list)
    instruction: Mapped[str | None] = mapped_column(Text)
    # Этап 3 пивота: пакет под цель пользователя. Колонки nullable: init_db досоздаёт их на старой базе
    target_id: Mapped[int | None] = mapped_column(ForeignKey("targets.id"), index=True)
    items: Mapped[list | None] = mapped_column(JSON)  # подмножество schemas.PACKAGE_ITEMS
    scheme: Mapped[str | None] = mapped_column(String(32))  # cv_render.SCHEMES
    coverage: Mapped[dict | None] = mapped_column(JSON)  # покрытие требований готовым резюме (services/coverage)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LlmUsage(Base):
    """Учёт токенов: только числа, текст запросов не хранится (см. llm.structured)."""

    __tablename__ = "llm_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    action: Mapped[str | None] = mapped_column(String(32))  # tailor | improve | profile | review | ... (llm.track)
    run: Mapped[str | None] = mapped_column(String(40), index=True)  # одно действие пользователя = один run
    step: Mapped[str] = mapped_column(String(64))  # имя схемы ответа: TailoredBoth, FactCheck, ...
    model: Mapped[str] = mapped_column(String(128))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Grant(Base):
    """Лимиты пользователя: бесплатный старт, покупка, тариф, промокод (services/billing.py)."""

    __tablename__ = "grants"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    sku: Mapped[str] = mapped_column(String(32))  # free | revision | package | week | month | ...
    quota: Mapped[dict] = mapped_column(JSON)  # {"package": 6, "revision": 15}
    source: Mapped[str] = mapped_column(String(16))  # free | payment | promo | admin
    ref: Mapped[str | None] = mapped_column(String(64))  # id платежа или промокода
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # None: бессрочно
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BillingUsage(Base):
    """Одно платное действие: пакет или правка. grant_id None: администратор, без списания."""

    __tablename__ = "billing_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    grant_id: Mapped[int | None] = mapped_column(ForeignKey("grants.id"), index=True)
    action: Mapped[str] = mapped_column(String(16))  # package | revision
    target_id: Mapped[int | None] = mapped_column(Integer)
    package_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(16))  # yookassa | stars
    sku: Mapped[str] = mapped_column(String(32))
    amount: Mapped[int] = mapped_column(Integer)  # рубли для RUB, звёзды для XTR
    currency: Mapped[str] = mapped_column(String(8))  # RUB | XTR
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | succeeded | canceled
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    grant_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PromoCode(Base):
    __tablename__ = "promo_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    sku: Mapped[str] = mapped_column(String(32))  # продукт из billing.PRODUCTS
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    used: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(256))
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PromoRedemption(Base):
    __tablename__ = "promo_redemptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    code_id: Mapped[int] = mapped_column(ForeignKey("promo_codes.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("code_id", "user_id"),)


class Job(Base):
    """Фоновая задача: сбор вакансий, скоринг, генерация."""

    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | error
    note: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
