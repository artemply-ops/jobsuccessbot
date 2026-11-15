// Разделы кабинета: мои резюме (все версии пакетов) и мои вакансии. Открыть = вернуться в мастер на этот пакет.
import { useEffect, useState } from "react";
import { api, download } from "../api";

type PkgRow = { id: number; target_id: number | null; title: string; company: string; version: number; items: string[];
  created_at: string; coverage: number | null };
type TargetRow = { id: number; title: string; company: string; general: boolean; created_at: string;
  coverage: number | null; package_id: number | null };

const TITLES: Record<string, string> = { cv: "Резюме", one_page: "Одностраничник", letter: "Письмо" };
const FILES: Record<string, string> = { cv: "Резюме", one_page: "Одностраничник", letter: "Сопроводительное_письмо" };
const dt = (s: string) => new Date(s).toLocaleDateString("ru-RU", { day: "numeric", month: "short", year: "numeric" });

// Мастер восстанавливает шаг из localStorage (pages/Make.tsx, ключ jp_make): кладём туда нужный пакет и переходим
function openInMaster(targetId: number, packageId: number | null, general: boolean) {
  try {
    localStorage.setItem("jp_make", JSON.stringify({ mode: general ? "quick" : "target", step: packageId ? "result" : "items",
      targetId, packageId: packageId ?? undefined }));
  } catch { /* приватный режим */ }
  window.location.href = "/make";
}

export function MyPackages() {
  const [rows, setRows] = useState<PkgRow[] | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => { api<PkgRow[]>("/packages").then(setRows).catch((e) => setErr(e.message)); }, []);
  if (err) return <div className="banner error">{err}</div>;
  if (!rows) return <div className="card"><span className="spinner" /></div>;
  if (!rows.length) return <div className="card stack"><p>Резюме пока нет.</p><a className="btn" href="/make">Сделать резюме</a></div>;
  return (
    <div className="stack">
      {rows.map((r) => (
        <div key={r.id} className="card stack small-gap">
          <div className="row between gap wrap">
            <b>{r.company ? `${r.title} · ${r.company}` : `Резюме: ${r.title}`}</b>
            <span className="muted small">{dt(r.created_at)} · версия {r.version}{r.coverage != null ? ` · покрытие ${r.coverage}%` : ""}</span>
          </div>
          <div className="row gap wrap">
            {r.items.map((it) => (
              <button key={it} className="btn secondary" disabled={busy === `${r.id}${it}`} onClick={async () => {
                setBusy(`${r.id}${it}`);
                try { await download(`/packages/${r.id}/file/${it}`, `${FILES[it]}_v${r.version}.pdf`); }
                catch (e: any) { setErr(e.message); } finally { setBusy(""); }
              }}>{busy === `${r.id}${it}` ? "…" : `${TITLES[it]} · PDF`}</button>
            ))}
            {r.target_id && <button className="link" onClick={() => openInMaster(r.target_id!, r.id, !r.company)}>Открыть и править →</button>}
          </div>
        </div>
      ))}
    </div>
  );
}

export function MyTargets() {
  const [rows, setRows] = useState<TargetRow[] | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { api<TargetRow[]>("/targets").then(setRows).catch((e) => setErr(e.message)); }, []);
  if (err) return <div className="banner error">{err}</div>;
  if (!rows) return <div className="card"><span className="spinner" /></div>;
  const list = rows.filter((r) => !r.general);
  if (!list.length) return <div className="card stack"><p>Вакансий пока нет.</p><a className="btn" href="/make">Подготовиться к вакансии</a></div>;
  return (
    <div className="stack">
      {list.map((r) => (
        <div key={r.id} className="card row between gap wrap">
          <div className="stack small-gap">
            <b>{r.title}{r.company ? ` · ${r.company}` : ""}</b>
            <span className="muted small">{dt(r.created_at)}{r.coverage != null ? ` · опыт закрывает ${r.coverage}% требований` : ""}
              {" · "}{r.package_id ? "документы собраны" : "документы не собраны"}</span>
          </div>
          <button className="btn secondary" onClick={() => openInMaster(r.id, r.package_id, false)}>
            {r.package_id ? "Открыть" : "Собрать документы"}</button>
        </div>
      ))}
    </div>
  );
}
