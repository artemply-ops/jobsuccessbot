"""Выгрузка пакета в DOCX и PDF. Здесь и только здесь обезличенные маркеры меняются на реальные данные."""

import io
from pathlib import Path

from docx import Document
from docx.shared import Pt
from fpdf import FPDF

from app.models import Contact, Package
from app.services.pii import Vault, restore

FONT_CANDIDATES = [
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
]


class FlagsNotAccepted(ValueError):
    pass


FILE_TITLES = {"cv": "Резюме", "one_page": "Одностраничник", "letter": "Сопроводительное_письмо"}
NO_COMPANY = "Компания не указана"  # как в services/intake.py


def _slug(s: str | None, limit: int) -> str:
    """Кусок имени файла: буквы и цифры любого алфавита, пробелы и знаки в «_», не длиннее limit."""
    import re

    s = re.sub(r"[^\w]+", "_", (s or "").strip(), flags=re.UNICODE).strip("_")
    return s[:limit].rstrip("_")


def file_name(item: str, pkg: Package, contact: Contact | None, role: str | None = None, company: str | None = None,
              ext: str = "pdf") -> str:
    """Имя файла для скачивания: Имя_Фамилия_Резюме_Роль_Компания[_v2].pdf (решение владельца 16.11.2026).
    Имя берём из контактов (как в самом документе), роль из вакансии, компанию если она указана."""
    who = "_".join((contact.full_name or "").split()[:2]) if contact else ""
    parts = [_slug(who, 40), FILE_TITLES.get(item, "Документ"), _slug(role, 50)]
    if company and company.strip() and company != NO_COMPANY:
        parts.append(_slug(company, 30))
    if pkg.version and pkg.version > 1:
        parts.append(f"v{pkg.version}")
    return "_".join(p for p in parts if p) + f".{ext}"


FILE_ITEMS = ("cv", "one_page", "letter")  # как schemas.FILE_ITEMS: у message и linkedin файлов нет
EXTRA_WHERE = ("recruiter_message", "linkedin")


def file_names(pkg: Package, contact: Contact | None, role: str | None, company: str | None) -> dict:
    return {it: file_name(it, pkg, contact, role, company) for it in (pkg.items or []) if it in FILE_ITEMS}


def docx_names(pkg: Package, contact: Contact | None, role: str | None, company: str | None) -> dict:
    return {it: file_name(it, pkg, contact, role, company, ext="docx") for it in (pkg.items or []) if it in FILE_ITEMS}


def _flagged(pkg: Package) -> set[str]:
    return {f["text"].strip() for f in pkg.fact_flags or []}


def cv_view(pkg: Package, contact: Contact | None, drop_flagged: bool = True) -> dict:
    """CV с реальными контактами. Неподтверждённые пункты CV выкидываем."""
    flagged = _flagged(pkg) if drop_flagged else set()
    vault = Vault(dict(contact.vault or {})) if contact else Vault()
    r = lambda t: restore(t, contact.full_name if contact else None, contact.email if contact else None,  # noqa: E731
                          contact.phone if contact else None, vault)
    cv = pkg.cv
    return {
        "name": (contact.full_name if contact else None) or "",
        "contacts": " · ".join(x for x in [contact.email, contact.phone, contact.city] if x) if contact else "",
        "headline": r(cv["headline"]),
        "summary": r(cv["summary"]),
        "skills": [r(s) for s in cv["skills"]],
        "experience": [{**e, "bullets": [r(b) for b in e["bullets"] if b.strip() not in flagged]}
                       for e in cv["experience"]],
        "education": [r(x) for x in cv.get("education", [])],
        "languages": [r(x) for x in cv.get("languages", [])],
        "cover_letter": r(pkg.cover_letter),
        "recruiter_message": r(pkg.recruiter_message),
    }


def letter_flags(pkg: Package) -> list[dict]:
    """Флаги письма: всё, что не резюме и не тексты LinkedIn и сообщения менеджеру (как tailor.letter_flag)."""
    return [f for f in pkg.fact_flags or [] if f.get("where") != "cv" and f.get("where") not in EXTRA_WHERE]


