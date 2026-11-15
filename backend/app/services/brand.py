"""Фирменные цвета компании для схемы резюме «в цветах компании».

Цвета считаются заново при каждом запросе (решение владельца 15.11.2026): берём главную страницу сайта компании
и её стили, ищем theme-color, CSS-переменные вида --primary/--brand/--accent и самые частые насыщенные цвета.
Серые, почти белые и почти чёрные отбрасываем. Сайт недоступен или цветов нет: None, схема не предлагается.
В LLM ничего не уходит.
"""

import colorsys
import logging
import re
from collections import Counter
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.services.sources.careers import allowed

log = logging.getLogger(__name__)
HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
RGB = re.compile(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})")
VAR = re.compile(r"--[\w-]*(primary|brand|accent|main|corporate)[\w-]*\s*:\s*([^;}{]+)", re.I)
MAX_CSS = 3
MAX_BYTES = 400_000


def _hex(c: str) -> str | None:
    m = HEX.search(c)
    if m:
        h = m.group(1).lower()
        return "".join(x * 2 for x in h) if len(h) == 3 else h
    m = RGB.search(c)
    if m:
        r, g, b = (min(255, int(x)) for x in m.groups())
        return f"{r:02x}{g:02x}{b:02x}"
    return None


def _hls(h: str) -> tuple[float, float, float]:
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return colorsys.rgb_to_hls(r, g, b)


def _from_hls(h: float, l: float, s: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h, max(0, min(1, l)), max(0, min(1, s)))
    return f"{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


def is_brand(h: str) -> bool:
    _, l, s = _hls(h)
    return s >= 0.25 and 0.12 <= l <= 0.85


def pick(weights: Counter) -> list[str]:
    """До двух фирменных цветов с разным оттенком, по убыванию веса."""
    out: list[str] = []
    for h, _ in weights.most_common():
        if not is_brand(h):
            continue
        hue = _hls(h)[0]
        if all(min(abs(hue - _hls(o)[0]), 1 - abs(hue - _hls(o)[0])) > 0.08 for o in out):
            out.append(h)
        if len(out) == 2:
            break
    return out


def collect(html_text: str, css_texts: list[str]) -> Counter:
    w: Counter = Counter()
    soup = BeautifulSoup(html_text, "html.parser")
    for name in ("theme-color", "msapplication-TileColor"):
        tag = soup.find("meta", attrs={"name": name})
        if tag and (h := _hex(tag.get("content", ""))):
            w[h] += 40
    texts = [t.get_text() for t in soup.find_all("style")] + css_texts
    inline = " ".join(t.get("style", "") for t in soup.find_all(style=True))
    for text in [*texts, inline]:
        for m in VAR.finditer(text):
            if h := _hex(m.group(2)):
                w[h] += 15
        for m in HEX.finditer(text):
            h = m.group(1).lower()
            w["".join(x * 2 for x in h) if len(h) == 3 else h] += 1
        for m in RGB.finditer(text):
            r, g, b = (min(255, int(x)) for x in m.groups())
            w[f"{r:02x}{g:02x}{b:02x}"] += 1
    return w


async def brand_colors(domain: str) -> list[str]:
    s = get_settings()
    url = f"https://{domain}"
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True,
                                     headers={"User-Agent": s.http_user_agent}) as c:
            if not await allowed(c, url):
                return []
            r = await c.get(url)
            if r.status_code != 200:
                return []
            page = r.text[:MAX_BYTES]
            soup = BeautifulSoup(page, "html.parser")
            host = urlparse(str(r.url)).netloc
            css = []
            for link in soup.find_all("link", rel=lambda v: v and "stylesheet" in v)[:MAX_CSS * 2]:
                href = urljoin(str(r.url), link.get("href", ""))
                if urlparse(href).netloc.split(":")[0].endswith(host.split(":")[0].removeprefix("www.")):
                    try:
                        cr = await c.get(href)
                        if cr.status_code == 200:
                            css.append(cr.text[:MAX_BYTES])
                    except httpx.HTTPError:
                        pass
                if len(css) >= MAX_CSS:
                    break
    except httpx.HTTPError as e:
        log.info("brand %s: %s", domain, type(e).__name__)
        return []
    return pick(collect(page, css))


def palette(colors: list[str]) -> dict:
    """Схема из фирменных цветов: акцент как есть, тёмная база того же оттенка, светлый цвет для шапки."""
    main = colors[0]
    h, l, s = _hls(main)
    signal = main if l <= 0.7 else _from_hls(h, 0.5, s)
    head = _from_hls(h, 0.17, min(s, 0.55))
    if len(colors) > 1:
        h2, l2, s2 = _hls(colors[1])
        highlight = _from_hls(h2, max(l2, 0.72), min(1, max(s2, 0.5)))
    else:
        highlight = _from_hls(h, 0.8, min(1, max(s, 0.5)))
    return {"signal": f"#{signal}", "head": f"#{head}", "highlight": f"#{highlight}"}
