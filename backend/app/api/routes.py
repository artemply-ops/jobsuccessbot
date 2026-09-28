from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import jobs
from app.auth import (AuthError, current_user, issue_token, upsert_tg_user, verify_login_widget,
                      verify_webapp_init_data)
from app.config import get_settings
from app.db import get_session
from app.models import (Company, CompanyScore, CompanySubscription, Contact, Job, Package, Source, User, Vacancy,
                        VacancyScore)
from app.schemas import ProfileData
from app.services import collect, export, profile
from app.services.stt import STTError, transcribe_any

api = APIRouter(prefix="/api")
MAX_UPLOAD = 15 * 1024 * 1024


# ---------- Авторизация ----------

class WebAppAuth(BaseModel):
    init_data: str


@api.post("/auth/telegram-webapp")
async def auth_webapp(body: WebAppAuth, session: AsyncSession = Depends(get_session)):
    try:
        tg = verify_webapp_init_data(body.init_data, get_settings().telegram_bot_token)
    except AuthError as e:
        raise HTTPException(401, f"Telegram: {e}")
    user = await upsert_tg_user(session, int(tg["id"]), tg.get("username"), tg.get("first_name"))
    await session.commit()
    return {"token": issue_token(user)}


@api.post("/auth/telegram-login")
async def auth_login(body: dict, session: AsyncSession = Depends(get_session)):
    try:
        tg = verify_login_widget(body, get_settings().telegram_bot_token)
    except AuthError as e:
        raise HTTPException(401, f"Telegram: {e}")
    user = await upsert_tg_user(session, int(tg["id"]), tg.get("username"), tg.get("first_name"))
    await session.commit()
    return {"token": issue_token(user)}


@api.post("/auth/dev")
async def auth_dev(session: AsyncSession = Depends(get_session)):
    if get_settings().env != "dev":
        raise HTTPException(404)
    user = await upsert_tg_user(session, 1, "dev", "Разработчик")
    await session.commit()
    return {"token": issue_token(user)}


@api.get("/config")
async def public_config():
    s = get_settings()
    return {"bot_username": s.telegram_bot_username, "dev_login": s.env == "dev"}


# ---------- Пользователь ----------

