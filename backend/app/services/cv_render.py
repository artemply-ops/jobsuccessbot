"""Вёрстка CV и сопроводительного письма в PDF. Один шаблон, цветовые схемы «два базовых + акцент».

Текст остаётся настоящим и идёт в одну логическую колонку, чтобы его читали системы отбора резюме.
Шрифты лежат в репозитории, поэтому PDF выглядит одинаково на любой машине.
"""

import io
from dataclasses import dataclass
from pathlib import Path

from fpdf import FPDF
from PIL import Image, ImageOps

FONTS = Path(__file__).resolve().parents[1] / "assets" / "fonts"
Color = tuple[int, int, int]


def _rgb(h: str) -> Color:
    h = h.lstrip("#")
    return int(h[:2], 16), int(h[2:4], 16), int(h[4:6], 16)


@dataclass(frozen=True)
class Scheme:
    key: str
    title: str
    paper: str  # базовый светлый: фон
    ink: str  # базовый тёмный: текст
    accent: str  # акцент: заголовки, линии, маркеры


SCHEMES = {s.key: s for s in [
    Scheme("navy", "Бежевый, чёрный, navy", "#F5EFE4", "#17171B", "#1E3A6B"),
    Scheme("burgundy", "Слоновая кость, уголь, бордо", "#F8F3EA", "#201E20", "#7A1F3D"),
    Scheme("emerald", "Светло-серый, чёрный, изумруд", "#F1F2ED", "#171A19", "#0D6455"),
    Scheme("terracotta", "Песочный, тёмный шоколад, терракота", "#F4EBDF", "#2A201B", "#A3481F"),
    Scheme("olive", "Кремовый, графит, олива", "#F6F3E8", "#25272A", "#55622F"),
]}


@dataclass(frozen=True)
class Density:
    body: float
    line: float
    name: float
    section_gap: float
    item_gap: float
    margin: float


FULL = Density(body=10.5, line=5.3, name=26, section_gap=6.0, item_gap=3.4, margin=18)
ONE_PAGE = Density(body=9.4, line=4.4, name=22, section_gap=3.6, item_gap=1.9, margin=14)


def _mix(a: Color, b: Color, t: float) -> Color:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


class _Doc(FPDF):
    def __init__(self, scheme: Scheme):
        super().__init__(format="A4")
        self.paper, self.ink, self.accent = _rgb(scheme.paper), _rgb(scheme.ink), _rgb(scheme.accent)
        self.muted = _mix(self.ink, self.paper, 0.38)
        self.hairline = _mix(self.ink, self.paper, 0.75)
        self.add_font("Sans", "", FONTS / "PT_Sans-Web-Regular.ttf")
        self.add_font("Sans", "B", FONTS / "PT_Sans-Web-Bold.ttf")
        self.add_font("Serif", "B", FONTS / "PT_Serif-Web-Bold.ttf")
        self.c_margin = 0

    def header(self):  # фон на каждой странице
        self.set_fill_color(*self.paper)
        self.rect(0, 0, self.w, self.h, "F")

    def text_block(self, text: str, size: float, line: float, color: Color, style: str = "", family: str = "Sans",
                   x: float | None = None, w: float | None = None):
        self.set_font(family, style, size)
        self.set_text_color(*color)
        if x is not None:
            self.set_x(x)
        self.multi_cell(w if w is not None else self.w - self.get_x() - self.r_margin, line, text,
                        align="L", new_x="LMARGIN", new_y="NEXT")

    def block_height(self, text: str, size: float, line: float, w: float, style: str = "", family: str = "Sans") -> float:
        self.set_font(family, style, size)
        return len(self.multi_cell(w, line, text, align="L", dry_run=True, output="LINES")) * line


