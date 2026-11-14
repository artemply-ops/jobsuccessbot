// Этап 5 пивота: тот же сценарий, что в боте. Материалы -> вакансия -> уточнения -> документы -> схема -> фото -> файлы.
import { useEffect, useState } from "react";
import { api, ApiError, download, getConfig, upload, waitJob } from "../api";

type Step = "materials" | "vacancy" | "answers" | "items" | "scheme" | "photo" | "result";
type Source = { id: number; kind: string; filename: string | null; status: string; error: string | null; preview: string };
type Question = { field: string; question: string };
type Target = {
  id: number; title: string; company: string; company_domain: string | null;
  parsed: { requirements?: string[] }; questions: Question[];
};
type Scheme = { key: string; title: string; head: string; highlight: string; signal: string };
type Flag = { text: string; where: string; reason: string };
type Pkg = { id: number; version: number; items: string[]; scheme: string; flags: Flag[]; revision_left: boolean };

const ITEMS: [string, string][] = [
  ["cv", "Резюме"],
  ["one_page", "Резюме на 1 страницу"],
  ["letter", "Сопроводительное письмо"],
];
const FILE_NAMES: Record<string, string> = { cv: "Резюме", one_page: "Резюме_1_страница", letter: "Сопроводительное_письмо" };
const SAVE_KEY = "jp_make";

function load(): { step?: Step; targetId?: number; items?: string[]; scheme?: string; packageId?: number } {
  try {
    return JSON.parse(localStorage.getItem(SAVE_KEY) || "{}");
  } catch {
    return {};
  }
}

export default function MakePage() {
  const saved = load();
  const [step, setStep] = useState<Step>(saved.step || "materials");
  const [target, setTarget] = useState<Target | null>(null);
  const [items, setItems] = useState<string[]>(saved.items || ITEMS.map(([k]) => k));
  const [scheme, setScheme] = useState(saved.scheme || "graphite");
  const [packageId, setPackageId] = useState<number | undefined>(saved.packageId);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [ai, setAi] = useState(true);

  useEffect(() => { getConfig().then((c) => setAi(c.ai)).catch(() => {}); }, []);
  useEffect(() => {
    try {
      localStorage.setItem(SAVE_KEY, JSON.stringify({ step, targetId: target?.id ?? saved.targetId, items, scheme, packageId }));
    } catch { /* приватный режим */ }
  }, [step, target, items, scheme, packageId]);
  useEffect(() => {
    if (saved.targetId && !target) api<Target>(`/targets/${saved.targetId}`).then(setTarget).catch(() => setStep("vacancy"));
  }, []);

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : "Ошибка");
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="stack">
      <Progress step={step} />
      {!ai && <div className="banner">ИИ пока не подключён: материалы и вакансия сохранятся, документы соберём после подключения.</div>}
      {error && <div className="banner error">{error}</div>}
      {busy && <div className="banner">{busy}</div>}

      {step === "materials" && (
        <Materials disabled={!!busy} onNext={() => run("Собираю профиль из материалов…", async () => {
          const r = await api<{ job_id?: number }>("/profile/build", { method: "POST", json: {} });
          if (r.job_id) await waitJob(r.job_id);
          setStep("vacancy");
        })} />
      )}

      {step === "vacancy" && (
        <Vacancy disabled={!!busy} onSubmit={(url, text, company_url) => run("Читаю вакансию и сайт компании…", async () => {
          const t = await api<Target>("/targets", { method: "POST", json: { url: url || null, text: text || null, company_url: company_url || null } });
          setTarget(t);
          setPackageId(undefined);
          const q = await api<{ questions: Question[] }>(`/targets/${t.id}/questions`, { method: "POST" });
          setTarget({ ...t, questions: q.questions });
          setStep(q.questions.length ? "answers" : "items");
        })} />
      )}

      {step === "answers" && target && (
        <Answers target={target} disabled={!!busy} onSkip={() => setStep("items")}
          onSubmit={(text) => run("Записываю ответы в профиль…", async () => {
            await api(`/targets/${target.id}/answers`, { method: "POST", json: { text } });
            setStep("items");
          })} />
      )}

      {step === "items" && (
        <div className="card stack">
          <h2>Что подготовить?</h2>
          {target && <p className="muted small">{target.title} · {target.company}</p>}
          {ITEMS.map(([k, title]) => (
            <label key={k} className="check">
              <input type="checkbox" checked={items.includes(k)}
                onChange={() => setItems(items.includes(k) ? items.filter((i) => i !== k) : ITEMS.map(([x]) => x).filter((x) => x === k || items.includes(x)))} />
              {title}
            </label>
          ))}
          <div className="row gap">
            <button className="btn secondary" onClick={() => setItems(ITEMS.map(([k]) => k))}>Все три</button>
            <button className="btn" disabled={!items.length} onClick={() => setStep("scheme")}>Дальше</button>
          </div>
        </div>
      )}

      {step === "scheme" && (
        <Schemes value={scheme} onChange={setScheme}
          onNext={() => setStep(items.some((i) => i !== "letter") ? "photo" : "result")} />
      )}

      {step === "photo" && <Photo disabled={!!busy} onNext={() => setStep("result")} />}

      {step === "result" && target && (
        <Result target={target} items={items} scheme={scheme} packageId={packageId} setPackageId={setPackageId}
          busy={busy} run={run} ai={ai} onNew={() => { setTarget(null); setPackageId(undefined); setStep("vacancy"); }} />
      )}

      {step !== "materials" && step !== "result" && (
        <button className="link" onClick={() => setStep(step === "vacancy" ? "materials" : "vacancy")}>
          ← {step === "vacancy" ? "Добавить материалы" : "Другая вакансия"}
        </button>
      )}
    </div>
  );
}

