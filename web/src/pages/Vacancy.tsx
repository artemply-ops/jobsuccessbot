import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api, download, waitJob } from "../api";
import { money, scoreClass, type Vac } from "./Vacancies";

type VacFull = Vac & { description: string; packages: { id: number; version: number }[] };
type Flag = { text: string; where: string; reason: string };
type Pkg = {
  id: number; version: number; flags: Flag[]; name: string; contacts: string; headline: string; summary: string;
  skills: string[]; experience: { company: string; role: string; period: string; bullets: string[] }[];
  education: string[]; languages: string[]; cover_letter: string; recruiter_message: string;
};

const LABELS: Record<string, string> = {
  skills: "Навыки", experience: "Опыт", level: "Уровень", industry: "Отрасль", money: "Деньги", format: "Формат",
};

export default function VacancyPage() {
  const { id } = useParams();
  const [v, setV] = useState<VacFull | null>(null);
  const [pkg, setPkg] = useState<Pkg | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [instruction, setInstruction] = useState("");
  const [tab, setTab] = useState<"cv" | "letter" | "msg">("letter");

  const load = useCallback(async () => {
    const data = await api<VacFull>(`/vacancies/${id}`);
    setV(data);
    if (data.packages[0]) setPkg(await api<Pkg>(`/packages/${data.packages[0].id}`));
  }, [id]);
  useEffect(() => { load().catch((e) => setErr(e.message)); }, [load]);

  const generate = async (instr?: string) => {
    setBusy("Собираю досье компании и пишу CV с письмом. Обычно 1-2 минуты…");
    setErr("");
    try {
      const r = await api(`/vacancies/${id}/package`, { method: "POST", json: { instruction: instr || null } });
      if (r.stub) throw new Error(`🤖 ${r.message} Пока можно откликнуться своим резюме по ссылке «Открыть у источника».`);
      const j = await waitJob(r.job_id);
      setPkg(await api<Pkg>(`/packages/${j.result.package_id}`));
      setInstruction("");
      await load();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  };

  const dl = async (fmt: "docx" | "pdf", accept = false) => {
    if (!pkg) return;
    try {
      await download(`/packages/${pkg.id}/${fmt}${accept ? "?accept_flags=true" : ""}`, `CV_${v?.id}_v${pkg.version}.${fmt}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 &&
        confirm(`${e.message}\n\nСкачать всё равно? Вы подтверждаете, что эти утверждения верны.`)) {
        await dl(fmt, true);
      } else if (!(e instanceof ApiError && e.status === 409)) setErr((e as Error).message);
    }
  };

  if (!v) return <div className="muted">{err || "Загрузка…"}</div>;
  return (
    <div className="stack">
      <Link to="/vacancies" className="link">← Все вакансии</Link>
      {busy && <div className="banner">{busy}</div>}
      {err && <div className="banner error">{err}</div>}

      <section className="card stack">
        <div className="row gap">
          <div className={`score big ${scoreClass(v.score)}`}>{v.score ?? "—"}</div>
          <div className="grow stack small-gap">
            <h2>{v.title}</h2>
            <span className="muted">{v.company_name} · {v.city || "город не указан"}{v.remote ? " · удалёнка" : ""}</span>
            {money(v) && <span>{money(v)}</span>}
          </div>
        </div>
        {Object.keys(v.breakdown).length > 0 && (
          <div className="bars">
            {Object.entries(v.breakdown).map(([k, val]) => (
              <div key={k} className="bar"><span>{LABELS[k] || k}</span><div><i style={{ width: `${val}%` }} /></div><b>{val}</b></div>
            ))}
          </div>
        )}
        {v.pros.map((p, i) => <div key={`p${i}`} className="small">✅ {p}</div>)}
        {v.gaps.map((g, i) => <div key={`g${i}`} className="small">⚠️ {g}</div>)}
        <details><summary>Описание вакансии</summary><p className="pre">{v.description}</p></details>
        <div className="row gap wrap">
          <a className="btn secondary" href={v.url} target="_blank" rel="noreferrer">Открыть у источника</a>
          <span className="muted small">Источники: {v.sources.join(", ")}</span>
        </div>
      </section>

      <section className="card stack">
        <h2>Пакет под вакансию</h2>
        {!pkg && <button className="btn" disabled={!!busy} onClick={() => generate()}>Сделать CV и сопроводительное</button>}
        {pkg && (
          <>
            <div className="row gap">
              <span className="muted small grow">Версия {pkg.version}</span>
              <button className="link" disabled={!!busy} onClick={() => generate()}>Пересобрать с нуля</button>
            </div>
            {pkg.flags.length > 0 && (
              <div className="banner warn">
                <strong>Не подтверждается профилем:</strong>
                <ul>{pkg.flags.map((f, i) => <li key={i}>{f.text} <span className="muted">— {f.reason}</span></li>)}</ul>
                Пункты CV с такими утверждениями в файл не попадут. Если это правда, добавьте факт в профиль.
              </div>
            )}
            <div className="tabs">
              <button className={tab === "letter" ? "on" : ""} onClick={() => setTab("letter")}>Письмо</button>
              <button className={tab === "cv" ? "on" : ""} onClick={() => setTab("cv")}>CV</button>
              <button className={tab === "msg" ? "on" : ""} onClick={() => setTab("msg")}>Рекрутеру</button>
            </div>
            {tab === "letter" && <p className="pre">{pkg.cover_letter}</p>}
            {tab === "msg" && (
              <div className="stack small-gap">
                <p className="pre">{pkg.recruiter_message}</p>
                <button className="btn secondary" onClick={() => navigator.clipboard.writeText(pkg.recruiter_message)}>Скопировать</button>
              </div>
            )}
            {tab === "cv" && <CvView p={pkg} />}
            <div className="row gap wrap">
              <button className="btn" onClick={() => dl("docx")}>Скачать DOCX</button>
              <button className="btn secondary" onClick={() => dl("pdf")}>Скачать PDF</button>
            </div>
            <textarea rows={2} placeholder="Правка: например «письмо короче и увереннее» или «добавь акцент на финтех»"
              value={instruction} onChange={(e) => setInstruction(e.target.value)} />
            <button className="btn secondary" disabled={!instruction.trim() || !!busy} onClick={() => generate(instruction)}>
              Применить правку
            </button>
          </>
        )}
      </section>
    </div>
  );
}

function CvView({ p }: { p: Pkg }) {
  const flagged = new Set(p.flags.map((f) => f.text.trim()));
  return (
    <div className="cv stack small-gap">
      {p.name && <h3>{p.name}</h3>}
      <strong>{p.headline}</strong>
      {p.contacts && <span className="muted small">{p.contacts}</span>}
      <p>{p.summary}</p>
      <div><b>Навыки: </b>{p.skills.join(", ")}</div>
      {p.experience.map((e, i) => (
        <div key={i}>
          <b>{e.role} — {e.company}</b> <span className="muted">{e.period}</span>
          <ul>{e.bullets.map((b, j) => <li key={j} className={flagged.has(b.trim()) ? "flagged" : ""}>{b}</li>)}</ul>
        </div>
      ))}
      {p.education.length > 0 && <div><b>Образование: </b>{p.education.join("; ")}</div>}
      {p.languages.length > 0 && <div><b>Языки: </b>{p.languages.join(", ")}</div>}
    </div>
  );
}
