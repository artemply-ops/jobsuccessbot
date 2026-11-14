// Этап 5 пивота: тот же сценарий, что в боте. Материалы -> вакансия -> уточнения -> документы -> схема -> фото -> файлы.
import { useEffect, useRef, useState } from "react";
import { api, ApiError, download, getConfig, inTelegram, upload, waitJob } from "../api";

type Step = "materials" | "vacancy" | "answers" | "items" | "scheme" | "photo" | "result";
type Source = { id: number; kind: string; filename: string | null; status: string; error: string | null; preview: string };
type Question = { field: string; question: string };
type Target = {
  id: number; title: string; company: string; company_domain: string | null;
  parsed: { requirements?: string[] }; questions: Question[]; coverage?: Cov | null;
};
type Scheme = { key: string; title: string; head: string; highlight: string; signal: string };
type Flag = { text: string; where: string; reason: string };
type Req = { requirement: string; status: "covered" | "partial" | "missing"; evidence: string; in_profile: boolean; question: string };
type Cov = { requirements: Req[]; req_covered: number; req_partial: number; req_total: number;
  keywords_covered: string[]; keywords_missing: string[]; kw_total: number; score: number };
type Pkg = { id: number; version: number; items: string[]; scheme: string; flags: Flag[]; revision_left: boolean;
  improve_left: number; coverage: Cov | null; coverage_before: Cov | null };