const STEPS: [Step, string][] = [["materials", "О себе"], ["vacancy", "Вакансия"], ["answers", "Уточнения"],
  ["items", "Документы"], ["scheme", "Стиль"], ["photo", "Фото"], ["result", "Готово"]];

function Progress({ step }: { step: Step }) {
  const i = STEPS.findIndex(([s]) => s === step);
  return (
    <div className="steps">
      {STEPS.map(([s, t], n) => (
        <span key={s} className={n === i ? "on" : n < i ? "done" : ""} title={t}>
          <span className="num">{n < i ? "✓" : n + 1}</span>
          <span className="lbl">{t}</span>
        </span>
      ))}
    </div>
  );
}

function Materials({ onNext, disabled }: { onNext: () => void; disabled: boolean }) {
  const [sources, setSources] = useState<Source[]>([]);
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const reload = () => api<Source[]>("/sources").then(setSources).catch(() => {});
  useEffect(() => { reload(); }, []);
  const onFile = async (f: File | undefined) => {
    if (!f) return;
    setErr("");
    try {
      const s = await upload<Source>("/sources/file", f, f.name);
      if (s.status === "error") setErr(s.error || "Не удалось прочитать файл");
    } catch (e: any) {
      setErr(e.message);
    }
    reload();
  };
  const ok = sources.filter((s) => s.status === "parsed");
  return (
    <div className="card stack">
      <h2>Ваше резюме и рассказ о себе</h2>
      <p className="muted small">Файл (PDF, DOCX), скан или фото. Плюс можно рассказать о себе: чем занимались, какие результаты с цифрами.</p>
      <label className="btn secondary file">
        Загрузить файл или фото
        <input type="file" accept=".pdf,.docx,.txt,.jpg,.jpeg,.png,.webp" hidden onChange={(e) => onFile(e.target.files?.[0])} />
      </label>
      <textarea rows={4} placeholder="Расскажите о себе" value={text} onChange={(e) => setText(e.target.value)} />
      <button className="btn secondary" disabled={!text.trim()} onClick={async () => {
        await api("/sources/text", { method: "POST", json: { text } });
        setText("");
        reload();
      }}>Добавить текст</button>
      {err && <p className="error">{err}</p>}
      {ok.length > 0 && (
        <ul className="sources">
          {ok.map((s) => <li key={s.id}>{s.filename || (s.kind === "voice" ? "Голосовое" : "Текст")}: {s.preview.slice(0, 60)}…</li>)}
        </ul>
      )}
      <button className="btn" disabled={!ok.length || disabled} onClick={onNext}>Дальше</button>
    </div>
  );
}

function Vacancy({ onSubmit, disabled }: { onSubmit: (url: string, text: string, company: string) => void; disabled: boolean }) {
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [company, setCompany] = useState("");
  return (
    <div className="card stack">
      <h2>Вакансия</h2>
      <label>Ссылка на вакансию на сайте компании
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://career.company.ru/..." />
      </label>
      <label>или текст вакансии
        <textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder="Ссылки hh.ru не открываю: скопируйте текст оттуда" />
      </label>
      <label>Сайт компании (необязательно, для миссии и ценностей в письме)
        <input value={company} onChange={(e) => setCompany(e.target.value)} placeholder="company.ru" />
      </label>
      <button className="btn" disabled={disabled || (!url.trim() && !text.trim())} onClick={() => onSubmit(url.trim(), text.trim(), company.trim())}>
        Дальше
      </button>
    </div>
  );
}