def _check(pkg: Package, accept_flags: bool) -> None:
    letter_flags_ = letter_flags(pkg)
    if letter_flags_ and not accept_flags:
        raise FlagsNotAccepted("В письме есть утверждения, не подтверждённые профилем. "
                               "Исправьте или подтвердите их явно.")


def to_docx(pkg: Package, contact: Contact | None, accept_flags: bool = False) -> bytes:
    _check(pkg, accept_flags)
    v = cv_view(pkg, contact)
    doc = Document()
    doc.styles["Normal"].font.name = "Arial"
    doc.styles["Normal"].font.size = Pt(10.5)
    doc.add_heading(v["name"] or v["headline"], level=0)
    if v["name"]:
        doc.add_paragraph(v["headline"]).runs[0].bold = True
    if v["contacts"]:
        doc.add_paragraph(v["contacts"])
    doc.add_paragraph(v["summary"])
    doc.add_heading("Ключевые навыки", level=2)
    doc.add_paragraph(", ".join(v["skills"]))
    doc.add_heading("Опыт работы", level=2)
    for e in v["experience"]:
        p = doc.add_paragraph()
        p.add_run(f"{e['role']} — {e['company']}").bold = True
        p.add_run(f"   {e['period']}")
        for b in e["bullets"]:
            doc.add_paragraph(b, style="List Bullet")
    if v["education"]:
        doc.add_heading("Образование", level=2)
        for x in v["education"]:
            doc.add_paragraph(x)
    if v["languages"]:
        doc.add_heading("Языки", level=2)
        doc.add_paragraph(", ".join(v["languages"]))
    doc.add_page_break()
    doc.add_heading("Сопроводительное письмо", level=1)
    for para in v["cover_letter"].split("\n"):
        if para.strip():
            doc.add_paragraph(para)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _font() -> tuple[str, str]:
    for reg, bold in FONT_CANDIDATES:
        if Path(reg).exists():
            return reg, bold if Path(bold).exists() else reg
    raise RuntimeError("Не найден TTF-шрифт с кириллицей (Arial или DejaVu). Установите fonts-dejavu-core.")


def to_pdf(pkg: Package, contact: Contact | None, accept_flags: bool = False) -> bytes:
    _check(pkg, accept_flags)
    v = cv_view(pkg, contact)
    reg, bold = _font()
    pdf = FPDF(format="A4")
    pdf.set_margins(18, 16, 18)
    pdf.add_font("Main", "", reg)
    pdf.add_font("Main", "B", bold)
    pdf.add_page()
    w = pdf.epw

    def h(text: str, size: int = 13):
        pdf.ln(2)
        pdf.set_font("Main", "B", size)
        pdf.multi_cell(w, 7, text)
        pdf.set_font("Main", "", 10.5)

    def p(text: str, line: float = 5.2):
        pdf.set_font("Main", "", 10.5)
        pdf.multi_cell(w, line, text)

    h(v["name"] or v["headline"], 18)
    if v["name"]:
        h(v["headline"], 12)
    if v["contacts"]:
        p(v["contacts"])
    pdf.ln(1)
    p(v["summary"])
    h("Ключевые навыки")
    p(", ".join(v["skills"]))
    h("Опыт работы")
    for e in v["experience"]:
        pdf.set_font("Main", "B", 11)
        pdf.multi_cell(w, 6, f"{e['role']} — {e['company']}   ({e['period']})")
        for b in e["bullets"]:
            p(f"•  {b}")
        pdf.ln(1)
    if v["education"]:
        h("Образование")
        for x in v["education"]:
            p(x)
    if v["languages"]:
        h("Языки")
        p(", ".join(v["languages"]))
    pdf.add_page()
    h("Сопроводительное письмо", 14)
    for para in v["cover_letter"].split("\n"):
        if para.strip():
            p(para)
            pdf.ln(1.5)
    return bytes(pdf.output())


