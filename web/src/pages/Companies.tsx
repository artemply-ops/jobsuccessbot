import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { scoreClass } from "./Vacancies";

type Company = {
  id: number; name: string; domain: string; segments: string[]; description: string | null; careers_url: string | null;
  status: string; score: number | null; reasons: string[]; open_vacancies: number; subscribed: boolean;
};

const SEGMENTS: Record<string, string> = { "": "Все", it: "IT", digital: "Digital", fintech: "Финтех", adtech: "Adtech", medtech: "Medtech" };

export default function CompaniesPage() {
  const [items, setItems] = useState<Company[]>([]);
  const [segment, setSegment] = useState("");
  const [err, setErr] = useState("");
  const [suggest, setSuggest] = useState({ name: "", domain: "", segment: "it" });
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    setItems(await api<Company[]>(`/companies${segment ? `?segment=${segment}` : ""}`));
  }, [segment]);
  useEffect(() => { load().catch((e) => setErr(e.message)); }, [load]);

  const toggle = async (c: Company) => {
    await api(`/companies/${c.id}/subscribe`, { method: c.subscribed ? "DELETE" : "POST" });
    await load();
  };

  const send = async () => {
    try {
      const r = await api("/companies", { method: "POST", json: suggest });
      setNote(r.note);
      setSuggest({ name: "", domain: "", segment: suggest.segment });
    } catch (e: any) {
      setNote(e.message);
    }
  };

  return (
    <div className="stack">
      {err && <div className="banner error">{err}</div>}
      <div className="chips">
        {Object.entries(SEGMENTS).map(([k, v]) => (
          <button key={k} className={segment === k ? "on" : ""} onClick={() => setSegment(k)}>{v}</button>
        ))}
      </div>
      <p className="muted small">Оценка подходящести появляется после первого поиска вакансий.</p>
      {items.map((c) => (
        <div key={c.id} className="card vac">
          <div className={`score ${scoreClass(c.score)}`}>{c.score ?? "—"}</div>
          <div className="grow stack small-gap">
            <strong>{c.name}</strong>
            <span className="muted small">{c.description} · {c.segments.map((s) => SEGMENTS[s] || s).join(", ")}</span>
            {c.reasons[0] && <span className="small">{c.reasons.join("; ")}</span>}
            <div className="row gap wrap small">
              {c.careers_url ? <a href={c.careers_url} target="_blank" rel="noreferrer">Раздел вакансий</a>
                : <span className="muted">{c.status === "unverified" ? "раздел вакансий ещё не проверен" : "раздел вакансий не найден"}</span>}
              <span>Открытых у нас: {c.open_vacancies}</span>
              <button className="link" onClick={() => toggle(c)}>{c.subscribed ? "Отписаться" : "Следить за вакансиями"}</button>
            </div>
          </div>
        </div>
      ))}
      <section className="card stack">
        <h2>Нет нужной компании?</h2>
        <input placeholder="Название" value={suggest.name} onChange={(e) => setSuggest({ ...suggest, name: e.target.value })} />
        <input placeholder="Сайт, например example.ru" value={suggest.domain} onChange={(e) => setSuggest({ ...suggest, domain: e.target.value })} />
        <select value={suggest.segment} onChange={(e) => setSuggest({ ...suggest, segment: e.target.value })}>
          {Object.entries(SEGMENTS).filter(([k]) => k).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <button className="btn secondary" disabled={!suggest.name || !suggest.domain} onClick={send}>Предложить</button>
        {note && <p className="small">{note}</p>}
      </section>
    </div>
  );
}