type ReviewRes = { score: number; checks: { key: string; title: string; ok: boolean; note: string }[];
  advice: { problem: string; fix: string }[] };

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
    // шаг после вакансии, а вакансии нет (очистили данные, другой браузер): иначе пустой экран
    else if (!saved.targetId && !["materials", "vacancy"].includes(step)) setStep("vacancy");
  }, []);

  // busy: этапы ожидания; Working показывает их по очереди с анимацией и секундами
  const run = async (stages: string | string[], fn: () => Promise<void>, est?: number) => {
    setBusy((Array.isArray(stages) ? stages.join("|") : stages) + (est ? `#${est}` : ""));
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
      {error && !busy && <div className="banner error">{error}</div>}
      {busy && <Working key={busy} stages={busy.split("#")[0].split("|")} est={Number(busy.split("#")[1]) || undefined} />}

      {/* На время обработки форма шага скрыта, но не размонтирована: при ошибке введённое на месте */}
      <div className="stack" hidden={!!busy}>
      {step === "materials" && (
        <Materials disabled={!!busy} onNext={() => run(["Читаю ваши материалы", "Выделяю опыт и достижения", "Собираю профиль"], async () => {
          const r = await api<{ job_id?: number }>("/profile/build", { method: "POST", json: {} });
          if (r.job_id) await waitJob(r.job_id);
          setStep("vacancy");
        })} />
      )}

      {step === "vacancy" && (
        <Vacancy disabled={!!busy} onSubmit={(url, text, company_url) => run(["Читаю вакансию", "Выделяю требования", "Смотрю сайт компании", "Сравниваю с вашим профилем", "Формулирую уточнения"], async () => {
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
          onSubmit={(text) => run(["Добавляю ответы в профиль", "Обновляю профиль"], async () => {
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
    </div>
  );
}

const STEPS: [Step, string][] = [["materials", "О себе"], ["vacancy", "Вакансия"], ["answers", "Уточнения"],
  ["items", "Документы"], ["scheme", "Стиль"], ["photo", "Фото"], ["result", "Готово"]];

function Progress({ step }: { step: Step }) {
  const i = STEPS.findIndex(([s]) => s === step);
  return (
    <div className="steps-wrap"><div className="steps">
      {STEPS.map(([s, t], n) => (
        <span key={s} className={n === i ? "on" : n < i ? "done" : ""} title={t}>
          <span className="num">{n < i ? "✓" : n + 1}</span>
          <span className="lbl">{t}</span>
        </span>
      ))}
    </div></div>
  );
}

// Экран ожидания: вместо формы шага. Этапы по очереди (галочки), анимация, секунды; после ответа сразу следующий шаг.
// est: типичная длительность в секундах (замер на живых прогонах), по ней темп смены этапов
function Working({ stages, est }: { stages: string[]; est?: number }) {
  const [sec, setSec] = useState(0);
  useEffect(() => {
    window.scrollTo({ top: 0 });
    const t = setInterval(() => setSec((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);
  const pace = est ? Math.max(3, Math.round(est / stages.length)) : 4;
  const i = Math.min(Math.floor(sec / pace), stages.length - 1);
  return (
    <div className="card wait" role="status" aria-live="polite">
      <div className="wait-orb"><span /><span /><span /></div>
      <h2 className="wait-title" key={i}>{stages[i]}…</h2>
      {stages.length > 1 && (
        <ol className="wait-steps">
          {stages.map((s, n) => (
            <li key={s} className={n < i ? "done" : n === i ? "on" : ""}><span className="mk">{n < i ? "✓" : ""}</span>{s}</li>
          ))}
        </ol>
      )}
      <div className="wbar"><span /></div>
      <p className="muted small hint">{sec} с{est ? ` · обычно около ${est} с` : ""}. Ничего нажимать не нужно: как закончу, сразу перейду дальше.</p>
    </div>
  );
}

function NameField() {
  // Имя угадывается по резюме и может ошибиться: показываем и даём поправить до генерации
  const [name, setName] = useState("");
  const [saved, setSaved] = useState("");
  useEffect(() => {
    api<{ contact: { full_name: string | null } | null }>("/me")
      .then((m) => { setName(m.contact?.full_name || ""); setSaved(m.contact?.full_name || ""); }).catch(() => {});
  }, []);
  const save = async () => {
    await api("/contact", { method: "PUT", json: { full_name: name.trim() } });
    setSaved(name.trim());
  };
  return (
    <label>Имя в шапке резюме
      <div className="row gap">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Имя Фамилия" />
        <button className="btn secondary" disabled={!name.trim() || name.trim() === saved} onClick={save}>
          {name.trim() && name.trim() === saved ? "✓" : "Сохранить"}
        </button>
      </div>
    </label>
  );
}

function Materials({ onNext, disabled }: { onNext: () => void; disabled: boolean }) {
  const [sources, setSources] = useState<Source[]>([]);
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const [nameKey, setNameKey] = useState(0);  // после загрузки файла имя могло определиться: перечитываем
  const reload = () => api<Source[]>("/sources").then(setSources).then(() => setNameKey((k) => k + 1)).catch(() => {});
  useEffect(() => { reload(); }, []);
  const [wait, setWait] = useState<string[] | null>(null);  // экран ожидания вместо формы
  const onFile = async (f: File | undefined) => {
    if (!f) return;
    setErr("");
    setWait(["Загружаю файл", "Читаю текст", "Убираю персональные данные"]);
    try {
      const s = await upload<Source>("/sources/file", f, f.name);
      if (s.status === "error") setErr(s.error || "Не удалось прочитать файл");
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setWait(null);
    }
    reload();
  };
  const ok = sources.filter((s) => s.status === "parsed");
  return (
    <>
    {wait && <Working stages={wait} />}
    <div className="card stack" hidden={!!wait}>
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
      {ok.length > 0 && <NameField key={nameKey} />}
      {ok.length > 0 && <ReviewBox onWait={setWait} />}
      <button className="btn" disabled={!ok.length || disabled} onClick={onNext}>Дальше</button>
    </div>
    </>
  );
}

type Check = { ok: boolean; reason?: string; title?: string };

function Vacancy({ onSubmit, disabled }: { onSubmit: (url: string, text: string, company: string) => void; disabled: boolean }) {
  // Отдельный шаг проверки ссылки: читается ли страница. Нет: объясняем почему и просим вставить текст.
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [company, setCompany] = useState("");
  const [check, setCheck] = useState<Check | null>(null);
  const [checking, setChecking] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const doCheck = async () => {
    setChecking(true);
    setCheck(null);
    try {
      const c = await api<Check>("/targets/check", { method: "POST", json: { url: url.trim() } });
      setCheck(c);
      if (!c.ok) setTimeout(() => textRef.current?.focus(), 50);
    } catch (e: any) {
      setCheck({ ok: false, reason: e.message });
    } finally {
      setChecking(false);
    }
  };
  const useUrl = !!url.trim() && check?.ok && !text.trim();
  return (
    <>
    {checking && <Working stages={["Открываю ссылку", "Проверяю, читается ли страница"]} />}
    <div className="card stack" hidden={checking}>
      <h2>Вакансия</h2>
      <label>Ссылка на вакансию на сайте компании
        <div className="row gap">
          <input value={url} onChange={(e) => { setUrl(e.target.value); setCheck(null); }} placeholder="https://career.company.ru/..." />
          <button className="btn secondary" disabled={!url.trim() || checking} onClick={doCheck}>
            {checking ? "Проверяю…" : "Проверить"}
          </button>
        </div>
      </label>
      {check?.ok && <div className="banner ok">✓ Страница читается{check.title ? `: «${check.title}»` : ""}</div>}
      {check && !check.ok && (
        <div className="banner error">
          Не получилось прочитать страницу: {check.reason} <b>Скопируйте текст вакансии и вставьте его ниже.</b>
        </div>
      )}
      <label>{check && !check.ok ? "Текст вакансии" : "или текст вакансии"}
        <textarea ref={textRef} rows={6} value={text} onChange={(e) => setText(e.target.value)}
          className={check && !check.ok ? "attention" : ""} placeholder="Ссылки hh.ru не открываю: скопируйте текст оттуда" />
      </label>
      <label>Сайт компании (необязательно, для миссии и ценностей в письме)
        <input value={company} onChange={(e) => setCompany(e.target.value)} placeholder="company.ru" />
      </label>
      {url.trim() && !check && !text.trim() && <p className="muted small">Сначала проверьте ссылку.</p>}
      <button className="btn" disabled={disabled || !(useUrl || text.trim())}
        onClick={() => onSubmit(useUrl ? url.trim() : (check?.ok ? url.trim() : ""), text.trim(), company.trim())}>
        Дальше
      </button>
    </div>
    </>
  );
}

function Answers({ target, onSubmit, onSkip, disabled }: { target: Target; onSubmit: (t: string) => void; onSkip: () => void; disabled: boolean }) {
  const [text, setText] = useState("");
  return (
    <div className="card stack">
      <h2>Несколько уточнений</h2>
      <p className="muted small">{target.title} · {target.company}</p>
      {target.coverage && target.coverage.req_total > 0 && (
        <div className="banner">📊 Ваше резюме сейчас закрывает <b>{target.coverage.req_covered} из {target.coverage.req_total}</b> требований
          вакансии{target.coverage.req_partial ? `, частично ещё ${target.coverage.req_partial}` : ""}. После генерации покажу, сколько стало.</div>
      )}
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
  const [preview, setPreview] = useState<string | null>(null);  // то, что реально попадёт в резюме
  const [fresh, setFresh] = useState(false);
  useEffect(() => {  // прежнее фото с сервера, если есть
    api<Blob>("/contact/photo").then((b) => setPreview(URL.createObjectURL(b))).catch(() => setPreview(null));
  }, []);
  const put = async (f: File | undefined) => {
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f, f.name);
    try {
      await api("/contact/photo", { method: "PUT", body: fd });
      setPreview(URL.createObjectURL(f));
      setFresh(true);
      setErr("");
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <div className="card stack">
      <h2>Фото для резюме</h2>
      <p className="muted small">Необязательно. Фото хранится у нас и в ИИ не отправляется.</p>
      {preview ? (
        <div className="photo-row">
          <img className="photo-preview" src={preview} alt="Фото для резюме" />
          <div className="stack small-gap">
            <b>{fresh ? "Фото загружено" : "Сейчас в резюме это фото"}</b>
            <span className="muted small">Будет в левом верхнем углу шапки, обрежется по рамке.</span>
          </div>
        </div>
      ) : <p className="muted small">Фото пока нет.</p>}
      <label className="btn secondary file">
        {preview ? "Заменить фото" : "Загрузить фото"}
        <input type="file" accept="image/*" hidden onChange={(e) => put(e.target.files?.[0])} />
      </label>
      {err && <p className="error">{err}</p>}
      <div className="row gap">
        <button className="btn secondary" disabled={disabled} onClick={async () => { await api("/contact/photo", { method: "DELETE" }); onNext(); }}>
          Без фото
        </button>
        <button className="btn" disabled={disabled} onClick={onNext}>{preview ? "Дальше с этим фото" : "Дальше без фото"}</button>
      </div>
    </div>
  );
}

function Result(props: {
  target: Target; items: string[]; scheme: string; packageId?: number; setPackageId: (id: number) => void;
  busy: string; run: (stages: string | string[], fn: () => Promise<void>, est?: number) => Promise<void>; ai: boolean; onNew: () => void;
}) {
  const { target, items, scheme, packageId, setPackageId, busy, run, ai, onNew } = props;
  const [pkg, setPkg] = useState<Pkg | null>(null);
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const [accept, setAccept] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [schemes, setSchemes] = useState<Scheme[]>([]);
  const [sent, setSent] = useState<string>("");  // статус отправки в Telegram
  const [loadingItem, setLoadingItem] = useState<string>("");

  // Из окна Mini App скачивание работает плохо: основной путь там — прислать файлы в чат с ботом
  const sendToChat = async () => {
    setSent("sending");
    try {
      const r = await api<{ sent: number; skipped: string[] }>(`/packages/${pkg!.id}/send`, {
        method: "POST", json: { accept_flags: accept } });
      setSent(`✓ Отправил в чат с ботом: ${r.sent} ${r.sent === 1 ? "файл" : r.sent < 5 ? "файла" : "файлов"}`
        + (r.skipped.length ? ". Письмо ждёт подтверждения" : ""));
    } catch (e: any) {
      setSent(`⚠️ ${e.message}`);
    }
  };
  const downloadItem = async (it: string) => {
    setLoadingItem(it);
    try {
      await download(`/packages/${pkg!.id}/file/${it}${it === "letter" && accept ? "?accept_flags=true" : ""}`,
        `${FILE_NAMES[it]}_v${pkg!.version}.pdf`);
    } finally {
      setLoadingItem("");
    }
  };

  const generate = (instr?: string) => run(instr
    ? ["Вношу правку", "Перепроверяю факты", "Собираю PDF"]
    : ["Изучаю компанию", "Пишу резюме под вакансию", "Пишу письмо", "Проверяю каждый факт по профилю", "Собираю PDF"], async () => {
    const r = await api<{ job_id?: number; stub?: boolean; message?: string }>(`/targets/${target.id}/package`, {
      method: "POST", json: { items, scheme: pkg?.scheme || scheme, instruction: instr || null } });
    if (r.stub) throw new Error(r.message || "ИИ не подключён");
    const j = await waitJob(r.job_id!);
    setPackageId(j.result.package_id);
    setInstruction("");
  }, 45);  // живой прогон: около 47 с на пакет

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
  if (!pkg) return busy ? null : <Working stages={["Загружаю документы"]} />;
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
              <button className={`btn ${inTelegram() ? "secondary" : ""}`}
                disabled={(it === "letter" && letterFlags.length > 0 && !accept) || loadingItem === it}
                onClick={() => downloadItem(it)}>
                {loadingItem === it ? "Скачиваю…" : `${ITEMS.find(([k]) => k === it)?.[1]} · PDF`}
              </button>
            </div>
          ))}
        </div>
        <button className={`btn ${inTelegram() ? "" : "secondary"}`} disabled={sent === "sending"} onClick={sendToChat}>
          {sent === "sending" ? "Отправляю в Telegram…" : "Прислать все файлы в Telegram"}
        </button>
        {sent && sent !== "sending" && <div className={`banner ${sent.startsWith("✓") ? "ok" : "error"}`}>{sent}</div>}
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
              onClick={() => run(["Перерисовываю документы"], async () => {
                await api(`/packages/${pkg.id}`, { method: "PATCH", json: { scheme: s.key } });
                await loadPkg(pkg.id);
              })}>{s.title.split(" (")[0]}</button>
          ))}
        </div>
      </div>

      {pkg.coverage && (
        <CoverageCard cov={pkg.coverage} before={pkg.coverage_before} left={pkg.improve_left} busy={!!busy}
          onImprove={(facts) => run((facts ? ["Добавляю ваши факты в профиль"] : []).concat(
            ["Закрываю пробелы", "Проверяю каждый факт", "Считаю покрытие", "Собираю PDF"]), async () => {
            const r = await api<{ job_id?: number; stub?: boolean; message?: string }>(`/packages/${pkg.id}/improve`, {
              method: "POST", json: { facts: facts || null } });
            if (r.stub) throw new Error(r.message || "ИИ не подключён");
            const j = await waitJob(r.job_id!);
            setPackageId(j.result.package_id);
          }, 45)} />
      )}

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

// Покрытие требований вакансии: сколько закрыто, было/стало, что не покрыто и почему + перегенерация
function CoverageCard({ cov, before, left, busy, onImprove }: {
  cov: Cov; before: Cov | null; left: number; busy: boolean; onImprove: (facts: string) => void;
}) {
  const [facts, setFacts] = useState("");
  const gaps = cov.requirements.filter((r) => r.status !== "covered");
  const askFacts = gaps.filter((r) => !r.in_profile);
  const pct = cov.req_total ? Math.round(100 * (cov.req_covered + cov.req_partial / 2) / cov.req_total) : cov.score;
  return (
    <div className="card stack">
      <h2>Покрытие требований вакансии</h2>
      {cov.req_total > 0 && (
        <div className="cov-head">
          <b className="cov-num">{cov.req_covered} из {cov.req_total}</b>
          <span className="muted small">
            {cov.req_partial ? `частично ещё ${cov.req_partial}` : ""}
            {before && before.req_total ? ` · в исходном резюме было ${before.req_covered} из ${before.req_total}${before.req_partial ? `, частично ${before.req_partial}` : ""}` : ""}
          </span>
        </div>
      )}
      {cov.req_total > 0 && <div className="cov-bar"><span style={{ width: `${pct}%` }} /></div>}
      {cov.kw_total > 0 && <p className="muted small">Ключевые слова вакансии: {cov.keywords_covered.length} из {cov.kw_total}        {cov.keywords_missing.length ? ` · не встретились: ${cov.keywords_missing.slice(0, 8).join(", ")}` : ""}</p>}
      {gaps.length > 0 ? (
        <ul className="cov-list">
          {gaps.map((r) => (
            <li key={r.requirement} className={r.status}>
              <span>{r.status === "partial" ? "◐" : "○"} {r.requirement}</span>
              <span className="muted small">{r.in_profile
                ? "есть в вашем профиле, но в резюме отражено слабо: перегенерация поможет"
                : `в профиле этого нет${r.question ? `. ${r.question}` : ""}`}</span>
            </li>
          ))}
        </ul>
      ) : cov.req_total > 0 ? <p className="small">Все требования подтверждены фактами из вашего профиля.</p> : null}
      {askFacts.length > 0 && left > 0 && (
        <textarea rows={3} value={facts} onChange={(e) => setFacts(e.target.value)}
          placeholder="Если такой опыт есть, расскажите своими словами. Только правду: каждый факт проверю и добавлю в профиль" />
      )}
      {left > 0 ? (
        <button className="btn" disabled={busy} onClick={() => onImprove(facts.trim())}>
          {facts.trim() ? "Добавить факты и перегенерировать" : "Перегенерировать с учётом пробелов"}
        </button>
      ) : <p className="muted small">Перегенерация по покрытию для этой вакансии уже использована.</p>}
      <p className="muted small">Выдумывать не буду: только точнее подам ваш опыт. Это не тратит вашу правку, осталось {left}.</p>
    </div>
  );
}

// Проверка резюме без вакансии: оценка и конкретные правки
function ReviewBox({ onWait }: { onWait: (stages: string[] | null) => void }) {
  const [res, setRes] = useState<ReviewRes | null>(null);
  const [err, setErr] = useState("");
  const go = async () => {
    onWait(["Читаю резюме", "Проверяю цифры, даты и структуру", "Готовлю советы"]); setErr("");
    try { setRes(await api<ReviewRes>("/review", { method: "POST" })); }
    catch (e: any) { setErr(e.message); }
    finally { onWait(null); }
  };
  return (
    <div className="stack small-gap">
      {!res && <button className="btn secondary" onClick={go}>Проверить резюме без вакансии</button>}
      {err && <p className="error">{err}</p>}
      {res && (
        <div className="review">
          <b>Оценка резюме: {res.score} из 100</b>
          <ul className="cov-list">
            {res.checks.map((c) => <li key={c.key} className={c.ok ? "covered" : "missing"}><span>{c.ok ? "✓" : "!"} {c.title}</span>
              <span className="muted small">{c.note}</span></li>)}
          </ul>
          {res.advice.length > 0 && <><b>Что поправить</b><ol>{res.advice.map((a) => (
            <li key={a.problem}>{a.problem}<div className="muted small">→ {a.fix}</div></li>))}</ol></>}
        </div>
      )}
    </div>
  );
}
