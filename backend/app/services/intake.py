"""Этап 2 пивота. Сбор данных под одну вакансию: вакансия (текст или ссылка) -> разбор -> досье компании -> уточнения.

Правила:
- ссылки hh.ru не читаем (парсинг запрещён), просим вставить текст;
- перед загрузкой любой страницы проверяем robots.txt;
- контакты рекрутеров вычищаются до хранения, имена людей обезличиваются до отправки в LLM;
- ответы на уточнения становятся источником профиля: иначе проверка фактов на этапе 3 их не увидит.
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Target, User, Vacancy
from app.schemas import ClarifyQuestions, VacancyParsed
from app.services import collect, llm, package, profile
from app.services.pii import Vault, depersonalize, scrub_contacts
from app.services.sources.base import RawVacancy, fingerprint, guess_remote, strip_html
from app.services.sources.careers import _jsonld_postings, allowed

log = logging.getLogger(__name__)

MAX_PAGE_BYTES = 3_000_000
MAX_VACANCY_CHARS = 20_000
NO_COMPANY = "Компания не указана"

# hh.ru и его зеркала: API закрыт без партнёрского доступа, парсинг сайта запрещён
HH_HOSTS = ("hh.ru", "hh.kz", "hh.uz", "hh.by", "headhunter.ru", "headhunter.kz", "career.ru")
# Площадки и ATS: сайт компании по домену вакансии не определить
JOB_BOARDS = ("lever.co", "greenhouse.io", "huntflow.io", "huntflow.ru", "talantix.ru", "career.habr.com",
              "superjob.ru", "trudvsem.ru", "rabota.ru", "zarplata.ru", "avito.ru", "linkedin.com", "getmatch.ru",
              "geekjob.ru", "workable.com", "smartrecruiters.com", "teamtailor.com", "recruitee.com",
              "breezy.hr", "bamboohr.com", "myworkdayjobs.com", "potok.io", "friendwork.ru", "dreamjob.ru")
CAREER_SUBDOMAINS = ("career.", "careers.", "jobs.", "job.", "hr.", "team.", "rabota.", "vacancy.", "work.")

SYSTEM_VACANCY = """Ты рекрутер-аналитик. Разбери текст вакансии на поля.
Только то, что есть в тексте, ничего не додумывай. Нет данных: пустая строка, null или пустой список.
Формулировки требований и задач сокращай, но не меняй смысл. Маркеры вида [PERSON_1], [LINK_1] не раскрывай.
Пиши на языке вакансии."""

SYSTEM_CLARIFY = """Ты карьерный консультант. Кандидат делает резюме под конкретную вакансию.
Сравни профиль кандидата с требованиями вакансии и задай 2-3 вопроса, ответы на которые сильнее всего
усилят резюме именно под эту вакансию:
- требование вакансии, про которое в профиле ничего нет, но опыт может быть (спроси, был ли такой опыт);
- достижение без цифр там, где вакансия ждёт измеримый результат;
- непонятный масштаб: команда, бюджет, число клиентов.
Не спрашивай о том, что уже есть в профиле. Не спрашивай о зарплате и контактах.
Вопрос короткий, конкретный, на «вы», с примером ответа в скобках, если это помогает. В field укажи,
к какому требованию вакансии относится вопрос. Маркер [CANDIDATE] это кандидат. Пиши по-русски."""


class IntakeError(ValueError):
    """Понятная пользователю причина, почему вакансию не удалось принять."""


@dataclass
class Page:
    url: str
    text: str
    title: str = ""
    company_name: str = ""
    company_url: str = ""
    jsonld: dict = field(default_factory=dict)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _host_in(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def normalize_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    if not _host(url) or "." not in _host(url):
        raise IntakeError("Не похоже на ссылку. Пришлите адрес страницы вакансии или вставьте её текст.")
    return url


def is_hh(url: str) -> bool:
    return _host_in(_host(url), HH_HOSTS)


def company_domain_from(url: str) -> str | None:
    """Сайт компании по адресу страницы вакансии: career.company.ru -> company.ru. Для площадок и ATS None."""
    host = _host(url)
    if not host or _host_in(host, JOB_BOARDS) or _host_in(host, HH_HOSTS):
        return None
    for sub in CAREER_SUBDOMAINS:
        if host.startswith(sub) and host.count(".") >= 2:
            return host[len(sub):]
    return host


def page_from_html(html: str, url: str) -> Page:
    """Текст вакансии со страницы: сначала разметка schema.org/JobPosting, иначе основной текст."""
    postings = _jsonld_postings(html)
    if postings:
        node = postings[0]
        org = node.get("hiringOrganization") or {}
        org = org if isinstance(org, dict) else {}
        same = org.get("sameAs") or org.get("url") or ""
        same = same[0] if isinstance(same, list) and same else same
        title = node.get("title") or ""
        text = f"{title}\n{strip_html(node.get('description', ''))}"
        for key in ("qualifications", "responsibilities", "skills", "experienceRequirements"):
            if isinstance(node.get(key), str):
                text += "\n" + strip_html(node[key])
        return Page(url=url, text=text, title=title, company_name=org.get("name") or "",
                    company_url=same if isinstance(same, str) else "", jsonld=node)
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "")[:300]
    for t in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg"]):
        t.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    return Page(url=url, text=root.get_text("\n", strip=True), title=title)


class PageUnreadable(IntakeError):
    """Страницу прочитать нельзя. reason: простыми словами почему; дальше просим вставить текст вакансии."""

    def __init__(self, code: str, reason: str):
        self.code, self.reason = code, reason
        super().__init__(f"Не получилось прочитать страницу: {reason} Скопируйте текст вакансии и вставьте его сюда.")


async def fetch_page(url: str) -> Page:
    url = normalize_url(url)
    if is_hh(url):
        raise PageUnreadable("hh", "ссылки hh.ru я не открываю, их правила запрещают автоматическое чтение.")
    s = get_settings()
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": s.http_user_agent}) as c:
        try:
            if not await allowed(c, url):
                raise PageUnreadable("robots", "сайт запрещает программам читать эту страницу.")
            r = await c.get(url)
        except httpx.TimeoutException as e:
            raise PageUnreadable("timeout", "сайт не ответил за 20 секунд.") from e
        except httpx.HTTPError as e:
            log.info("fetch_page %s: %s", _host(url), type(e).__name__)
            raise PageUnreadable("unreachable", "сайт недоступен или адрес с ошибкой.") from e
    if is_hh(str(r.url)):
        raise PageUnreadable("hh", "ссылка ведёт на hh.ru, такие страницы я не открываю.")
    if r.status_code in (401, 403):
        raise PageUnreadable("closed", "сайт закрыл страницу от программ или просит войти.")
    if r.status_code == 404:
        raise PageUnreadable("not_found", "страница не найдена, возможно, вакансию уже закрыли.")
    if r.status_code != 200:
        raise PageUnreadable("status", f"сайт ответил ошибкой {r.status_code}.")
    if "html" not in r.headers.get("content-type", "") or len(r.content) > MAX_PAGE_BYTES:
        raise PageUnreadable("not_html", "по ссылке не веб-страница, а файл.")
    page = page_from_html(r.text, str(r.url))
    if len(page.text) < 200:  # страница рисуется скриптами: текста в HTML нет
        raise PageUnreadable("js", "текст вакансии на этой странице показывается скриптом, программа его не видит.")
    return page


async def check_url(url: str) -> dict:
    """Отдельная проверка ссылки до разбора: читается ли страница. Без LLM, только загрузка."""
    try:
        page = await fetch_page(url)
    except PageUnreadable as e:
        return {"ok": False, "code": e.code, "reason": e.reason, "message": str(e)}
    except IntakeError as e:
        return {"ok": False, "code": "bad_url", "reason": str(e), "message": str(e)}
    return {"ok": True, "title": (page.title or "")[:200], "company": page.company_name, "chars": len(page.text),
            "jobposting": bool(page.jsonld)}


def basic_parse(text: str, page: Page | None = None) -> VacancyParsed:
    """Разбор без ИИ: название из разметки или первой строки, компания из разметки."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    first = re.split(r"(?<=[^\s.]{3})\.\s", lines[0])[0].strip(" .") if lines else ""  # первое предложение
    title = (page.title if page and page.jsonld else "") or (first if 0 < len(first) <= 120 else "Вакансия")
    m = re.search(r"(?im)^\W*(?:компания|работодатель|company)\s*[:\-—]\s*([^\n.;]{2,80})", text.replace(". ", ".\n"))
    company = (page.company_name if page else "") or (m.group(1).strip() if m else "")
    return VacancyParsed(title=title[:200], company_name=company, company_site="", remote=guess_remote(text))