# ---------- Этап 3 пивота: файлы в шаблоне cv_render ----------

def tailored_view(pkg: Package, contact: Contact | None, drop_flagged: bool = True) -> dict:
    """Данные для шаблона с реальными контактами. Неподтверждённые пункты и показатели CV выкидываем."""
    flagged = _flagged(pkg) if drop_flagged else set()
    vault = Vault(dict(contact.vault or {})) if contact else Vault()
    r = lambda t: restore(t or "", contact.full_name if contact else None, contact.email if contact else None,  # noqa: E731
                          contact.phone if contact else None, vault)
    cv = pkg.cv or {}
    hl = [h for h in cv.get("highlights", [])
          if h["value"].strip() not in flagged and f"{h['value']} {h['label']}".strip() not in flagged]
    return {
        "name": (contact.full_name if contact else None) or "",
        "contacts": " · ".join(x for x in [contact.email, contact.phone, contact.city] if x) if contact else "",
        "headline": r(cv.get("headline")),
        "summary": r(cv.get("summary")),
        "highlights": [{"value": r(h["value"]), "label": r(h["label"])} for h in hl],
        "skills": [r(s) for s in cv.get("skills", [])],
        "experience": [{"period": e["period"], "company": r(e["company"]), "role": r(e["role"]),
                        "bullets": [r(b) for b in e["bullets"] if b.strip() not in flagged]}
                       for e in cv.get("experience", [])],
        "education": [r(x) for x in cv.get("education", [])],
        "languages": [r(x) for x in cv.get("languages", [])],
        "cover_letter": r(pkg.cover_letter),
        "recruiter_message": r(pkg.recruiter_message),
        "linkedin": _restore_linkedin(pkg.linkedin, r),
    }


def _restore_linkedin(li: dict | None, r) -> dict | None:
    if not li:
        return None
    return {"headline": r(li.get("headline")), "about": r(li.get("about")),
            "experience": [{"role": r(e.get("role")), "company": r(e.get("company")), "period": e.get("period", ""),
                            "text": r(e.get("text"))} for e in li.get("experience", [])],
            "skills": [r(s) for s in li.get("skills", [])]}


def linkedin_text(li: dict | None) -> str:
    """Профиль LinkedIn одним текстом: по разделам, чтобы скопировать в поля профиля."""
    if not li:
        return ""
    parts = [f"Заголовок (Headline):\n{li['headline']}", f"О себе (About):\n{li['about']}"]
    if li.get("experience"):
        parts.append("Опыт (Experience):\n" + "\n\n".join(
            f"{e['role']}, {e['company']} ({e['period']})\n{e['text']}" for e in li["experience"]))
    if li.get("skills"):
        parts.append("Навыки (Skills):\n" + ", ".join(li["skills"]))
    return "\n\n".join(parts)


def fit_one_page(v: dict, scheme: str, photo: bytes | None = None, max_steps: int = 80) -> tuple[bytes, dict]:
    """Ужимает резюме до одной страницы без LLM: модель уже отсортировала пункты по важности.

    Порядок: убираем последний пункт у роли с наибольшим числом пунктов (при равенстве у более старой,
    у трёх свежих ролей остаётся хотя бы 2 пункта), затем старые роли целиком, затем второй ряд показателей.
    """
    import copy

    from app.services.cv_render import render_cv

    v = copy.deepcopy(v)
    for _ in range(max_steps):
        pdf, pages = render_cv(v, scheme, one_page=True, photo=photo)
        if pages <= 1:
            return pdf, v
        exp = v.get("experience") or []
        floor = lambda i: 2 if i < 3 else 1  # noqa: E731
        cand = [(len(e["bullets"]), i) for i, e in enumerate(exp) if len(e["bullets"]) > floor(i)]
        if cand:
            _, i = max(cand)
            exp[i]["bullets"].pop()
        elif len(exp) > 3:
            exp.pop()
        elif len(v.get("highlights") or []) > 4:
            v["highlights"] = v["highlights"][:4]
        elif len(v.get("skills") or []) > 8:
            v["skills"] = v["skills"][:8]
        else:
            break
    return render_cv(v, scheme, one_page=True, photo=photo)[0], v


