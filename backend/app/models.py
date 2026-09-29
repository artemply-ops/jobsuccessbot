"""Модель данных MVP.

Разделение ПДн: контакты человека (ФИО, телефон, email) лежат только в `contacts`.
В `profiles` хранится обезличенная версия, именно она уходит в LLM.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notify_threshold: Mapped[int] = mapped_column(Integer, default=75)
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


class Package(Base):
    __tablename__ = "packages"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    cv: Mapped[dict] = mapped_column(JSON)  # schemas.CV, обезличенный
    cover_letter: Mapped[str] = mapped_column(Text)
    recruiter_message: Mapped[str] = mapped_column(Text)
    fact_flags: Mapped[list] = mapped_column(JSON, default=list)
    instruction: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


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
