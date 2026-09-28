"""Обезличивание текста перед отправкой во внешнюю LLM и обратная подстановка.

Что заменяется:
- ФИО кандидата (с падежными формами) -> [CANDIDATE]
- email, телефоны, ссылки, СНИЛС/паспорт, адреса, даты рождения -> маркеры
- имена третьих лиц (NER natasha) -> [PERSON_n], соответствие хранится в contacts.vault

Города, компании и навыки остаются: без них профиль бесполезен, и сами по себе они не ПДн.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?<!\d)(?:\+7|8|7)[\s(\-]*\d{3}[\s)\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")
URL_RE = re.compile(r"(?:https?://|www\.|t\.me/|@)[\w\-./?=&%#:@]+", re.IGNORECASE)
SNILS_RE = re.compile(r"\b\d{3}-\d{3}-\d{3}[\s-]\d{2}\b")
PASSPORT_RE = re.compile(r"\b\d{2}\s?\d{2}\s?№?\s?\d{6}\b")
BIRTH_RE = re.compile(r"(?i)(дата рождения|родил[аи]?с[ья]|д\.р\.|born)[:\s]*[\d.\s/a-zа-я]{6,20}\d")
ADDRESS_RE = re.compile(r"(?i)\b(ул\.|улица|пр-т|проспект|пер\.|переулок|д\.|дом|кв\.|квартира)\s*[\w\-.]+(?:[,\s]+(?:д\.|дом|кв\.|к\.|корп\.|стр\.)\s*[\w\-/]+)*")

TOKEN_RE = re.compile(r"\[(CANDIDATE|EMAIL|PHONE|LINK_\d+|PERSON_\d+|ID|ADDRESS|BIRTHDATE)\]")


@dataclass
class Vault:
    """Соответствие маркер -> исходное значение. Хранится у нас, наружу не уходит."""

    items: dict[str, str] = field(default_factory=dict)

    def add(self, prefix: str, value: str) -> str:
        for k, v in self.items.items():
            if v == value and k.startswith(f"[{prefix}_"):
                return k
        n = sum(1 for k in self.items if k.startswith(f"[{prefix}_")) + 1
        token = f"[{prefix}_{n}]"
        self.items[token] = value
        return token


def _name_patterns(full_name: str | None) -> list[re.Pattern]:
    """Паттерны для частей ФИО с учётом русских падежных окончаний."""
    if not full_name:
        return []
    pats = []
    for part in full_name.split():
        part = part.strip(".,")
        if len(part) < 3:
            continue
        stem = part[:-2] if len(part) > 5 else part[:-1] if len(part) > 3 else part
        pats.append(re.compile(rf"\b{re.escape(stem)}[а-яёa-z]{{0,4}}\b", re.IGNORECASE))
    return pats


@lru_cache(maxsize=1)
def _ner():
    try:
        from natasha import Doc, NewsEmbedding, NewsNERTagger, Segmenter

        emb = NewsEmbedding()
        return Segmenter(), NewsNERTagger(emb), Doc
    except Exception:  # natasha не установлена: работаем только на регулярках
        return None


def _person_spans(text: str) -> list[tuple[int, int]]:
    ner = _ner()
    if ner is None:
        return []
    segmenter, tagger, Doc = ner
    doc = Doc(text)
    doc.segment(segmenter)
    doc.tag_ner(tagger)
    return [(s.start, s.stop) for s in doc.spans if s.type == "PER"]


def depersonalize(text: str, full_name: str | None, vault: Vault, use_ner: bool = True) -> str:
    if not text:
        return text
    out = EMAIL_RE.sub("[EMAIL]", text)
    out = URL_RE.sub(lambda m: vault.add("LINK", m.group(0)), out)
    out = SNILS_RE.sub("[ID]", out)
    out = PASSPORT_RE.sub("[ID]", out)
    out = PHONE_RE.sub("[PHONE]", out)
    out = BIRTH_RE.sub("[BIRTHDATE]", out)
    out = ADDRESS_RE.sub("[ADDRESS]", out)
    for pat in _name_patterns(full_name):
        out = pat.sub("[CANDIDATE]", out)
    out = re.sub(r"(\[CANDIDATE\][\s,]*){2,}", "[CANDIDATE] ", out)

    if use_ner:
        spans = _person_spans(out)
        for start, stop in sorted(spans, reverse=True):
            chunk = out[start:stop]
            if TOKEN_RE.search(chunk):
                continue
            out = out[:start] + vault.add("PERSON", chunk) + out[stop:]
    return out


def restore(text: str, full_name: str | None, email: str | None, phone: str | None, vault: Vault) -> str:
    """Обратная подстановка. Используется только для итоговых файлов, которые получает сам пользователь."""
    repl = {"[CANDIDATE]": full_name or "", "[EMAIL]": email or "", "[PHONE]": phone or ""}
    repl.update(vault.items)
    return TOKEN_RE.sub(lambda m: repl.get(m.group(0), m.group(0)), text)


def has_pii(text: str) -> bool:
    """Страховка перед вызовом LLM: остались ли очевидные ПДн."""
    return bool(EMAIL_RE.search(text) or PHONE_RE.search(text) or SNILS_RE.search(text))
