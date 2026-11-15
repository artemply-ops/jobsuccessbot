// Вход и способы входа. Один кабинет на сайт, Mini App и бота (backend/app/services/accounts.py).
// Если человек уже вошёл (гость или кабинет), новый способ входа привязывается к этому кабинету, а не создаёт новый.
import { useEffect, useRef, useState } from "react";
import { api, getConfig, getToken, inTelegram, setToken, type Cfg } from "../api";

type Methods = { telegram: boolean; telegram_username: string | null; email: string | null; yandex: boolean; guest: boolean };

const done = (token: string, onDone: () => void) => { setToken(token); onDone(); };

// Кнопка Telegram Login Widget. Работает только на домене, привязанном к боту (/setdomain у BotFather)
export function TgWidget({ onDone, onError }: { onDone: () => void; onError: (m: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [bot, setBot] = useState("");
  useEffect(() => { getConfig().then((c) => setBot(c.bot_username)).catch(() => {}); }, []);
  useEffect(() => {
    if (!bot || !ref.current) return;
    window.onTelegramAuth = async (user) => {
      try { done((await api<{ token: string }>("/auth/telegram-login", { method: "POST", json: user })).token, onDone); }
      catch (e: any) { onError(e.message); }
    };
    const s = document.createElement("script");
    s.src = "https://telegram.org/js/telegram-widget.js?22";
    s.async = true;
    s.setAttribute("data-telegram-login", bot);
    s.setAttribute("data-size", "large");
    s.setAttribute("data-radius", "12");
    s.setAttribute("data-onauth", "onTelegramAuth(user)");
    s.setAttribute("data-request-access", "write");
    ref.current.innerHTML = "";
    ref.current.appendChild(s);
  }, [bot]);
  if (!bot) return <span className="muted small">Вход через Telegram скоро появится</span>;
  return <div ref={ref} className="tg-widget" />;
}

export function EmailLogin({ onDone, cta = "Войти" }: { onDone: () => void; cta?: string }) {
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(false);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [wait, setWait] = useState(0);
  useEffect(() => { if (wait > 0) { const t = setTimeout(() => setWait(wait - 1), 1000); return () => clearTimeout(t); } }, [wait]);

  const send = async () => {
    setBusy(true); setMsg("");
    try {
      const r = await api<{ dev_code?: string }>("/auth/email/start", { method: "POST", json: { email } });
      setSent(true); setWait(60);
      if (r.dev_code) setCode(r.dev_code);  // локальная разработка: письма не уходят
    } catch (e: any) { setMsg(e.message); } finally { setBusy(false); }
  };
  const verify = async () => {
    setBusy(true); setMsg("");
    try { done((await api<{ token: string }>("/auth/email/verify", { method: "POST", json: { email, code } })).token, onDone); }
    catch (e: any) { setMsg(e.message); } finally { setBusy(false); }
  };

  return (
    <div className="stack small-gap">
      {!sent ? (
        <form className="row gap" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <input type="email" autoComplete="email" placeholder="Ваша почта" value={email} onChange={(e) => setEmail(e.target.value)} />
          <button className="btn" disabled={busy || !email.includes("@")}>{busy ? "…" : "Получить код"}</button>
        </form>
      ) : (
        <>
          <p className="muted small">Код отправлен на {email}. Если письма нет, проверьте папку «Спам».</p>
          <form className="row gap" onSubmit={(e) => { e.preventDefault(); verify(); }}>
            <input inputMode="numeric" autoComplete="one-time-code" placeholder="Код из письма" value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
            <button className="btn" disabled={busy || code.length !== 6}>{busy ? "…" : cta}</button>
          </form>
          <div className="row gap small">
            <button className="link" onClick={() => { setSent(false); setCode(""); }}>Другая почта</button>
            <button className="link" disabled={wait > 0 || busy} onClick={send}>{wait > 0 ? `Отправить снова через ${wait} с` : "Отправить снова"}</button>
          </div>
        </>
      )}
      {msg && <p className="error small">{msg}</p>}
    </div>
  );
}

async function yandex() {
  // Уже вошёл (гость или кабинет): ссылка с привязкой к этому кабинету, иначе обычный вход
  if (getToken()) {
    const r = await api<{ url: string }>("/auth/yandex/link", { method: "POST" });
    window.location.href = r.url;
  } else window.location.href = "/api/auth/yandex/start";
}

// Окно входа: почта, Telegram, Яндекс ID (когда включён). Используется на лендинге, перед сборкой документов
export function LoginBox({ onDone, title = "Вход в JobPilot", note }: { onDone: () => void; title?: string; note?: string }) {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { getConfig().then(setCfg).catch(() => {}); }, []);
  const dev = async () => done((await api<{ token: string }>("/auth/dev", { method: "POST" })).token, onDone);
  const err0 = new URLSearchParams(window.location.search).get("login_error");
  return (
    <div className="card stack login-box">
      <h2>{title}</h2>
      {note && <p className="muted">{note}</p>}
      {cfg?.login.email && <EmailLogin onDone={onDone} />}
      {cfg && !cfg.login.email && <p className="muted small">Вход по почте скоро появится.</p>}
      <div className="or"><span>или</span></div>
      {!inTelegram() && <TgWidget onDone={onDone} onError={setErr} />}
      {cfg?.login.yandex && <button className="btn secondary" onClick={() => yandex().catch((e) => setErr(e.message))}>Войти с Яндекс ID</button>}
      {cfg?.dev_login && <button className="btn secondary" onClick={dev}>Войти (локальная разработка)</button>}
      {(err || err0) && <p className="error small">{err || "Не удалось войти через Яндекс. Попробуйте ещё раз"}</p>}
      <p className="muted small">Вход любым способом открывает один и тот же кабинет на сайте, в боте и в мини-приложении Telegram.</p>
    </div>
  );
}

// Профиль: какие способы входа привязаны, добавить недостающие
export function LoginMethods({ onChange }: { onChange: () => void }) {
  const [m, setM] = useState<Methods | null>(null);
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [err, setErr] = useState("");
  const [addEmail, setAddEmail] = useState(false);
  const load = () => api<Methods>("/auth/methods").then(setM).catch(() => {});
  useEffect(() => { load(); getConfig().then(setCfg).catch(() => {}); }, []);
  if (!m) return null;
  const changed = () => { setAddEmail(false); load(); onChange(); };
  return (
    <div className="card stack">
      <h2>Способы входа</h2>
      <p className="muted small">Один кабинет на сайте job-pilot.ru, в боте и в мини-приложении. Привяжите несколько способов,
        чтобы войти откуда удобно.</p>
      <div className="method">
        <span>Telegram</span>
        {m.telegram ? <span className="ok-text">✓ {m.telegram_username ? `@${m.telegram_username}` : "подключён"}</span>
          : inTelegram() ? null : <TgWidget onDone={changed} onError={setErr} />}
      </div>
      <div className="method">
        <span>Почта</span>
        {m.email ? <span className="ok-text">✓ {m.email}</span>
          : cfg?.login.email ? (addEmail ? null : <button className="link" onClick={() => setAddEmail(true)}>Привязать</button>)
          : <span className="muted small">скоро</span>}
      </div>
      {addEmail && <EmailLogin onDone={changed} cta="Привязать" />}
      {cfg?.login.yandex && (
        <div className="method">
          <span>Яндекс ID</span>
          {m.yandex ? <span className="ok-text">✓ подключён</span>
            : <button className="link" onClick={() => yandex().catch((e) => setErr(e.message))}>Привязать</button>}
        </div>
      )}
      {err && <p className="error small">{err}</p>}
    </div>
  );
}
