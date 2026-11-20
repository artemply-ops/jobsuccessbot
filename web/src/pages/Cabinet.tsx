// Разделы кабинета: мои резюме (все версии пакетов) и мои вакансии. Открыть = вернуться в мастер на этот пакет.
import { useEffect, useState } from "react";
import type { Me } from "../App";
import { api, download } from "../api";

type PkgRow = { id: number; target_id: number | null; title: string; company: string; version: number; items: string[];
  created_at: string; coverage: number | null; file_names?: Record<string, string> };
type TargetRow = { id: number; title: string; company: string; general: boolean; created_at: string;
  coverage: number | null; package_id: number | null };

const TITLES: Record<string, string> = { cv: "Резюме", one_page: "Одностраничник", letter: "Письмо" };
const FILES: Record<string, string> = { cv: "Резюме", one_page: "Одностраничник", letter: "Сопроводительное_письмо" };
const dt = (s: string) => new Date(s).toLocaleDateString("ru-RU", { day: "numeric", month: "short", year: "numeric" });

// «Запустить заново»: мастер с развилки «быстро / под вакансию». Материалы и профиль на сервере остаются
export function RestartCard() {
  return (
    <div className="card row between gap wrap restart-card">
      <div className="stack small-gap">
        <b>Новое резюме или другая вакансия</b>
        <span className="muted small">Начнём с выбора пути. Ваши материалы и профиль уже сохранены, заново загружать не нужно.</span>
      </div>
      <button className="btn gold" onClick={() => { try { localStorage.removeItem("jp_make"); } catch { /* */ } window.location.href = "/make"; }}>
        Запустить заново</button>
    </div>
  );
}

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
            {r.items.filter((it) => it in FILES).map((it) => (
              <button key={it} className="btn secondary" disabled={busy === `${r.id}${it}`} onClick={async () => {
                setBusy(`${r.id}${it}`);
                try { await download(`/packages/${r.id}/file/${it}`, r.file_names?.[it] || `${FILES[it]}_v${r.version}.pdf`); }
                catch (e: any) { setErr(e.message); } finally { setBusy(""); }
              }}>{busy === `${r.id}${it}` ? "…" : `${TITLES[it]} · PDF`}</button>
            ))}
            {r.items.includes("cv") && (
              <button className="btn secondary" disabled={busy === `${r.id}docx`} onClick={async () => {
                setBusy(`${r.id}docx`);
                try { await download(`/packages/${r.id}/file/cv?fmt=docx`, (r.file_names?.cv || `${FILES.cv}_v${r.version}.pdf`).replace(/\.pdf$/, ".docx")); }
                catch (e: any) { setErr(e.message); } finally { setBusy(""); }
              }}>{busy === `${r.id}docx` ? "…" : "Резюме · Word"}</button>
            )}
            {r.items.some((it) => it === "message" || it === "linkedin") && <span className="muted small">тексты для LinkedIn в мастере</span>}
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

// Уведомления в браузер о готовых документах, даже когда сайт закрыт
export function PushCard() {
  const [on, setOn] = useState<boolean | null>(null);
  const [msg, setMsg] = useState("");
  useEffect(() => { import("../push").then((p) => (p.pushSupported() ? p.pushEnabled().then(setOn) : setOn(null))); }, []);
  const toggle = async () => {
    const p = await import("../push");
    setMsg("");
    if (on) { await p.disablePush(); setOn(false); return; }
    const r = await p.enablePush().catch(() => "off" as const);
    if (r === "on") setOn(true);
    else setMsg(r === "denied" ? "Браузер запретил уведомления. Разрешите их в настройках сайта (значок замка в адресной строке)."
      : r === "unsupported" ? "Этот браузер не поддерживает уведомления." : "Уведомления пока не подключены.");
  };
  if (on === null) return null;
  return (
    <div className="card stack small-gap">
      <div className="row between gap wrap">
        <h2>Уведомления в браузере</h2>
        <button className={`btn ${on ? "secondary" : ""}`} onClick={toggle}>{on ? "Выключить" : "Включить"}</button>
      </div>
      <p className="muted small">Сообщим, когда документы готовы, даже если сайт закрыт. В уведомлении нет ваших данных.
        На iPhone работает, если сайт добавлен на экран «Домой».</p>
      {msg && <p className="error small">{msg}</p>}
    </div>
  );
}

// П13: стиль комментариев сервиса. Документы (резюме, письмо) всегда в деловом стиле
const TONES: [Me["tone"], string, string][] = [
  ["neutral", "Нейтрально", "Только цифры и факты"],
  ["vy", "На вы", "Вежливые живые комментарии"],
  ["ty", "На ты", "Например: «Красота: 9 из 10 пунктов залетают на позицию»"],
];

export function ToneCard({ me, onChange }: { me: Me; onChange: () => void }) {
  const [tone, setTone] = useState<Me["tone"]>(me.tone || "neutral");
  const [err, setErr] = useState("");
  const pick = async (t: Me["tone"]) => {
    const was = tone;
    setTone(t); setErr("");
    try { await api("/me/settings", { method: "PATCH", json: { tone: t } }); onChange(); }
    catch (e: any) { setTone(was); setErr(e.message); }
  };
  return (
    <div className="card stack small-gap">
      <h2>Стиль общения</h2>
      <p className="muted small">Как сервис комментирует оценки на сайте, в мини-приложении и в боте. Резюме и письма всегда в деловом стиле.</p>
      <div className="tone-pick" role="radiogroup">
        {TONES.map(([k, t, note]) => (
          <button key={k} role="radio" aria-checked={tone === k} className={tone === k ? "on" : ""} onClick={() => pick(k)}>
            <b>{t}</b><span className="muted small">{note}</span>
          </button>
        ))}
      </div>
      {err && <p className="error small">{err}</p>}
    </div>
  );
}
