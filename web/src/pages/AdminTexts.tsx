// Редактор текстов лендинга (только админ). Структура блоков задаётся в backend/app/services/site_texts.py,
// здесь меняются только значения. «Опубликовать» = новая версия, сайт подхватывает сразу, без выкатки.
import { useEffect, useState } from "react";
import { api } from "../api";

type Field = { key: string; label: string; type?: "line" | "text" | "items"; long?: boolean; fields?: Field[] };
type Section = { key: string; title: string; fields: Field[] };
type Data = Record<string, Record<string, any>>;
type Resp = { schema: Section[]; data: Data; version: number | null; placeholders: string[]; warnings: string[];
  history: { id: number; created_at: string; title: string }[] };

function Items({ f, value, onChange }: { f: Field; value: Record<string, string>[]; onChange: (v: Record<string, string>[]) => void }) {
  const set = (i: number, k: string, v: string) => onChange(value.map((it, j) => (j === i ? { ...it, [k]: v } : it)));
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= value.length) return;
    const next = [...value];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  return (
    <div className="stack small-gap">
      <span className="field-label">{f.label}</span>
      {value.map((it, i) => (
        <div key={i} className="text-item">
          <div className="stack small-gap grow">
            {f.fields!.map((sf) => sf.long
              ? <textarea key={sf.key} rows={2} placeholder={sf.label} value={it[sf.key] || ""} onChange={(e) => set(i, sf.key, e.target.value)} />
              : <input key={sf.key} placeholder={sf.label} value={it[sf.key] || ""} onChange={(e) => set(i, sf.key, e.target.value)}
                  className={sf.key === "icon" ? "icon-input" : ""} />)}
          </div>
          <div className="item-tools">
            <button className="link" title="Выше" onClick={() => move(i, -1)}>↑</button>
            <button className="link" title="Ниже" onClick={() => move(i, 1)}>↓</button>
            <button className="link danger-text" title="Удалить" onClick={() => onChange(value.filter((_, j) => j !== i))}>×</button>
          </div>
        </div>
      ))}
      <button className="link" onClick={() => onChange([...value, Object.fromEntries(f.fields!.map((sf) => [sf.key, ""]))])}>+ Добавить</button>
    </div>
  );
}

export default function AdminTexts() {
  const [r, setR] = useState<Resp | null>(null);
  const [data, setData] = useState<Data | null>(null);
  const [open, setOpen] = useState("hero");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => api<Resp>("/admin/texts").then((x) => { setR(x); setData(structuredClone(x.data)); }).catch((e) => setMsg(`⚠️ ${e.message}`));
  useEffect(() => { load(); }, []);
  if (!r || !data) return <div className="card">{msg || <span className="spinner" />}</div>;
  const dirty = JSON.stringify(data) !== JSON.stringify(r.data);
  const set = (sec: string, key: string, v: any) => setData({ ...data, [sec]: { ...data[sec], [key]: v } });

  const publish = async () => {
    setBusy(true); setMsg("");
    try {
      const res = await api<{ version: number; warnings: string[] }>("/admin/texts", { method: "PUT", json: { data, version: r.version } });
      setMsg(`✓ Опубликовано${res.warnings.length ? `. Обратите внимание: ${res.warnings.join("; ")}` : ""}`);
      await load();
    } catch (e: any) { setMsg(`⚠️ ${e.message}`); } finally { setBusy(false); }
  };
  const restore = async (id: number) => {
    if (!confirm("Вернуть эту версию? Текущие тексты останутся в истории.")) return;
    try { await api(`/admin/texts/restore/${id}`, { method: "POST" }); setMsg("✓ Версия возвращена"); await load(); }
    catch (e: any) { setMsg(`⚠️ ${e.message}`); }
  };

  return (
    <div className="stack admin">
      <div className="row between wrap gap">
        <h2>Тексты сайта</h2>
        <span className="row gap wrap"><a className="link" href="/site" target="_blank" rel="noreferrer">Посмотреть сайт ↗</a>
          <a className="link" href="/admin">Статистика</a><a className="link" href="/profile">← В кабинет</a></span>
      </div>
      <p className="muted small">Меняются только тексты, порядок блоков и цены здесь не правятся. Вместо цен пишите подстановки:
        {" "}{r.placeholders.map((p) => <code key={p}>{p} </code>)}, сайт подставит актуальные цены. Длинное тире лучше не использовать.</p>
      {r.warnings.length > 0 && <div className="banner warn">{r.warnings.join("; ")}</div>}

      {r.schema.map((sec) => (
        <div key={sec.key} className="card stack">
          <button className="sec-toggle" onClick={() => setOpen(open === sec.key ? "" : sec.key)}>
            <b>{sec.title}</b><span>{open === sec.key ? "−" : "+"}</span>
          </button>
          {open === sec.key && sec.fields.map((f) => (
            f.type === "items"
              ? <Items key={f.key} f={f} value={data[sec.key][f.key] || []} onChange={(v) => set(sec.key, f.key, v)} />
              : <label key={f.key}>{f.label}
                  {f.type === "text"
                    ? <textarea rows={3} value={data[sec.key][f.key] || ""} onChange={(e) => set(sec.key, f.key, e.target.value)} />
                    : <input value={data[sec.key][f.key] || ""} onChange={(e) => set(sec.key, f.key, e.target.value)} />}
                </label>
          ))}
        </div>
      ))}

      <div className="publish-bar">
        {msg && <span className={`small ${msg.startsWith("✓") ? "ok-text" : "error"}`}>{msg}</span>}
        <button className="btn secondary" disabled={!dirty || busy} onClick={() => setData(structuredClone(r.data))}>Отменить</button>
        <button className="btn gold" disabled={!dirty || busy} onClick={publish}>{busy ? "Публикую…" : "Опубликовать"}</button>
      </div>

      <div className="card stack small-gap">
        <b>История (последние 30 версий)</b>
        {!r.history.length && <span className="muted small">Пока только тексты по умолчанию.</span>}
        <ul className="small history">
          {r.history.map((h, i) => (
            <li key={h.id}>{new Date(h.created_at).toLocaleString("ru-RU")} · «{h.title.slice(0, 50)}»
              {i === 0 ? <span className="muted"> · текущая</span> : <button className="link" onClick={() => restore(h.id)}>вернуть</button>}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
