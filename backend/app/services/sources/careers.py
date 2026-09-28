"""Карьерные страницы компаний.

discover(): по домену ищет раздел вакансий и определяет, как его читать (ATS или разметка).
fetch(): собирает вакансии компании выбранным способом.
Перед каждым запросом к сайту компании проверяем robots.txt.
"""

import json
import re
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel

from app.config import get_settings
from app.services.pii import EMAIL_RE, PHONE_RE
from app.services.sources.base import RawVacancy, guess_remote, strip_html

CAREER_PATHS = ["/careers", "/career", "/jobs", "/vacancies", "/vakansii", "/job", "/rabota", "/team", "/hr"]
CAREER_WORDS = ("ваканс", "карьер", "career", "jobs", "работа у нас", "работать у нас", "join")

GREENHOUSE_RE = re.compile(r"boards(?:-api)?\.greenhouse\.io/(?:embed/job_board\?for=|v1/boards/)?([\w-]+)")
LEVER_RE = re.compile(r"jobs\.lever\.co/([\w-]+)")


class Discovery(BaseModel):
    careers_url: str | None = None
    ats_type: str | None = None  # greenhouse | lever | jsonld | html
    ats_token: str | None = None
    status: str = "no_careers"  # active | no_careers | blocked | error
    note: str | None = None


_robots: dict[str, RobotFileParser | None] = {}


async def allowed(client: httpx.AsyncClient, url: str) -> bool:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = RobotFileParser()
        try:
            r = await client.get(base + "/robots.txt")
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
            else:
                rp.parse([])  # robots.txt нет: ограничений нет
            _robots[base] = rp
        except httpx.HTTPError:
            _robots[base] = None
    rp = _robots[base]
    return True if rp is None else rp.can_fetch(get_settings().http_user_agent, url)


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20, follow_redirects=True,
                             headers={"User-Agent": get_settings().http_user_agent})


def _detect_ats(html: str) -> tuple[str, str] | None:
    if m := LEVER_RE.search(html):
        return "lever", m.group(1)
    if m := GREENHOUSE_RE.search(html):
        return "greenhouse", m.group(1)
    return None


def _jsonld_postings(html: str) -> list[dict]:
    out = []
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("@type") == "JobPosting":
                    out.append(node)
                stack.extend(v for k, v in node.items() if k in ("@graph", "itemListElement", "item"))
            elif isinstance(node, list):
                stack.extend(node)
    return out


def _career_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        text = (a.get_text(" ", strip=True) + " " + a["href"]).lower()
        if any(w in text for w in CAREER_WORDS):
            links.append(urljoin(base_url, a["href"]))
    return list(dict.fromkeys(links))[:5]


async def discover(domain: str, careers_url: str | None = None) -> Discovery:
    """Находит раздел вакансий компании и способ его чтения."""
    async with _http() as c:
        candidates = [careers_url] if careers_url else []
        home = f"https://{domain}"
        try:
            if await allowed(c, home):
                r = await c.get(home)
                if r.status_code == 200:
                    if ats := _detect_ats(r.text):
                        return Discovery(careers_url=home, ats_type=ats[0], ats_token=ats[1], status="active")
                    candidates += _career_links(r.text, str(r.url))
        except httpx.HTTPError as e:
            return Discovery(status="error", note=f"Сайт недоступен: {type(e).__name__}")
        candidates += [home + p for p in CAREER_PATHS]

        blocked = False
        for url in dict.fromkeys(candidates):
            if not await allowed(c, url):
                blocked = True
                continue
            try:
                r = await c.get(url)
            except httpx.HTTPError:
                continue
            if r.status_code != 200:
                continue
            final = str(r.url)
            if ats := _detect_ats(r.text):
                return Discovery(careers_url=final, ats_type=ats[0], ats_token=ats[1], status="active")
            if _jsonld_postings(r.text):
                return Discovery(careers_url=final, ats_type="jsonld", status="active")
            if any(w in r.text.lower() for w in ("ваканс", "vacanc", "open positions", "открытые позиции")):
                return Discovery(careers_url=final, ats_type="html", status="active")
        if blocked:
            return Discovery(status="blocked", note="Раздел вакансий закрыт в robots.txt")
        return Discovery(status="no_careers", note="Раздел вакансий не найден")


# ---------- Чтение вакансий ----------

async def fetch_lever(token: str, company_name: str) -> list[RawVacancy]:
    async with _http() as c:
        r = await c.get(f"https://api.lever.co/v0/postings/{token}", params={"mode": "json"})
        r.raise_for_status()
        out = []
        for p in r.json():
            cat = p.get("categories") or {}
            out.append(RawVacancy(
                source="lever", external_id=p["id"], title=p.get("text", ""), company_name=company_name,
                url=p.get("hostedUrl", ""), city=cat.get("location"),
                description=(p.get("descriptionPlain") or "") + "\n" + "\n".join(
                    f"{lst.get('text')}: {strip_html(lst.get('content', ''))}" for lst in p.get("lists", [])),
                remote=guess_remote(cat.get("location"), cat.get("commitment"), p.get("workplaceType")),
            ))
        return out


