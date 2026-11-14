"""Pydantic-схемы. Большинство из них одновременно служат форматом структурированного вывода LLM."""

from pydantic import BaseModel, Field


# ---------- Профиль ----------

class Skill(BaseModel):
    name: str
    level: str = Field(description="basic | confident | expert")
    years: float | None = None
    source_ids: list[int] = Field(default_factory=list, description="id источников, где это упомянуто")


class Fact(BaseModel):
    text: str
    source_ids: list[int] = Field(default_factory=list)


class Experience(BaseModel):
    company: str
    role: str
    period: str = Field(description="например 2021-03 — 2024-06 или 2021 — н.в.")
    industry: str | None = None
    achievements: list[Fact] = Field(default_factory=list)


class Expectations(BaseModel):
    salary_min: int | None = Field(None, description="минимальная зарплата в месяц, на руки")
    currency: str = "RUB"
    cities: list[str] = Field(default_factory=list)
    formats: list[str] = Field(default_factory=list, description="office | hybrid | remote")
    relocation: bool | None = None


class ProfileData(BaseModel):
    headline: str = ""
    summary: str = ""
    target_roles: list[str] = Field(default_factory=list)
    seniority: str = Field("", description="junior | middle | senior | lead | head | c-level")
    skills: list[Skill] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    expectations: Expectations = Field(default_factory=Expectations)
    values: list[str] = Field(default_factory=list)
    stop_factors: list[str] = Field(default_factory=list)
    writing_style: str = Field("", description="как человек пишет о себе: тон, длина фраз, лексика")


class Question(BaseModel):
    field: str
    question: str


class ProfileExtraction(BaseModel):
    profile: ProfileData
    questions: list[Question] = Field(description="5-7 уточняющих вопросов по пробелам профиля")


# ---------- Скоринг ----------

class Breakdown(BaseModel):
    skills: int
    experience: int
    level: int
    industry: int
    money: int
    format: int


class VacancyAssessment(BaseModel):
    vacancy_id: int
    score: int = Field(description="0-100")
    breakdown: Breakdown
    pros: list[str] = Field(description="до 3 пунктов: почему подходит")
    gaps: list[str] = Field(description="до 3 пунктов: чего не хватает")


class VacancyAssessments(BaseModel):
    items: list[VacancyAssessment]


class CompanyFit(BaseModel):
    company_id: int
    score: int = Field(description="0-100")
    reasons: list[str] = Field(description="до 3 коротких причин")


class CompanyFits(BaseModel):
    items: list[CompanyFit]


# ---------- Досье и пакет ----------

class DossierData(BaseModel):
    business_area: str = ""
    summary: str = ""
    mission: str | None = None
    values: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    culture_signals: list[str] = Field(default_factory=list)
    tone_of_voice: str = ""
    sources: list[str] = Field(default_factory=list)


class CVExperience(BaseModel):
    company: str
    role: str
    period: str
    bullets: list[str]


class Highlight(BaseModel):
    value: str = Field(description="число из профиля дословно, например «180 тыс.», «+27%», «14»")
    label: str = Field(description="что это за число, 2-5 слов")


class CV(BaseModel):
    headline: str
    summary: str
    highlights: list[Highlight] = Field(default_factory=list, description="до 8 показателей, только из профиля")
    skills: list[str]
    experience: list[CVExperience]
    education: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)


class PackageDraft(BaseModel):
    cv: CV
    cover_letter: str
    recruiter_message: str = Field(description="до 500 знаков")


class FactFlag(BaseModel):
    text: str = Field(description="утверждение из CV или письма")
    where: str = Field(description="cv | cover_letter | recruiter_message")
    reason: str = Field(description="почему не подтверждается профилем")


class FactCheck(BaseModel):
    flags: list[FactFlag]


# ---------- Вакансия от пользователя и уточнения (этап 2 пивота) ----------

class VacancyParsed(BaseModel):
    title: str = Field(description="название должности")
    company_name: str = Field(description="название компании-работодателя; пусто, если не указано")
    company_site: str = Field(description="домен сайта компании, если он прямо указан в тексте, иначе пусто")
    city: str = ""
    remote: bool | None = None
    salary_from: int | None = None
    salary_to: int | None = None
    currency: str = ""
    seniority: str = Field("", description="junior | middle | senior | lead | head | c-level или пусто")
    responsibilities: list[str] = Field(default_factory=list, description="задачи, коротко, до 10")
    requirements: list[str] = Field(default_factory=list, description="обязательные требования, до 12")
    nice_to_have: list[str] = Field(default_factory=list, description="будет плюсом, до 8")
    keywords: list[str] = Field(default_factory=list, description="ключевые навыки и термины вакансии, до 15")
    language: str = Field("ru", description="язык вакансии: ru | en")


class ClarifyQuestions(BaseModel):
    questions: list[Question] = Field(description="2-3 вопроса кандидату")


# ---------- Пакет под вакансию (этап 3 пивота) ----------

PACKAGE_ITEMS = ("cv", "one_page", "letter")  # полное резюме, резюме на одну страницу, сопроводительное письмо


class TailoredCV(BaseModel):
    cv: CV


class TailoredLetter(BaseModel):
    cover_letter: str


class TailoredBoth(BaseModel):
    cv: CV
    cover_letter: str