class ContactIn(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    city: str | None = None


class SettingsIn(BaseModel):
    notify_threshold: int


@api.get("/me")
async def me(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    c = await session.get(Contact, user.id)
    return {"id": user.id, "first_name": user.first_name, "consent": user.consent_at is not None,
            "notify_threshold": user.notify_threshold, "is_admin": user.tg_id in get_settings().admin_tg_ids,
            "contact": {"full_name": c.full_name, "email": c.email, "phone": c.phone, "city": c.city} if c else None}


@api.post("/me/consent")
async def consent(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    u = await session.get(User, user.id)
    u.consent_at = datetime.now(timezone.utc)
    await session.commit()
    return {"ok": True}


@api.patch("/me/settings")
async def update_settings(body: SettingsIn, user: User = Depends(current_user),
                          session: AsyncSession = Depends(get_session)):
    u = await session.get(User, user.id)
    u.notify_threshold = max(0, min(100, body.notify_threshold))
    await session.commit()
    return {"ok": True}


@api.put("/contact")
async def put_contact(body: ContactIn, user: User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    c = await profile.get_contact(session, user)
    for k, v in body.model_dump().items():
        setattr(c, k, v or None)
    await session.commit()
    return {"ok": True}


def _need_consent(user: User):
    if user.consent_at is None:
        raise HTTPException(403, "Нужно согласие на обработку персональных данных")


# ---------- Источники и профиль ----------

def _src(s: Source) -> dict:
    return {"id": s.id, "kind": s.kind, "filename": s.filename, "status": s.status, "error": s.error,
            "preview": (s.text or "")[:200], "created_at": s.created_at}


@api.get("/sources")
async def list_sources(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Source).where(Source.user_id == user.id).order_by(Source.id))).scalars()
    return [_src(s) for s in rows]


@api.post("/sources/file")
async def upload_file(file: UploadFile = File(...), user: User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    _need_consent(user)
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Файл больше 15 МБ")
    src = await profile.add_file_source(session, user, file.filename or "file", data)
    await session.commit()
    return _src(src)


class TextIn(BaseModel):
    text: str


@api.post("/sources/text")
async def add_text(body: TextIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    _need_consent(user)
    if not body.text.strip():
        raise HTTPException(400, "Пустой текст")
    src = await profile.add_text_source(session, user, body.text)
    await session.commit()
    return _src(src)


@api.post("/sources/voice")
async def add_voice(file: UploadFile = File(...), user: User = Depends(current_user),
                    session: AsyncSession = Depends(get_session)):
    _need_consent(user)
    data = await file.read()
    try:
        text = await transcribe_any(data)
    except STTError as e:
        raise HTTPException(422, str(e))
    if not text.strip():
        raise HTTPException(422, "Не удалось разобрать речь")
    src = await profile.add_text_source(session, user, text, kind="voice")
    await session.commit()
    return _src(src)


@api.delete("/sources/{source_id}")
async def delete_source(source_id: int, user: User = Depends(current_user),
                        session: AsyncSession = Depends(get_session)):
    src = await session.get(Source, source_id)
    if src is None or src.user_id != user.id:
        raise HTTPException(404)
    await session.delete(src)
    await session.commit()
    return {"ok": True}


@api.get("/profile")
async def get_profile(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    p = await profile.latest_profile(session, user.id)
    if p is None:
        return None
    return {"version": p.version, "data": p.data, "questions": p.questions, "confirmed": p.confirmed}


class BuildIn(BaseModel):
    source_ids: list[int] | None = None


@api.post("/profile/build")
async def build(body: BuildIn, user: User = Depends(current_user)):
    _need_consent(user)
    return {"job_id": await jobs.enqueue("profile", user.id, jobs.build_profile_job(user.id, body.source_ids))}


class ProfileIn(BaseModel):
    data: ProfileData
    confirm: bool = False


@api.put("/profile")
async def put_profile(body: ProfileIn, user: User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    p = await profile.save_edited(session, user, body.data, body.confirm)
    await session.commit()
    return {"version": p.version, "confirmed": p.confirmed}


@api.post("/profile/answers")
async def answers(body: TextIn, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    _need_consent(user)
    src = await profile.add_text_source(session, user, body.text)
    await session.commit()
    return {"job_id": await jobs.enqueue("profile", user.id, jobs.build_profile_job(user.id, [src.id]))}


# ---------- Поиск ----------

@api.post("/search/run")
async def run_search(user: User = Depends(current_user)):
    return {"job_id": await jobs.enqueue("search", user.id, jobs.search_job(user.id))}


@api.get("/jobs/{job_id}")
async def job_status(job_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    j = await session.get(Job, job_id)
    if j is None or (j.user_id and j.user_id != user.id):
        raise HTTPException(404)
    return {"id": j.id, "kind": j.kind, "status": j.status, "note": j.note, "result": j.result}


async def _version(session: AsyncSession, user_id: int) -> int | None:
    p = await profile.latest_profile(session, user_id)
    return p.version if p else None


def _vac(v: Vacancy, sc: VacancyScore | None) -> dict:
    return {"id": v.id, "title": v.title, "company_name": v.company_name, "company_id": v.company_id,
            "city": v.city, "remote": v.remote, "salary_from": v.salary_from, "salary_to": v.salary_to,
            "currency": v.currency, "url": v.url, "sources": [s["source"] for s in v.sources],
            "published_at": v.published_at,
            "score": sc.score if sc else None, "breakdown": sc.breakdown if sc else {},
            "pros": sc.pros if sc else [], "gaps": sc.gaps if sc else []}


@api.get("/vacancies")
async def vacancies(min_score: int = 0, remote: bool | None = None, q: str | None = None, limit: int = 100,
                    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    ver = await _version(session, user.id)
    if ver is None:
        return []
    # Оценки берём по актуальной версии профиля, а если пересчёта ещё не было, по самой свежей
    sub = (select(VacancyScore.vacancy_id, func.max(VacancyScore.profile_version).label("pv"))
           .where(VacancyScore.user_id == user.id, VacancyScore.score.is_not(None))
           .group_by(VacancyScore.vacancy_id).subquery())
    stmt = (select(Vacancy, VacancyScore)
            .join(sub, sub.c.vacancy_id == Vacancy.id)
            .join(VacancyScore, (VacancyScore.vacancy_id == Vacancy.id) & (VacancyScore.user_id == user.id)
                  & (VacancyScore.profile_version == sub.c.pv))
            .where(VacancyScore.score >= min_score, Vacancy.active.is_(True)))
    if remote is not None:
        stmt = stmt.where(Vacancy.remote.is_(True) if remote else Vacancy.remote.is_not(True))
    if q:
        stmt = stmt.where(Vacancy.title.ilike(f"%{q}%") | Vacancy.company_name.ilike(f"%{q}%"))
    rows = (await session.execute(stmt.order_by(VacancyScore.score.desc()).limit(min(limit, 300)))).all()
    return [_vac(v, sc) for v, sc in rows]


@api.get("/vacancies/{vacancy_id}")
async def vacancy(vacancy_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    v = await session.get(Vacancy, vacancy_id)
    if v is None:
        raise HTTPException(404)
    sc = (await session.execute(select(VacancyScore).where(VacancyScore.user_id == user.id,
                                                           VacancyScore.vacancy_id == v.id)
                                .order_by(VacancyScore.profile_version.desc()).limit(1))).scalar_one_or_none()
    pkgs = (await session.execute(select(Package.id, Package.version, Package.created_at)
                                  .where(Package.user_id == user.id, Package.vacancy_id == v.id)
                                  .order_by(Package.version.desc()))).all()
    return {**_vac(v, sc), "description": v.description,
            "packages": [{"id": i, "version": ver, "created_at": c} for i, ver, c in pkgs]}


# ---------- Компании ----------

@api.get("/companies")
async def companies(segment: str | None = None, min_score: int = 0, user: User = Depends(current_user),
                    session: AsyncSession = Depends(get_session)):
    ver = await _version(session, user.id)
    comps = (await session.execute(select(Company).where(Company.moderated.is_(True)))).scalars().all()
    scores = {}
    if ver:
        scores = {s.company_id: s for s in (await session.execute(
            select(CompanyScore).where(CompanyScore.user_id == user.id, CompanyScore.profile_version == ver))).scalars()}
    open_counts = dict((await session.execute(
        select(Vacancy.company_id, func.count()).where(Vacancy.active.is_(True), Vacancy.company_id.is_not(None))
        .group_by(Vacancy.company_id))).all())
    subs = set((await session.execute(select(CompanySubscription.company_id)
                                      .where(CompanySubscription.user_id == user.id))).scalars())
    out = []
    for c in comps:
        if segment and segment not in c.segments:
            continue
        sc = scores.get(c.id)
        if min_score and (sc is None or sc.score < min_score):
            continue
        out.append({"id": c.id, "name": c.name, "domain": c.domain, "segments": c.segments,
                    "description": c.description, "careers_url": c.careers_url, "status": c.status,
                    "score": sc.score if sc else None, "reasons": sc.reasons if sc else [],
                    "open_vacancies": open_counts.get(c.id, 0), "subscribed": c.id in subs})
    out.sort(key=lambda x: (x["score"] is None, -(x["score"] or 0), x["name"]))
    return out


class CompanyIn(BaseModel):
    name: str
    domain: str
    segment: str | None = None


@api.post("/companies")
async def suggest_company(body: CompanyIn, user: User = Depends(current_user),
                          session: AsyncSession = Depends(get_session)):
    domain = body.domain.lower().split("//")[-1].split("/")[0].removeprefix("www.")
    if (await session.execute(select(Company).where(Company.domain == domain))).scalar_one_or_none():
        raise HTTPException(409, "Такая компания уже есть в каталоге")
    session.add(Company(name=body.name.strip(), domain=domain, segments=[body.segment] if body.segment else [],
                        moderated=False, added_by_user_id=user.id))
    await session.commit()
    return {"ok": True, "note": "Компания добавлена на модерацию"}


@api.post("/companies/{company_id}/subscribe")
async def subscribe(company_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    if await session.get(CompanySubscription, (user.id, company_id)) is None:
        session.add(CompanySubscription(user_id=user.id, company_id=company_id))
        await session.commit()
    return {"ok": True}


@api.delete("/companies/{company_id}/subscribe")
async def unsubscribe(company_id: int, user: User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    sub = await session.get(CompanySubscription, (user.id, company_id))
    if sub:
        await session.delete(sub)
        await session.commit()
    return {"ok": True}


# ---------- Пакеты ----------

class PackageIn(BaseModel):
    instruction: str | None = None


@api.post("/vacancies/{vacancy_id}/package")
async def make_package(vacancy_id: int, body: PackageIn, user: User = Depends(current_user)):
    _need_consent(user)
    return {"job_id": await jobs.enqueue("package", user.id, jobs.package_job(user.id, vacancy_id, body.instruction))}


async def _own_package(session: AsyncSession, package_id: int, user: User) -> Package:
    pkg = await session.get(Package, package_id)
    if pkg is None or pkg.user_id != user.id:
        raise HTTPException(404)
    return pkg


@api.get("/packages/{package_id}")
async def get_package(package_id: int, user: User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    pkg = await _own_package(session, package_id, user)
    view = export.cv_view(pkg, await session.get(Contact, user.id), drop_flagged=False)
    return {"id": pkg.id, "vacancy_id": pkg.vacancy_id, "version": pkg.version, "flags": pkg.fact_flags,
            "instruction": pkg.instruction, **view}


@api.get("/packages/{package_id}/{fmt}")
async def download(package_id: int, fmt: str, accept_flags: bool = False, user: User = Depends(current_user),
                   session: AsyncSession = Depends(get_session)):
    pkg = await _own_package(session, package_id, user)
    contact = await session.get(Contact, user.id)
    try:
        if fmt == "docx":
            data = export.to_docx(pkg, contact, accept_flags)
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif fmt == "pdf":
            data, mime = export.to_pdf(pkg, contact, accept_flags), "application/pdf"
        else:
            raise HTTPException(404)
    except export.FlagsNotAccepted as e:
        raise HTTPException(409, str(e))
    return Response(data, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="cv_{pkg.vacancy_id}_v{pkg.version}.{fmt}"'})


# ---------- Администрирование ----------

def _admin(user: User = Depends(current_user)) -> User:
    if user.tg_id not in get_settings().admin_tg_ids and get_settings().env != "dev":
        raise HTTPException(403)
    return user


@api.post("/admin/seed")
async def admin_seed(_: User = Depends(_admin), session: AsyncSession = Depends(get_session)):
    n = await collect.load_seed(session)
    await session.commit()
    return {"added": n}


@api.post("/admin/discover")
async def admin_discover(limit: int = 20, user: User = Depends(_admin)):
    async def fn(s):
        return await collect.discover_companies(s, limit=limit)
    return {"job_id": await jobs.enqueue("discover", user.id, fn)}


@api.post("/admin/collect-companies")
async def admin_collect(user: User = Depends(_admin)):
    async def fn(s):
        return await collect.collect_companies(s)
    return {"job_id": await jobs.enqueue("collect", user.id, fn)}


@api.get("/admin/companies/pending")
async def pending(_: User = Depends(_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Company).where(Company.moderated.is_(False)))).scalars()
    return [{"id": c.id, "name": c.name, "domain": c.domain, "segments": c.segments} for c in rows]


@api.post("/admin/companies/{company_id}/approve")
async def approve(company_id: int, _: User = Depends(_admin), session: AsyncSession = Depends(get_session)):
    c = await session.get(Company, company_id)
    if c is None:
        raise HTTPException(404)
    c.moderated = True
    await session.commit()
    return {"ok": True}