async def fetch_greenhouse(token: str, company_name: str) -> list[RawVacancy]:
    async with _http() as c:
        r = await c.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"})
        r.raise_for_status()
        return [RawVacancy(
            source="greenhouse", external_id=str(j["id"]), title=j.get("title", ""), company_name=company_name,
            url=j.get("absolute_url", ""), city=(j.get("location") or {}).get("name"),
            description=strip_html(BeautifulSoup(j.get("content", ""), "html.parser").get_text()),
            remote=guess_remote((j.get("location") or {}).get("name")),
        ) for j in r.json().get("jobs", [])]


def _jsonld_to_raw(node: dict, company_name: str, page_url: str) -> RawVacancy:
    loc = node.get("jobLocation") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
    salary = (node.get("baseSalary") or {}).get("value") or {}
    return RawVacancy(
        source="jsonld", external_id=str(node.get("identifier", {}).get("value") if isinstance(node.get("identifier"), dict)
                                         else node.get("url") or node.get("title")),
        title=node.get("title", ""), company_name=company_name, url=node.get("url") or page_url,
        description=strip_html(node.get("description", "")),
        city=addr.get("addressLocality") if isinstance(addr, dict) else None,
        remote=True if node.get("jobLocationType") == "TELECOMMUTE" else None,
        salary_from=salary.get("minValue") if isinstance(salary, dict) else None,
        salary_to=salary.get("maxValue") if isinstance(salary, dict) else None,
        currency=(node.get("baseSalary") or {}).get("currency"),
    )


async def fetch_jsonld(careers_url: str, company_name: str, max_pages: int = 30) -> list[RawVacancy]:
    async with _http() as c:
        if not await allowed(c, careers_url):
            return []
        r = await c.get(careers_url)
        out = [_jsonld_to_raw(n, company_name, careers_url) for n in _jsonld_postings(r.text)]
        if out:
            return out
        # Разметка на страницах самих вакансий: идём по ссылкам со списка
        soup = BeautifulSoup(r.text, "html.parser")
        host = urlparse(careers_url).netloc
        links = [urljoin(careers_url, a["href"]) for a in soup.find_all("a", href=True)]
        links = [u for u in dict.fromkeys(links) if urlparse(u).netloc == host and u != careers_url][:max_pages]
        for u in links:
            if not await allowed(c, u):
                continue
            try:
                rr = await c.get(u)
            except httpx.HTTPError:
                continue
            out += [_jsonld_to_raw(n, company_name, u) for n in _jsonld_postings(rr.text)]
        return out


class _HtmlVacancy(BaseModel):
    title: str
    url: str | None = None
    city: str | None = None
    summary: str = ""


class _HtmlVacancies(BaseModel):
    items: list[_HtmlVacancy]


async def fetch_html_llm(careers_url: str, company_name: str) -> list[RawVacancy]:
    """Резервный способ для нестандартных страниц: LLM выделяет список вакансий из текста страницы."""
    from app.services import llm

    async with _http() as c:
        if not await allowed(c, careers_url):
            return []
        r = await c.get(careers_url)
    soup = BeautifulSoup(r.text, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    links = "\n".join(f"{a.get_text(' ', strip=True)[:80]} -> {urljoin(careers_url, a['href'])}"
                      for a in soup.find_all("a", href=True) if a.get_text(strip=True))[:15000]
    text = soup.get_text("\n", strip=True)[:20000]
    # На страницах бывают контакты рекрутеров: вычищаем до отправки в LLM
    text = EMAIL_RE.sub("[EMAIL]", PHONE_RE.sub("[PHONE]", text))
    links = EMAIL_RE.sub("[EMAIL]", links)
    res = await llm.structured(
        schema=_HtmlVacancies, model=get_settings().anthropic_model_scoring, effort="low", max_tokens=8000,
        system="Выдели из страницы карьерного раздела список открытых вакансий. Только реальные вакансии, "
               "без навигации и общих разделов. Если вакансий нет, верни пустой список. "
               "Не выписывай телефоны, email и имена людей.",
        user=f"Страница: {careers_url}\n\nТекст:\n{text}\n\nСсылки:\n{links}",
    )
    return [RawVacancy(source="html_llm", external_id=i.url or f"{careers_url}#{i.title}", title=i.title,
                       company_name=company_name, url=i.url or careers_url, city=i.city, description=i.summary,
                       remote=guess_remote(i.city, i.summary)) for i in res.items]
