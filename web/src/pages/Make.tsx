// Мастер с развилкой в начале:
// quick:  резюме из опыта без вакансии. Материалы (текст, голос, файлы) -> документы -> схема -> фото -> файлы.
// target: под конкретную вакансию. Материалы -> вакансия -> уточнения -> документы -> схема -> фото -> файлы + покрытие.
import { useEffect, useRef, useState } from "react";
import { api, ApiError, download, getConfig, inTelegram, upload, waitJob, type Cfg } from "../api";
import { Paywall } from "./Billing";
import { LoginBox } from "./Auth";
import { offerPush } from "../push";
import { VoiceRecorder } from "./Profile";

type Mode = "quick" | "target";
type Step = "start" | "materials" | "vacancy" | "answers" | "items" | "scheme" | "photo" | "result";
type Source = { id: number; kind: string; filename: string | null; status: string; error: string | null; preview: string };
type Question = { field: string; question: string };
type Target = {
  id: number; title: string; company: string; company_domain: string | null;
  parsed: { requirements?: string[] }; questions: Question[]; coverage?: Cov | null; general?: boolean;
};
type Scheme = { key: string; title: string; head: string; highlight: string; signal: string };
type Flag = { text: string; where: string; reason: string };
type Req = { requirement: string; status: "covered" | "partial" | "missing"; evidence: string; in_profile: boolean; question: string };
type Cov = { requirements: Req[]; req_covered: number; req_partial: number; req_total: number;
  keywords_covered: string[]; keywords_missing: string[]; kw_total: number; score: number };
type Score = { kind: "vacancy" | "quality"; title: string; before: number | null; after: number | null };
type Pkg = { id: number; version: number; items: string[]; scheme: string; flags: Flag[]; revision_left: boolean; revision_price: number;
  improve_left: number; coverage: Cov | null; coverage_before: Cov | null; file_names?: Record<string, string>;
  docx_names?: Record<string, string>; recruiter_message?: string; linkedin_text?: string; score?: Score | null; remark?: string };
type Salary = { role?: string; n: number; scope?: string; median: number | null; p25?: number; p75?: number;
  source?: string; date?: string; caveat?: string; url?: string; reason?: string };
type ReviewRes = { score: number; checks: { key: string; title: string; ok: boolean; note: string }[];
  advice: { problem: string; fix: string }[]; remark?: string };

const ITEMS: [string, string][] = [
  ["cv", "Резюме"],
  ["one_page", "Одностраничник"],
  ["letter", "Сопроводительное письмо"],
  ["message", "Сообщение нанимающему менеджеру"],
  ["linkedin", "Профиль LinkedIn"],
];
const ITEM_NOTES: Record<string, string> = {
  message: "короткий текст для LinkedIn или почты",
  linkedin: "заголовок, «О себе», опыт и навыки, чтобы скопировать в профиль",
};
const FILE_ITEMS = ["cv", "one_page", "letter"];  // файлы PDF и Word; message и linkedin это тексты
const TARGET_ONLY = ["letter", "message"];  // без вакансии писать не о чем
const DEFAULT_ITEMS: Record<Mode, string[]> = { target: ["cv", "one_page", "letter"], quick: ["cv", "one_page"] };
const FILE_NAMES: Record<string, string> = { cv: "Резюме", one_page: "Одностраничник", letter: "Сопроводительное_письмо" };
const EXTRA_WHERE = ["recruiter_message", "linkedin"];
const isLetterFlag = (f: Flag) => f.where !== "cv" && !EXTRA_WHERE.includes(f.where);
const SAVE_KEY = "jp_make";

function load(): { mode?: Mode; step?: Step; targetId?: number; items?: string[]; scheme?: string; packageId?: number } {
  try {
    return JSON.parse(localStorage.getItem(SAVE_KEY) || "{}");
  } catch {
    return {};
  }
}