def _photo_jpeg(data: bytes) -> io.BytesIO:
    """Кроп 4:5 с учётом EXIF-поворота; фото в модель не отправляется, только в итоговый файл."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img = ImageOps.fit(img, (640, 800), Image.Resampling.LANCZOS, centering=(0.5, 0.35))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    buf.seek(0)
    return buf


def _head(pdf: _Doc, d: Density, name: str, headline: str, contacts: str, photo: bytes | None):
    """Шапка: сплошная полоса акцентного цвета и тонкая тёмная линия под ней."""
    top = pdf.t_margin
    photo_w = (27 if d is ONE_PAGE else 32) if photo else 0
    photo_h = photo_w * 1.25
    width = pdf.epw - (photo_w + 7 if photo else 0)
    title, sub = (name, headline) if name else (headline, "")
    light = _mix(pdf.paper, pdf.accent, 0.18)
    name_line, sub_line = d.name * 0.46, d.line + 1
    h = pdf.block_height(title, d.name, name_line, width, "B", "Serif") + 1.2
    if sub:
        h += pdf.block_height(sub, d.body + 2.2, sub_line, width, "B") + 0.8
    if contacts:
        h += pdf.block_height(contacts, d.body - 0.8, d.line, width)
    h = max(h, photo_h)
    band = top + h + top * 0.7
    pdf.set_fill_color(*pdf.accent)
    pdf.rect(0, 0, pdf.w, band, "F")
    pdf.set_fill_color(*pdf.ink)
    pdf.rect(0, band, pdf.w, 1.4, "F")
    if photo:
        pdf.image(_photo_jpeg(photo), x=pdf.w - pdf.r_margin - photo_w, y=top, w=photo_w, h=photo_h)
    pdf.set_y(top)
    pdf.text_block(title, d.name, name_line, pdf.paper, "B", "Serif", w=width)
    pdf.ln(1.2)
    if sub:
        pdf.text_block(sub, d.body + 2.2, sub_line, pdf.paper, "B", w=width)
        pdf.ln(0.8)
    if contacts:
        pdf.text_block(contacts, d.body - 0.8, d.line, light, w=width)
    pdf.set_y(band + 1.4)


def _section(pdf: _Doc, d: Density, title: str):
    pdf.ln(d.section_gap)
    pdf.set_font("Sans", "B", d.body - 0.6)
    pdf.set_text_color(*pdf.accent)
    pdf.set_char_spacing(0.9)
    pdf.cell(0, d.line, title.upper(), new_x="LMARGIN", new_y="NEXT")
    pdf.set_char_spacing(0)
    pdf.set_draw_color(*pdf.hairline)
    pdf.set_line_width(0.2)
    y = pdf.get_y() + 0.5
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(2.0)


def _bullet(pdf: _Doc, d: Density, text: str):
    y = pdf.get_y()
    if y + d.line > pdf.page_break_trigger:
        pdf.add_page()
        y = pdf.get_y()
    pdf.set_fill_color(*pdf.accent)
    pdf.rect(pdf.l_margin + 0.5, y + d.line * 0.5 - 0.6, 1.2, 1.2, "F")
    pdf.text_block(text, d.body, d.line, pdf.ink, x=pdf.l_margin + 4.5, w=pdf.epw - 4.5)
    pdf.ln(0.5)


def _role(pdf: _Doc, d: Density, e: dict):
    needed = d.line * 3.2  # должность не должна остаться одна внизу страницы
    if pdf.get_y() + needed > pdf.page_break_trigger:
        pdf.add_page()
    period = e.get("period") or ""
    pdf.set_font("Sans", "", d.body - 0.8)
    pw = pdf.get_string_width(period) + 1
    y = pdf.get_y()
    pdf.text_block(e["role"], d.body + 0.7, d.line + 0.3, pdf.ink, "B", w=pdf.epw - pw - 4)
    after = pdf.get_y()
    pdf.set_font("Sans", "", d.body - 0.8)
    pdf.set_text_color(*pdf.muted)
    pdf.set_xy(pdf.w - pdf.r_margin - pw, y)
    pdf.cell(pw, d.line + 0.3, period, align="R")
    pdf.set_xy(pdf.l_margin, after)
    pdf.text_block(e["company"], d.body, d.line, pdf.accent, "B")
    pdf.ln(0.7)
    for b in e.get("bullets", []):
        _bullet(pdf, d, b)
    pdf.ln(d.item_gap)


def render_cv(v: dict, scheme: str = "navy", one_page: bool = False, photo: bytes | None = None) -> tuple[bytes, int]:
    """v: name, contacts, headline, summary, skills, experience, education, languages. Возвращает (pdf, страниц)."""
    d = ONE_PAGE if one_page else FULL
    pdf = _Doc(SCHEMES[scheme])
    pdf.set_margins(d.margin, d.margin - 2, d.margin)
    pdf.set_auto_page_break(True, d.margin - 3)
    pdf.add_page()
    _head(pdf, d, v.get("name") or "", v.get("headline") or "", v.get("contacts") or "", photo)
    if v.get("summary"):
        pdf.ln(d.section_gap * 0.7)
        pdf.text_block(v["summary"], d.body, d.line, pdf.ink)
    if v.get("skills"):
        _section(pdf, d, "Ключевые навыки")
        pdf.text_block("  ·  ".join(v["skills"]), d.body, d.line, pdf.ink)
    if v.get("experience"):
        _section(pdf, d, "Опыт работы")
        for e in v["experience"]:
            _role(pdf, d, e)
    if v.get("education"):
        _section(pdf, d, "Образование")
        for x in v["education"]:
            pdf.text_block(x, d.body, d.line, pdf.ink)
    if v.get("languages"):
        _section(pdf, d, "Языки")
        pdf.text_block("  ·  ".join(v["languages"]), d.body, d.line, pdf.ink)
    return bytes(pdf.output()), pdf.page_no()


def render_letter(v: dict, scheme: str = "navy", company: str = "", position: str = "") -> bytes:
    """Письмо в одном стиле с CV: та же шапка, без фото, спокойная ширина строки."""
    d = FULL
    pdf = _Doc(SCHEMES[scheme])
    pdf.set_margins(24, 20, 24)
    pdf.set_auto_page_break(True, 18)
    pdf.add_page()
    _head(pdf, d, v.get("name") or "", v.get("headline") or "", v.get("contacts") or "", None)
    pdf.ln(9)
    if company or position:
        pdf.text_block(" · ".join(x for x in [company, position] if x), d.body, d.line, pdf.accent, "B")
        pdf.ln(5)
    for para in (v.get("cover_letter") or "").split("\n"):
        if para.strip():
            pdf.text_block(para.strip(), d.body + 0.5, d.line + 1.0, pdf.ink)
            pdf.ln(3.2)
    return bytes(pdf.output())


def preview_png(pdf_bytes: bytes, page: int = 0, dpi: int = 110) -> bytes:
    """Картинка страницы для превью в боте и PWA."""
    import pymupdf

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        return doc[page].get_pixmap(dpi=dpi).tobytes("png")
