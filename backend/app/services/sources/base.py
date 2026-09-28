"""Общий формат вакансии от любого источника и реестр источников.

Реестр легальности (обновлять при подключении нового источника):
- trudvsem    открытые данные «Работа России», API без ключа           — подключён
- superjob    официальный API, нужен X-Api-App-Id                     — подключён, включается ключом
- lever       публичный API вакансий работодателя (jobs board)        — подключён
- greenhouse  публичный Job Board API работодателя                    — подключён, из РФ может не отвечать
- jsonld      разметка schema.org/JobPosting на карьерной странице    — подключён, соблюдаем robots.txt
- html_llm    разбор карьерной страницы через LLM                     — подключён, соблюдаем robots.txt
- hh.ru       API вакансий отвечает 403 без партнёрского доступа      — НЕ подключён, парсинг сайта запрещён
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawVacancy:
    source: str
    external_id: str
    title: str
    company_name: str
    url: str
    description: str = ""
    city: str | None = None
    remote: bool | None = None
    salary_from: int | None = None
    salary_to: int | None = None
    currency: str | None = None
    published_at: datetime | None = None
    company_domain: str | None = None
    extra: dict = field(default_factory=dict)


_LEGAL_FORMS = re.compile(r"\b(ооо|оао|пао|зао|ао|ип|llc|ltd|inc|gmbh)\b\.?", re.IGNORECASE)


def norm(s: str | None) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = _LEGAL_FORMS.sub(" ", s)
    s = re.sub(r"[^\w]+", " ", s)
    return " ".join(s.split())


def fingerprint(v: RawVacancy) -> str:
    """Ключ склейки дублей: одна и та же вакансия на разных площадках."""
    key = f"{norm(v.company_name)}|{norm(v.title)}|{norm(v.city)}"
    return hashlib.sha1(key.encode()).hexdigest()


def strip_html(html: str) -> str:
    from bs4 import BeautifulSoup

    return BeautifulSoup(html or "", "html.parser").get_text("\n", strip=True)


REMOTE_WORDS = ("удален", "удалён", "remote", "дистанц")


def guess_remote(*texts: str | None) -> bool | None:
    t = " ".join(x or "" for x in texts).lower()
    return True if any(w in t for w in REMOTE_WORDS) else None