export default function MakePage({ guest = false }: { guest?: boolean }) {
  const saved = load();
  const [mode, setMode] = useState<Mode>(saved.mode || "target");
  const [step, setStep] = useState<Step>(saved.step || "start");
  const [target, setTarget] = useState<Target | null>(null);
  const [items, setItems] = useState<string[]>(saved.items || DEFAULT_ITEMS[saved.mode || "target"]);
  const [scheme, setScheme] = useState(saved.scheme || "graphite");
  const [packageId, setPackageId] = useState<number | undefined>(saved.packageId);
  const [role, setRole] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [paywall, setPaywall] = useState<string | null>(null);  // лимит закончился: что именно (package | revision)
  // Гость на сайте дошёл до сборки документов: вход, затем то же действие повторяется само
  const [needLogin, setNeedLogin] = useState<null | (() => void)>(null);
  const [ai, setAi] = useState(true);
  const quick = mode === "quick";
  const itemList = quick ? ITEMS.filter(([k]) => !TARGET_ONLY.includes(k)) : ITEMS;  // без вакансии письмо писать не о чем

  useEffect(() => { getConfig().then((c) => setAi(c.ai)).catch(() => {}); }, []);
  useEffect(() => {
    try {
      localStorage.setItem(SAVE_KEY, JSON.stringify({ mode, step, targetId: target?.id ?? saved.targetId, items, scheme, packageId }));
    } catch { /* приватный режим */ }
  }, [mode, step, target, items, scheme, packageId]);
  useEffect(() => {
    if (saved.targetId && !target) api<Target>(`/targets/${saved.targetId}`).then(setTarget).catch(() => setStep("start"));
    // шаг, которому нужна цель, а её нет (очистили данные, другой браузер): иначе пустой экран
    else if (!saved.targetId && !["start", "materials", "vacancy"].includes(step) && !(quick && step === "items"))
      setStep(quick ? "materials" : "vacancy");
  }, []);

  const choose = (m: Mode) => {
    setMode(m);
    setTarget(null);
    setPackageId(undefined);
    setItems(DEFAULT_ITEMS[m]);
    setStep("materials");
  };
  const restart = () => { setTarget(null); setPackageId(undefined); setStep("start"); };

  // busy: этапы ожидания; Working показывает их по очереди с анимацией и секундами
  const run = async (stages: string | string[], fn: () => Promise<void>, est?: number) => {
    setBusy((Array.isArray(stages) ? stages.join("|") : stages) + (est ? `#${est}` : ""));
    setError("");
    setPaywall(null);
    setNeedLogin(null);
    if (est && est >= 30) offerPush();  // пуш о готовности, даже если закроют сайт; разрешение спрашиваем по клику
    try {
      await fn();
    } catch (e) {
      if (e instanceof ApiError && e.status === 402) setPaywall(e.data?.need || "package");
      else if (e instanceof ApiError && e.status === 403 && e.data?.need === "login") setNeedLogin(() => () => run(stages, fn, est));
      else setError(e instanceof ApiError || e instanceof Error ? e.message : "Ошибка");
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="stack">
      {step !== "start" && <Progress step={step} mode={mode} />}
      {guest && step !== "start" && (
        <div className="banner save-banner">
          <span>Вы работаете без входа. Сохраните кабинет, чтобы не потерять материалы и вернуться с любого устройства.</span>
          <button className="btn gold" onClick={() => window.dispatchEvent(new Event("jp-login"))}>Сохранить в кабинете</button>
        </div>
      )}
      {!ai && step !== "start" && <div className="banner">ИИ пока не подключён: материалы и вакансия сохранятся, документы соберём после подключения.</div>}
      {error && !busy && <div className="banner error">{error}</div>}
      {paywall && !busy && <Paywall need={paywall} onClose={() => setPaywall(null)} />}
      {needLogin && !busy && (
        <LoginBox title="Осталось войти" note="Документы готовы к сборке. Войдите, и они соберутся в вашем кабинете: всё, что вы загрузили, сохранится. Зайти потом можно будет с сайта, из бота или мини-приложения."
          onDone={() => { const retry = needLogin; setNeedLogin(null); window.dispatchEvent(new Event("jp-me")); retry(); }} />
      )}
      {busy && <Working key={busy} stages={busy.split("#")[0].split("|")} est={Number(busy.split("#")[1]) || undefined} />}

      {/* На время обработки форма шага скрыта, но не размонтирована: при ошибке введённое на месте */}
      <div className="stack" hidden={!!busy}>
      {step === "start" && <Start onChoose={choose} />}

      {step === "materials" && (
        <Materials quick={quick} disabled={!!busy} onNext={() => run(["Читаю ваши материалы", "Выделяю опыт и достижения", "Собираю профиль"], async () => {
          const r = await api<{ job_id?: number }>("/profile/build", { method: "POST", json: {} });
          if (r.job_id) await waitJob(r.job_id);
          setStep(quick ? "items" : "vacancy");
        }, 30)} />
      )}

      {step === "vacancy" && (
        <Vacancy disabled={!!busy} onSubmit={(url, text, company_url) => run(["Читаю вакансию", "Выделяю требования", "Смотрю сайт компании", "Сравниваю с вашим профилем", "Формулирую уточнения"], async () => {
          let t: Target;
          try {
            t = await api<Target>("/targets", { method: "POST", json: { url: url || null, text: text || null, company_url: company_url || null } });
          } catch (e) {
            if (!text || !(url || company_url)) throw e;
            // сайт недоступен, а текст есть: повторяем сами, без ссылок, чтобы человеку не начинать заново
            t = await api<Target>("/targets", { method: "POST", json: { url: null, text, company_url: null } });
          }
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
          {target && !quick && <p className="muted small">{target.title} · {target.company}</p>}
          {itemList.map(([k, title]) => (
            <label key={k} className="check">
              <input type="checkbox" checked={items.includes(k)}
                onChange={() => setItems(items.includes(k) ? items.filter((i) => i !== k) : itemList.map(([x]) => x).filter((x) => x === k || items.includes(x)))} />
              {title}{ITEM_NOTES[k] && <span className="muted small"> · {ITEM_NOTES[k]}</span>}
            </label>
          ))}
          {quick && (
            <label>На какую должность резюме (необязательно)
              <input value={role} onChange={(e) => setRole(e.target.value)} placeholder="Например: руководитель отдела продаж" />
              <span className="muted small">Не знаете точно: оставьте пустым, соберу под вашу основную профессию.</span>
            </label>
          )}
          {quick && <p className="muted small">Сопроводительное письмо и сообщение менеджеру пишутся под конкретную вакансию: они есть во втором пути.</p>}
          <div className="row gap">
            <button className="btn secondary" onClick={() => setItems(itemList.map(([k]) => k))}>Всё сразу</button>
            <button className="btn" disabled={!items.some((i) => itemList.some(([k]) => k === i))}
              onClick={() => quick
                ? run("Готовлю черновик", async () => {
                  const its = items.filter((i) => !TARGET_ONLY.includes(i));
                  setItems(its);
                  setTarget(await api<Target>("/targets/general", { method: "POST", json: { role: role.trim() || null } }));
                  setPackageId(undefined);
                  setStep(its.some((i) => FILE_ITEMS.includes(i)) ? "scheme" : "result");  // только тексты: без схемы
                })
                : setStep(items.some((i) => FILE_ITEMS.includes(i)) ? "scheme" : "result")}>Дальше</button>
          </div>
        </div>
      )}

      {step === "scheme" && (
        <Schemes value={scheme} onChange={setScheme} targetId={quick ? undefined : target?.id}
          onNext={() => setStep(items.some((i) => i === "cv" || i === "one_page") ? "photo" : "result")} />
      )}

      {step === "photo" && <Photo disabled={!!busy} onNext={() => setStep("result")} />}

      {step === "result" && target && (
        <Result target={target} items={items} scheme={scheme} packageId={packageId} setPackageId={setPackageId}
          busy={busy} run={run} ai={ai} onNew={restart}
          onTarget={quick ? () => { choose("target"); setStep("vacancy"); } : undefined} />
      )}

      {step === "materials" && <button className="link" onClick={() => setStep("start")}>← Выбрать другой путь</button>}
      {!["start", "materials", "result"].includes(step) && (
        quick
          ? <button className="link" onClick={() => setStep("materials")}>← Добавить материалы</button>
          : <button className="link" onClick={() => setStep(step === "vacancy" ? "materials" : "vacancy")}>
              ← {step === "vacancy" ? "Добавить материалы" : "Другая вакансия"}
            </button>
      )}
      </div>
    </div>
  );
}

// Развилка в начале: быстро собрать резюме из опыта или подготовиться к конкретной вакансии
function Start({ onChoose }: { onChoose: (m: Mode) => void }) {
  return (
    <div className="stack">
      <h2 className="fork-title">Что делаем?</h2>
      <button className="card fork" onClick={() => onChoose("quick")}>
        <span className="fork-ico">⚡</span>
        <span className="fork-body">
          <b>Быстро собрать резюме из опыта</b>
          <span className="muted small">Надиктуйте или напишите, что делали, пришлите старое резюме, заметки, любые
            разрозненные материалы. Соберу аккуратное резюме и одностраничник.</span>
          <span className="fork-time">⏱ около 2 минут: сам ИИ работает чуть больше минуты</span>
        </span>
        <span className="fork-go">→</span>
      </button>
      <button className="card fork" onClick={() => onChoose("target")}>
        <span className="fork-ico">🎯</span>
        <span className="fork-body">
          <b>Подготовиться к конкретной вакансии</b>
          <span className="muted small">Пришлите вакансию: проверю, насколько ваш опыт ей соответствует, и соберу резюме,
            одностраничник и сопроводительное письмо под её требования, в стиле компании.</span>
          <span className="fork-time">⏱ 5-7 минут вместе с ответами на 2-3 вопроса</span>
        </span>
        <span className="fork-go">→</span>
      </button>
    </div>
  );
}

const STEPS: [Step, string][] = [["materials", "О себе"], ["vacancy", "Вакансия"], ["answers", "Уточнения"],
  ["items", "Документы"], ["scheme", "Стиль"], ["photo", "Фото"], ["result", "Готово"]];
const QUICK_STEPS = STEPS.filter(([s]) => s !== "vacancy" && s !== "answers");

function Progress({ step, mode }: { step: Step; mode: Mode }) {
  const list = mode === "quick" ? QUICK_STEPS : STEPS;
  const i = list.findIndex(([s]) => s === step);
  return (
    <div className="steps-wrap"><div className="steps">
      {list.map(([s, t], n) => (
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

function Materials({ onNext, disabled, quick }: { onNext: () => void; disabled: boolean; quick: boolean }) {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  useEffect(() => { getConfig().then(setCfg).catch(() => {}); }, []);
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
  const onVoice = async (b: Blob, name: string) => {
    setErr("");
    setWait(["Загружаю запись", "Распознаю речь", "Убираю персональные данные"]);
    try { await upload<Source>("/sources/voice", b, name); }
    catch (e: any) { setErr(e.message); }
    finally { setWait(null); }
    reload();
  };
  const ok = sources.filter((s) => s.status === "parsed");
  return (
    <>
    {wait && <Working stages={wait} />}
    <div className="card stack" hidden={!!wait}>
      <h2>{quick ? "Расскажите о своём опыте" : "Ваше резюме и рассказ о себе"}</h2>
      <p className="muted small">{quick
        ? "Подойдёт что угодно и в любом порядке: надиктуйте, напишите своими словами, пришлите старое резюме, скан, фото, заметки. Где работали, что делали, каких результатов добились, лучше с цифрами. Материалов может быть несколько: добавляйте по очереди."
        : "Файл (PDF, DOCX), скан или фото. Плюс можно рассказать о себе: чем занимались, какие результаты с цифрами."}</p>
      <label className="btn secondary file">
        Загрузить файл или фото
        <input type="file" accept=".pdf,.docx,.txt,.jpg,.jpeg,.png,.webp" hidden onChange={(e) => onFile(e.target.files?.[0])} />
      </label>
      {cfg?.voice && <VoiceRecorder onDone={onVoice} />}
      {cfg && !cfg.voice && quick && <p className="muted small">🎙 Голос в приложении пока не подключён: напишите текстом или надиктуйте боту в Telegram.</p>}
      <textarea rows={quick ? 6 : 4} placeholder={quick ? "Например: 2019–2024 руководила отделом продаж в дистрибьюторе, команда 12 человек, выручка выросла на 40%…" : "Расскажите о себе"} value={text} onChange={(e) => setText(e.target.value)} />
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
  // Ссылка и текст можно указать вместе: тогда берём текст, а ссылку не открываем. Только ссылка: сначала проверяем,
  // читается ли страница (не дольше 25 с). Не читается: объясняем почему и просим вставить текст.
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [company, setCompany] = useState("");
  const [check, setCheck] = useState<Check | null>(null);
  const [checking, setChecking] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const next = async () => {
    if (text.trim()) return onSubmit(url.trim(), text.trim(), company.trim());
    setChecking(true);
    setCheck(null);
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 25000);
    try {
      const c = await api<Check>("/targets/check", { method: "POST", json: { url: url.trim() }, signal: ctrl.signal });
      setCheck(c);
      if (c.ok) onSubmit(url.trim(), "", company.trim());
      else setTimeout(() => textRef.current?.focus(), 50);
    } catch (e: any) {
      setCheck({ ok: false, reason: e?.name === "AbortError" ? "сайт не ответил за 25 секунд." : e.message });
      setTimeout(() => textRef.current?.focus(), 50);
    } finally {
      clearTimeout(timer);
      setChecking(false);
    }
  };
  return (
    <div className="card stack">
      <h2>Вакансия</h2>
      <p className="muted small">Можно ссылку, можно текст, можно и то и другое. Если есть текст, ссылку я не открываю.</p>
      <label>Ссылка на вакансию на сайте компании
        <input value={url} onChange={(e) => { setUrl(e.target.value); setCheck(null); }} placeholder="https://career.company.ru/..." />
      </label>
      {check && !check.ok && (
        <div className="banner error">
          Не получилось прочитать страницу: {check.reason} <b>Скопируйте текст вакансии и вставьте его ниже.</b>
        </div>
      )}
      <label>Текст вакансии
        <textarea ref={textRef} rows={6} value={text} onChange={(e) => setText(e.target.value)}
          className={check && !check.ok ? "attention" : ""} placeholder="Ссылки hh.ru не открываю: скопируйте текст оттуда" />
      </label>
      <label>Сайт компании (необязательно: миссия, ценности и фирменные цвета для резюме)
        <input value={company} onChange={(e) => setCompany(e.target.value)} placeholder="company.ru" />
      </label>
      <button className="btn" disabled={disabled || checking || !(url.trim() || text.trim())} onClick={next}>
        {checking ? <><span className="spinner sm" /> Открываю ссылку…</> : "Дальше"}
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

// Схема в цветах компании: цвета снимаются с её сайта заново при каждом показе (GET /targets/{id}/brand)
function useBrand(targetId?: number) {
  const [brand, setBrand] = useState<{ scheme: Scheme | null; reason?: string } | null>(null);
  useEffect(() => {
    if (!targetId) return;
    setBrand(null);
    api<{ scheme: Scheme | null; reason?: string }>(`/targets/${targetId}/brand`).then(setBrand)
      .catch(() => setBrand({ scheme: null, reason: "Не получилось подобрать цвета компании." }));
  }, [targetId]);
  return { brand, loading: !!targetId && brand === null };
}

function SchemeCard({ s, on, onClick }: { s: Scheme; on: boolean; onClick: () => void }) {
  return (
    <button className={`scheme ${on ? "on" : ""}`} onClick={onClick}>
      <span className="sheet">
        <span className="stripe" style={{ background: s.signal }} />
        <span className="head" style={{ background: s.head }}>
          <span className="nm" />
          <span className="hl" style={{ background: s.highlight }} />
        </span>
        <span className="body" />
      </span>
      <span className="small">{s.key.startsWith("company-") ? "🎨 " : ""}{s.title.split(" (")[0]}</span>
    </button>
  );
}

function Schemes({ value, onChange, onNext, targetId }: { value: string; onChange: (k: string) => void; onNext: () => void; targetId?: number }) {
  const [list, setList] = useState<Scheme[]>([]);
  const { brand, loading } = useBrand(targetId);
  useEffect(() => { api<Scheme[]>("/schemes").then(setList).catch(() => {}); }, []);
  useEffect(() => { if (brand?.scheme && !value.startsWith("company-")) onChange(brand.scheme.key); }, [brand]);
  return (
    <div className="card stack">
      <h2>Цветовая схема</h2>
      <div className="schemes">
        {loading && (
          <div className="scheme ph-scheme"><span className="sheet center-ph"><span className="spinner" /></span>
            <span className="small muted">Подбираю цвета компании…</span></div>
        )}
        {brand?.scheme && <SchemeCard s={brand.scheme} on={brand.scheme.key === value} onClick={() => onChange(brand.scheme!.key)} />}
        {list.map((s) => <SchemeCard key={s.key} s={s} on={s.key === value} onClick={() => onChange(s.key)} />)}
      </div>
      {brand && !brand.scheme && brand.reason && <p className="muted small">🎨 {brand.reason}</p>}
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
  busy: string; run: (stages: string | string[], fn: () => Promise<void>, est?: number) => Promise<void>; ai: boolean; onNew: () => void; onTarget?: () => void;
}) {
  const { target, items, scheme, packageId, setPackageId, busy, run, ai, onNew, onTarget } = props;
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
      const r = await api<{ sent: number; texts?: number; skipped: string[] }>(`/packages/${pkg!.id}/send`, {
        method: "POST", json: { accept_flags: accept } });
      setSent(`✓ Отправил в чат с ботом: ${r.sent} ${r.sent === 1 ? "файл" : r.sent < 5 ? "файла" : "файлов"}`
        + (r.texts ? " и тексты для копирования" : "") + (r.skipped.length ? ". Письмо ждёт подтверждения" : ""));
    } catch (e: any) {
      setSent(`⚠️ ${e.message}`);
    }
  };
  const downloadItem = async (it: string, fmt: "pdf" | "docx" = "pdf") => {
    setLoadingItem(`${it}:${fmt}`);
    const q = [fmt === "docx" ? "fmt=docx" : "", it === "letter" && accept ? "accept_flags=true" : ""].filter(Boolean).join("&");
    try {
      await download(`/packages/${pkg!.id}/file/${it}${q ? `?${q}` : ""}`,
        (fmt === "docx" ? pkg!.docx_names?.[it] : pkg!.file_names?.[it]) || `${FILE_NAMES[it]}_v${pkg!.version}.${fmt}`);
    } finally {
      setLoadingItem("");
    }
  };

  const generate = (instr?: string) => run(instr
    ? ["Вношу правку", "Перепроверяю факты", "Собираю PDF"]
    : target.general
      ? ["Навожу порядок в опыте", "Пишу резюме", "Проверяю каждый факт по профилю", "Собираю PDF"]
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
    setPreviews({});
    for (const it of p.items.filter((i) => FILE_ITEMS.includes(i))) {
      const letterBlocked = it === "letter" && p.flags.some(isLetterFlag);
      if (letterBlocked) continue;
      try {
        const b = await api<Blob>(`/packages/${id}/file/${it}?preview=true`);
        const u = URL.createObjectURL(b);
        setPreviews((prev) => ({ ...prev, [it]: u }));
      } catch { /* превью необязательно */ }
    }
  };

  const { brand } = useBrand(target.general ? undefined : target.id);
  const [salary, setSalary] = useState<Salary | null>(null);
  useEffect(() => {
    if (pkg) api<Salary>(`/targets/${target.id}/salary`).then(setSalary)
      .catch(() => setSalary({ n: 0, median: null, reason: "Не получилось посчитать ориентир. Попробуйте позже." }));
  }, [pkg?.id]);
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
        <button className="btn secondary" onClick={onNew}>В начало</button>
      </div>
    );
  }
  if (!pkg && !packageId && !busy) {  // сборка не началась или упала (например, нужен тариф): даём повторить
    return (
      <div className="card stack">
        <p>Документы ещё не собраны.</p>
        <button className="btn" onClick={() => generate()}>Собрать документы</button>
      </div>
    );
  }
  if (!pkg) return busy ? null : <Working stages={["Загружаю документы"]} />;
  const cvFlags = pkg.flags.filter((f) => f.where === "cv");
  const letterFlags = pkg.flags.filter(isLetterFlag);
  const extraFlags = pkg.flags.filter((f) => EXTRA_WHERE.includes(f.where));
  const fileItems = pkg.items.filter((i) => FILE_ITEMS.includes(i));
  return (
    <div className="stack">
      <div className="card stack">
        <h2>{target.general ? `Резюме: ${target.title}` : `${target.title} · ${target.company}`}</h2>
        {cvFlags.length > 0 && (
          <div className="banner">Убрал из резюме то, что не подтверждается вашим профилем:
            <ul>{cvFlags.map((f) => <li key={f.text}>{f.text}</li>)}</ul></div>
        )}
        {pkg.score && pkg.score.before != null && pkg.score.after != null && <ScoreLine s={pkg.score} />}
        {pkg.remark && <Remark text={pkg.remark} />}
        <div className="previews" hidden={!fileItems.length}>
          {fileItems.map((it) => (
            <div key={it} className="preview">
              {previews[it] ? <img src={previews[it]} alt="" /> : (
                <div className="ph muted small">
                  {it === "letter" && letterFlags.length > 0 && !accept ? "Письмо ждёт подтверждения"
                    : <><span className="spinner" /><span className="dots-text">Готовлю превью</span></>}
                </div>
              )}
              <button className={`btn ${inTelegram() ? "secondary" : ""}`}
                disabled={(it === "letter" && letterFlags.length > 0 && !accept) || loadingItem === `${it}:pdf`}
                onClick={() => downloadItem(it)}>
                {loadingItem === `${it}:pdf` ? "Скачиваю…" : `${ITEMS.find(([k]) => k === it)?.[1]} · PDF`}
              </button>
              <button className="btn secondary small-btn"
                disabled={(it === "letter" && letterFlags.length > 0 && !accept) || loadingItem === `${it}:docx`}
                onClick={() => downloadItem(it, "docx")}>
                {loadingItem === `${it}:docx` ? "Скачиваю…" : "Word (DOCX)"}
              </button>
            </div>
          ))}
        </div>
        {pkg.items.includes("message") && pkg.recruiter_message && (
          <TextBlock title="Сообщение нанимающему менеджеру" text={pkg.recruiter_message}
            note="Для LinkedIn или почты. Имя менеджера подставьте сами." />
        )}
        {pkg.items.includes("linkedin") && pkg.linkedin_text && (
          <TextBlock title="Профиль LinkedIn" text={pkg.linkedin_text}
            note="Скопируйте разделы в соответствующие поля профиля." />
        )}
        {extraFlags.length > 0 && (
          <div className="banner">В этих текстах есть утверждения, которых нет в вашем профиле. Проверьте их:
            <ul>{extraFlags.map((f) => <li key={f.text}>{f.text} — {f.reason}</li>)}</ul></div>
        )}
        <button className={`btn ${inTelegram() ? "" : "secondary"}`} disabled={sent === "sending"} onClick={sendToChat}>
          {sent === "sending" ? "Отправляю в Telegram…" : "Прислать всё в Telegram"}
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

      {salary ? <SalaryCard s={salary} /> : (
        <div className="card stack small-gap"><h2>Ориентир по зарплате</h2>
          <p className="muted small"><span className="spinner" /> Считаю по открытым данным «Работы России»…</p></div>
      )}

      <div className="card stack" hidden={!fileItems.length}>
        <h2>Цветовая схема</h2>
        <div className="row gap wrap">
          {[...(brand?.scheme ? [brand.scheme] : []),
            ...(pkg.scheme.startsWith("company-") && brand?.scheme?.key !== pkg.scheme
              ? [{ key: pkg.scheme, title: "Цвета компании", head: "", highlight: "", signal: "" }] : []),
            ...schemes].map((s) => (
            <button key={s.key} className={`btn ${s.key === pkg.scheme ? "" : "secondary"}`} disabled={!!busy}
              onClick={() => run(["Перерисовываю документы"], async () => {
                await api(`/packages/${pkg.id}`, { method: "PATCH", json: { scheme: s.key } });
                await loadPkg(pkg.id);
              })}>{s.key.startsWith("company-") ? "🎨 " : ""}{s.title.split(" (")[0]}</button>
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

      {(
        <div className="card stack">
          <h2>Правка</h2>
          <p className="muted small">{pkg.revision_left ? "Правка входит в ваш лимит." : `Бесплатная правка уже использована: следующая ${pkg.revision_price} ₽ или по тарифу.`}
            {" "}Опишите все правки одним сообщением.</p>
          <textarea rows={3} value={instruction} onChange={(e) => setInstruction(e.target.value)}
            placeholder="Например: короче summary, акцент на B2B, письмо менее формально" />
          <button className="btn" disabled={!instruction.trim() || !!busy} onClick={() => generate(instruction.trim())}>Внести правку</button>
        </div>
      )}
      {onTarget && (
        <div className="card stack">
          <h2>Есть конкретная вакансия?</h2>
          <p className="muted small">Проверю, насколько ваш опыт подходит под её требования, и соберу документы под неё,
            включая сопроводительное письмо. Материалы уже есть, начнём сразу с вакансии.</p>
          <button className="btn" onClick={onTarget}>Подготовиться к вакансии</button>
        </div>
      )}
      <button className="btn secondary" onClick={onNew}>{onTarget ? "В начало" : "Новая вакансия"}</button>
    </div>
  );
}

// П13: живая реплика сервиса (стиль «на ты / на вы» в кабинете), пусто при нейтральном стиле
function Remark({ text }: { text: string }) {
  return <div className="remark"><span aria-hidden>💬</span> {text}</div>;
}

// П4: балл до и после. Под вакансию: соответствие требованиям; без вакансии: качество по проверкам /check
function ScoreLine({ s }: { s: Score }) {
  const d = (s.after ?? 0) - (s.before ?? 0);
  return (
    <div className="score-line">
      <span className="muted small">{s.title}</span>
      <b>{s.before} → {s.after}</b><span className="muted small">из 100{d ? ` (${d > 0 ? "+" : ""}${d})` : ""}</span>
      <span className="muted small">{s.kind === "vacancy" ? "насколько резюме подходит под требования вакансии"
        : "цифры в результатах, объём, даты, контакты, разделы"}</span>
    </div>
  );
}

// П5, П3: тексты для копирования
function TextBlock({ title, text, note }: { title: string; text: string; note?: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 2000); }
    catch { /* нет доступа к буферу: текст можно выделить руками */ }
  };
  return (
    <div className="text-block stack small-gap">
      <div className="row gap wrap"><b>{title}</b>
        <button className="btn secondary small-btn" onClick={copy}>{done ? "✓ Скопировано" : "Скопировать"}</button></div>
      {note && <span className="muted small">{note}</span>}
      <pre className="text-body">{text}</pre>
    </div>
  );
}

// П6: ориентир по зарплате из открытых данных, с датой и источником
function SalaryCard({ s }: { s: Salary }) {
  const rub = (x?: number | null) => (x ?? 0).toLocaleString("ru-RU") + " ₽";
  return (
    <div className="card stack small-gap">
      <h2>Ориентир по зарплате</h2>
      {s.median ? (
        <>
          <p><b>{rub(s.median)}</b> медиана · обычно {rub(s.p25)} - {rub(s.p75)}</p>
          <p className="muted small">«{s.role}», {s.scope}, по {s.n} вакансиям с указанной зарплатой.
            Источник: <a href={s.url} target="_blank" rel="noreferrer">{s.source}</a>, {s.date}. {s.caveat}</p>
        </>
      ) : <p className="muted small">{s.reason || "Данных нет."}</p>}
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
          {res.remark && <Remark text={res.remark} />}
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