async def parse_vacancy(text: str, page: Page | None = None) -> VacancyParsed:
    if not llm.available():
        return basic_parse(text, page)
    hint = f"Компания по разметке страницы: {page.company_name}\n" if page and page.company_name else ""
    return await llm.structured(schema=VacancyParsed, system=SYSTEM_VACANCY, model=get_settings().llm_model_scoring,
                                effort="low", max_tokens=4000,
                                user=f"{hint}Текст вакансии:\n{depersonalize(text, None, Vault())}")


def _domain(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    try:
        return company_domain_from(normalize_url(value))
    except IntakeError:
        return None


async def add_target(session: AsyncSession, user: User, *, url: str | None = None, text: str | None = None,
                     company_url: str | None = None) -> Target:
    """Принимает вакансию (ссылку, текст или оба) и сохраняет её как цель пользователя."""
    text = (text or "").strip()
    url = (url or "").strip()
    if not text and not url:
        raise IntakeError("Пришлите ссылку на вакансию на сайте компании или её текст.")
    page = None
    if url and not text:
        page = await fetch_page(url)
        text = page.text
    elif url:
        url = normalize_url(url)
    text = scrub_contacts(text)[:MAX_VACANCY_CHARS]
    if len(text) < 80:
        raise IntakeError("Текст вакансии слишком короткий. Пришлите его целиком: задачи и требования.")

    parsed = await parse_vacancy(text, page)
    parsed.company_name = parsed.company_name.strip() or NO_COMPANY
    domain = (_domain(company_url) or _domain(page.company_url if page else None)
              or _domain(parsed.company_site) or (company_domain_from(url) if url else None))

    page_url = (page.url if page else url) or ""
    raw = RawVacancy(source="user", external_id=hashlib.sha1(text.encode()).hexdigest()[:16], title=parsed.title,
                     company_name=parsed.company_name, url=page_url or f"user:{user.id}", description=text,
                     city=parsed.city or None, remote=parsed.remote, salary_from=parsed.salary_from,
                     salary_to=parsed.salary_to, currency=parsed.currency or None, company_domain=domain)
    await collect.upsert(session, [raw])
    vacancy = (await session.execute(select(Vacancy).where(Vacancy.fingerprint == fingerprint(raw)))).scalar_one()

    target = Target(user_id=user.id, vacancy_id=vacancy.id, source_url=page_url or None, company_domain=domain,
                    parsed=parsed.model_dump())
    session.add(target)
    await session.flush()
    return target


async def dossier(session: AsyncSession, target: Target) -> dict | None:
    """Досье компании: миссия, ценности, продукт, tone of voice. Без ИИ None. Ошибка сайта не роняет сценарий."""
    if not llm.available():
        return None
    vacancy = await session.get(Vacancy, target.vacancy_id)
    try:
        return await package.get_dossier(session, vacancy, domain=target.company_domain)
    except llm.LLMError as e:
        log.warning("Досье для target %s не собрано: %s", target.id, e)
        return None


async def clarify(session: AsyncSession, user: User, target: Target) -> list[dict]:
    """2-3 уточняющих вопроса по разрыву между профилем и вакансией. Без ИИ или без профиля: пустой список."""
    prof = await profile.latest_profile(session, user.id)
    if prof is None or not llm.available():
        return []
    p = VacancyParsed.model_validate(target.parsed)
    vac = (f"{p.title} — {p.company_name}\nЗадачи: {'; '.join(p.responsibilities)}\n"
           f"Требования: {'; '.join(p.requirements)}\nБудет плюсом: {'; '.join(p.nice_to_have)}")
    res = await llm.structured(schema=ClarifyQuestions, system=SYSTEM_CLARIFY, effort="medium", max_tokens=3000,
                               user=f"ПРОФИЛЬ КАНДИДАТА:\n{profile.profile_to_text(prof.data)}\n\nВАКАНСИЯ:\n{vac}")
    target.questions = [q.model_dump() for q in res.questions[:3]]
    await session.flush()
    return target.questions


async def answer(session: AsyncSession, user: User, target: Target, text: str) -> None:
    """Ответы на уточнения: в источники профиля и сразу в профиль (если ИИ подключён)."""
    if not text.strip():
        raise IntakeError("Пустой ответ")
    qs = "\n".join(f"- {q['question']}" for q in target.questions)
    title = VacancyParsed.model_validate(target.parsed).title
    src = await profile.add_text_source(
        session, user, f"Ответы кандидата на уточнения под вакансию «{title}».\nВопросы:\n{qs}\nОтветы:\n{text.strip()}")
    if llm.available() and await profile.latest_profile(session, user.id):
        await profile.build_profile(session, user, [src])
    target.answered = True
    await session.flush()


def view(target: Target, vacancy: Vacancy, dossier_data: dict | None = None) -> dict:
    return {"id": target.id, "vacancy_id": vacancy.id, "title": vacancy.title, "company": vacancy.company_name,
            "source_url": target.source_url, "company_domain": target.company_domain, "parsed": target.parsed,
            "questions": target.questions, "answered": target.answered, "dossier": dossier_data,
            "ai": llm.available(), "created_at": target.created_at}