def render_item(pkg: Package, contact: Contact | None, item: str, photo: bytes | None = None,
                accept_flags: bool = False, scheme: str | None = None, company: str = "", position: str = "") -> bytes:
    """Один документ пакета в PDF: cv, one_page или letter."""
    from app.services.cv_render import render_cv, render_letter

    if item not in (pkg.items or []) or item not in FILE_ITEMS:
        raise ValueError("Этого документа нет в пакете")
    scheme = scheme or pkg.scheme or "graphite"
    v = tailored_view(pkg, contact)
    if item == "letter":
        _check(pkg, accept_flags)
        return render_letter(v, scheme, company=company, position=position)
    if item == "one_page":
        return fit_one_page(v, scheme, photo)[0]
    return render_cv(v, scheme, one_page=False, photo=photo)[0]


def render_docx(pkg: Package, contact: Contact | None, item: str, accept_flags: bool = False, company: str = "",
                position: str = "") -> bytes:
    """П7: документ пакета в Word. Тот же текст, что в PDF (одностраничник ужат тем же рендером), простая вёрстка:
    Word у работодателя открывает и правит его без сюрпризов. Фото в Word не ставим."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, RGBColor

    if item not in (pkg.items or []) or item not in FILE_ITEMS:
        raise ValueError("Этого документа нет в пакете")
    v = tailored_view(pkg, contact)
    if item == "letter":
        _check(pkg, accept_flags)
    if item == "one_page":
        v = fit_one_page(v, pkg.scheme or "graphite")[1]
    doc = Document()
    for s in doc.sections:
        s.left_margin = s.right_margin = Cm(2)
        s.top_margin = s.bottom_margin = Cm(1.6)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Arial", Pt(10.5)
    gray = RGBColor(0x55, 0x55, 0x55)

    def head(text: str, size: float = 12):
        p = doc.add_paragraph()
        run = p.add_run(text)
        run.bold, run.font.size = True, Pt(size)
        p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(8), Pt(2)
        return p

    def line(text: str, color=None, bold=False):
        p = doc.add_paragraph()
        run = p.add_run(text)
        run.bold = bold
        if color:
            run.font.color.rgb = color
        p.paragraph_format.space_after = Pt(2)
        return p

    if v["name"]:
        head(v["name"], 18)
    if item == "letter":
        if v["contacts"]:
            line(v["contacts"], gray)
        if position or company:
            line(" · ".join(x for x in (position, company) if x and x != NO_COMPANY), gray)
        doc.add_paragraph()
        for para in v["cover_letter"].split("\n"):
            if para.strip():
                doc.add_paragraph(para.strip()).paragraph_format.space_after = Pt(6)
        if v["name"]:
            p = doc.add_paragraph(v["name"])
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    else:
        line(v["headline"], bold=True)
        if v["contacts"]:
            line(v["contacts"], gray)
        if v["summary"]:
            doc.add_paragraph(v["summary"]).paragraph_format.space_before = Pt(6)
        if v["highlights"]:
            head("Ключевые результаты")
            for h in v["highlights"]:
                doc.add_paragraph(f"{h['value']}  {h['label']}", style="List Bullet")
        head("Опыт работы")
        for e in v["experience"]:
            p = doc.add_paragraph()
            p.add_run(f"{e['role']}, {e['company']}").bold = True
            r = p.add_run(f"   {e['period']}")
            r.font.color.rgb = gray
            p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(6), Pt(2)
            for b in e["bullets"]:
                doc.add_paragraph(b, style="List Bullet")
        if v["skills"]:
            head("Навыки")
            doc.add_paragraph(", ".join(v["skills"]))
        if v["education"]:
            head("Образование")
            for x in v["education"]:
                doc.add_paragraph(x)
        if v["languages"]:
            head("Языки")
            doc.add_paragraph(", ".join(v["languages"]))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