function Answers({ target, onSubmit, onSkip, disabled }: { target: Target; onSubmit: (t: string) => void; onSkip: () => void; disabled: boolean }) {
  const [text, setText] = useState("");
  return (
    <div className="card stack">
      <h2>Несколько уточнений</h2>
      <p className="muted small">{target.title} · {target.company}</p>
      <ol>{target.questions.map((q) => <li key={q.field + q.question}>{q.question}</li>)}</ol>
      <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} placeholder="Ответьте своими словами, можно коротко" />
      <div className="row gap">
        <button className="btn secondary" onClick={onSkip}>Пропустить</button>
        <button className="btn" disabled={disabled || !text.trim()} onClick={() => onSubmit(text)}>Дальше</button>
      </div>
    </div>
  );
}

function Schemes({ value, onChange, onNext }: { value: string; onChange: (k: string) => void; onNext: () => void }) {
  const [list, setList] = useState<Scheme[]>([]);
  useEffect(() => { api<Scheme[]>("/schemes").then(setList).catch(() => {}); }, []);
  return (
    <div className="card stack">
      <h2>Цветовая схема</h2>
      <div className="schemes">
        {list.map((s) => (
          <button key={s.key} className={`scheme ${s.key === value ? "on" : ""}`} onClick={() => onChange(s.key)}>
            <span className="sheet">
              <span className="stripe" style={{ background: s.signal }} />
              <span className="head" style={{ background: s.head }}>
                <span className="nm" />
                <span className="hl" style={{ background: s.highlight }} />
              </span>
              <span className="body" />
            </span>
            <span className="small">{s.title.split(" (")[0]}</span>
          </button>
        ))}
      </div>
      <button className="btn" onClick={onNext}>Дальше</button>
    </div>
  );
}

function Photo({ onNext, disabled }: { onNext: () => void; disabled: boolean }) {
  const [err, setErr] = useState("");
  const [has, setHas] = useState(false);
  const put = async (f: File | undefined) => {
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f, f.name);
    try {
      await api("/contact/photo", { method: "PUT", body: fd });
      setHas(true);
      setErr("");
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <div className="card stack">
      <h2>Фото для резюме</h2>
      <p className="muted small">Необязательно. Фото хранится у нас и в ИИ не отправляется.</p>
      <label className="btn secondary file">
        {has ? "Фото загружено, заменить" : "Загрузить фото"}
        <input type="file" accept="image/*" hidden onChange={(e) => put(e.target.files?.[0])} />
      </label>
      {err && <p className="error">{err}</p>}
      <div className="row gap">
        <button className="btn secondary" disabled={disabled} onClick={async () => { await api("/contact/photo", { method: "DELETE" }); onNext(); }}>
          Без фото
        </button>
        <button className="btn" disabled={disabled} onClick={onNext}>{has ? "Дальше" : "Оставить прежнее"}</button>
      </div>
    </div>
  );
}

