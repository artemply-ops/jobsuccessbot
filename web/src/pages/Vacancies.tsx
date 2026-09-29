import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, waitJob } from "../api";

export type Vac = {
  id: number; title: string; company_name: string; city: string | null; remote: boolean | null;
  salary_from: number | null; salary_to: number | null; currency: string | null; url: string; sources: string[];
  score: number | null; method: string | null; breakdown: Record<string, number>; pros: string[]; gaps: string[];
};

export const money = (v: Vac) =>
  v.salary_from || v.salary_to
    ? `${v.salary_from ? `от ${v.salary_from.toLocaleString("ru")}` : ""}${v.salary_to ? ` до ${v.salary_to.toLocaleString("ru")}` : ""} ${v.currency === "RUB" ? "₽" : v.currency || ""}`
    : "";

export const scoreClass = (s: number | null) => (s == null ? "" : s >= 75 ? "hi" : s >= 50 ? "mid" : "lo");

export default function VacanciesPage() {
  const [items, setItems] = useState<Vac[]>([]);
  const [minScore, setMinScore] = useState(0);
  const [remote, setRemote] = useState(false);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    const params = new URLSearchParams({ min_score: String(minScore) });
    if (remote) params.set("remote", "true");
    if (q) params.set("q", q);
    setItems(await api<Vac[]>(`/vacancies?${params}`));
  }, [minScore, remote, q]);
  useEffect(() => { load().catch((e) => setErr(e.message)); }, [load]);

  const search = async () => {
    setBusy("Ищу и оцениваю вакансии. Это может занять несколько минут…");
    setErr("");
    try {
      const { job_id } = await api("/search/run", { method: "POST" });
      const j = await waitJob(job_id);
      const r = j.result;
      setBusy("");
      setErr("");
      await load();
      setNote(`Новых вакансий: ${r.collect.created}, оценено: ${r.score.scored}, отсеяно фильтрами: ${r.score.filtered}.` +
        (r.score.method === "basic" ? " 🤖 Точная ИИ-оценка пока не подключена, показана предварительная по совпадению с должностью." : ""));
    } catch (e: any) {
      setErr(e.message);
      setBusy("");
    }
  };

  return (
    <div className="stack">
      {busy && <div className="banner">{busy}</div>}
      {err && <div className="banner error">{err}</div>}
      {note && <div className="banner">{note}</div>}
      <div className="row gap wrap">
        <button className="btn" onClick={search} disabled={!!busy}>Найти вакансии</button>
        <input className="grow" placeholder="Поиск по названию или компании" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="row gap wrap small">
        <label className="row gap">Оценка от {minScore}%
          <input type="range" min={0} max={100} step={5} value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} />
        </label>
        <label className="check"><input type="checkbox" checked={remote} onChange={(e) => setRemote(e.target.checked)} />Только удалёнка</label>
      </div>
      {items.length === 0 && !busy && <p className="muted">Пока пусто. Соберите профиль и нажмите «Найти вакансии».</p>}
      {items.map((v) => (
        <Link to={`/vacancies/${v.id}`} key={v.id} className="card vac">
          <div className={`score ${scoreClass(v.score)}`} title={v.method === "basic" ? "предварительная оценка без ИИ" : ""}>
            {v.score ?? "—"}{v.method === "basic" && <sup>*</sup>}
          </div>
          <div className="grow stack small-gap">
            <strong>{v.title}</strong>
            <span className="muted">{v.company_name} · {v.city || "город не указан"}{v.remote ? " · удалёнка" : ""}</span>
            {money(v) && <span>{money(v)}</span>}
            {v.pros[0] && <span className="small">✅ {v.pros[0]}</span>}
            {v.gaps[0] && v.method !== "basic" && <span className="small">⚠️ {v.gaps[0]}</span>}
          </div>
        </Link>
      ))}
    </div>
  );
}