function Result(props: {
  target: Target; items: string[]; scheme: string; packageId?: number; setPackageId: (id: number) => void;
  busy: string; run: (label: string, fn: () => Promise<void>) => Promise<void>; ai: boolean; onNew: () => void;
}) {
  const { target, items, scheme, packageId, setPackageId, busy, run, ai, onNew } = props;
  const [pkg, setPkg] = useState<Pkg | null>(null);
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const [accept, setAccept] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [schemes, setSchemes] = useState<Scheme[]>([]);

  const generate = (instr?: string) => run(instr ? "Вношу правку и перепроверяю факты…" : "Пишу документы и проверяю каждый факт. Обычно 1-2 минуты…", async () => {
    const r = await api<{ job_id?: number; stub?: boolean; message?: string }>(`/targets/${target.id}/package`, {
      method: "POST", json: { items, scheme: pkg?.scheme || scheme, instruction: instr || null } });
    if (r.stub) throw new Error(r.message || "ИИ не подключён");
    const j = await waitJob(r.job_id!);
    setPackageId(j.result.package_id);
    setInstruction("");
  });

  const loadPkg = async (id: number) => {
    const p = await api<Pkg>(`/packages/${id}`);
    setPkg(p);
    const out: Record<string, string> = {};
    for (const it of p.items) {
      const letterBlocked = it === "letter" && p.flags.some((f) => f.where !== "cv");
      if (letterBlocked) continue;
      try {
        const b = await api<Blob>(`/packages/${id}/file/${it}?preview=true`);
        out[it] = URL.createObjectURL(b);
      } catch { /* превью необязательно */ }
    }
    setPreviews(out);
  };

  useEffect(() => { api<Scheme[]>("/schemes").then(setSchemes).catch(() => {}); }, []);
  useEffect(() => {  // письмо с неподтверждёнными фактами показываем только после явного согласия
    if (!accept || !pkg?.items.includes("letter")) return;
    api<Blob>(`/packages/${pkg.id}/file/letter?preview=true&accept_flags=true`)
      .then((b) => setPreviews((p) => ({ ...p, letter: URL.createObjectURL(b) }))).catch(() => {});
  }, [accept, pkg]);
  useEffect(() => { if (packageId) loadPkg(packageId).catch(() => {}); else if (ai && !busy) generate(); }, [packageId]);

  if (!pkg && !ai && !busy) {
    return (
      <div className="card stack">
        <p>Всё сохранено: материалы, вакансия, выбор документов и стиль. Документы пишет ИИ, а он пока не подключён.
          Вернитесь сюда после подключения, и пакет соберётся с этого шага.</p>
        <button className="btn secondary" onClick={onNew}>Другая вакансия</button>
      </div>
    );
  }
  if (!pkg) return <div className="card muted">{busy || "Готовлю документы…"}</div>;
  const cvFlags = pkg.flags.filter((f) => f.where === "cv");
  const letterFlags = pkg.flags.filter((f) => f.where !== "cv");
  return (
    <div className="stack">
      <div className="card stack">
        <h2>{target.title} · {target.company}</h2>
        {cvFlags.length > 0 && (
          <div className="banner">Убрал из резюме то, что не подтверждается вашим профилем:
            <ul>{cvFlags.map((f) => <li key={f.text}>{f.text}</li>)}</ul></div>
        )}
        <div className="previews">
          {pkg.items.map((it) => (
            <div key={it} className="preview">
              {previews[it] ? <img src={previews[it]} alt="" /> : (
                <div className="ph muted small">
                  {it === "letter" && letterFlags.length > 0 && !accept ? "Письмо ждёт подтверждения" : "Превью готовится"}
                </div>
              )}
              <button className="btn" disabled={it === "letter" && letterFlags.length > 0 && !accept}
                onClick={() => download(`/packages/${pkg.id}/file/${it}${it === "letter" && accept ? "?accept_flags=true" : ""}`,
                  `${FILE_NAMES[it]}_v${pkg.version}.pdf`)}>
                {ITEMS.find(([k]) => k === it)?.[1]} · PDF
              </button>
            </div>
          ))}
        </div>
        {letterFlags.length > 0 && (
          <div className="banner error">В письме есть утверждения, которых нет в вашем профиле:
            <ul>{letterFlags.map((f) => <li key={f.text}>{f.text} — {f.reason}</li>)}</ul>
            <label className="check"><input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} />
              Это правда, разрешаю скачать письмо</label>
          </div>
        )}
      </div>

      <div className="card stack">
        <h2>Цветовая схема</h2>
        <div className="row gap wrap">
          {schemes.map((s) => (
            <button key={s.key} className={`btn ${s.key === pkg.scheme ? "" : "secondary"}`} disabled={!!busy}
              onClick={() => run("Перерисовываю…", async () => {
                await api(`/packages/${pkg.id}`, { method: "PATCH", json: { scheme: s.key } });
                await loadPkg(pkg.id);
              })}>{s.title.split(" (")[0]}</button>
          ))}
        </div>
      </div>

      {pkg.revision_left && (
        <div className="card stack">
          <h2>Одна правка</h2>
          <textarea rows={3} value={instruction} onChange={(e) => setInstruction(e.target.value)}
            placeholder="Например: короче summary, акцент на B2B, письмо менее формально" />
          <button className="btn" disabled={!instruction.trim() || !!busy} onClick={() => generate(instruction.trim())}>Внести правку</button>
        </div>
      )}
      <button className="btn secondary" onClick={onNew}>Новая вакансия</button>
    </div>
  );
}
