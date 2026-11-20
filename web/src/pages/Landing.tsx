// Сайт для браузера без входа. Логика как у сервисов-конкурентов: одна главная кнопка, как работает, пример,
// почему мы, цены, вопросы. «Сделать резюме» сразу пускает в работу гостем, вход нужен перед сборкой документов.
// Тексты владелец правит в админке (GET /api/site/texts, backend/app/services/site_texts.py), структура здесь.
// Цены и реквизиты приходят с сервера (/api/billing/public) и подставляются в тексты вместо {week}, {month}...
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, getToken, startGuest } from "../api";
import { LoginBox } from "./Auth";
import type { Product } from "./Billing";

// П12: иллюстрации из Weave. Файл кладётся в web/public/art/<имя>.webp, имя добавляется сюда, затем выкатка.
// Пока файла нет, слот пустой и вёрстка не ломается: вместо картинки работает SVG-сцена
const ART = new Set<string>([]);
function Art({ name, alt = "" }: { name: string; alt?: string }) {
  if (!ART.has(name)) return null;
  return <img className={`art art-${name}`} src={`/art/${name}.webp`} alt={alt} loading="lazy" decoding="async" />;
}

// П12: появление блоков при прокрутке. Без поддержки IntersectionObserver или при «меньше движения» всё видно сразу
function useReveal(ready: boolean) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = root.current;
    if (!ready || !el) return;
    const items = Array.from(el.querySelectorAll<HTMLElement>(".reveal"));
    if (!("IntersectionObserver" in window) || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      items.forEach((x) => x.classList.add("in"));
      return;
    }
    const io = new IntersectionObserver((es) => es.forEach((e) => {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }), { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    items.forEach((x) => io.observe(x));
    return () => io.disconnect();
  }, [ready]);
  return root;
}

// Сцена первого экрана: линия роста, орбита и искры. Чистый SVG, без внешних картинок
function HeroScene() {
  return (
    <svg className="hero-scene" viewBox="0 0 600 600" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="hs-gold" x1="0" x2="1" y1="1" y2="0">
          <stop offset="0" style={{ stopColor: "var(--gold)", stopOpacity: 0 }} />
          <stop offset=".6" style={{ stopColor: "var(--gold)", stopOpacity: .9 }} />
          <stop offset="1" style={{ stopColor: "var(--gold)" }} />
        </linearGradient>
      </defs>
      <circle className="hs-orbit" cx="300" cy="300" r="250" />
      <circle className="hs-orbit hs-orbit-2" cx="300" cy="300" r="190" />
      <path className="hs-growth" d="M40 470 C 150 455, 190 400, 260 380 S 380 300, 430 230 S 520 110, 570 80" stroke="url(#hs-gold)" />
      <circle className="hs-dot" cx="570" cy="80" r="7" />
      {[[96, 140, 0], [500, 420, 1.2], [150, 520, 2.1], [470, 170, .6], [60, 330, 1.7]].map(([x, y, d], i) => (
        <path key={i} className="hs-spark" style={{ animationDelay: `${d}s` }}
          d={`M${x} ${y - 9} L${x + 2.5} ${y - 2.5} L${x + 9} ${y} L${x + 2.5} ${y + 2.5} L${x} ${y + 9} L${x - 2.5} ${y + 2.5} L${x - 9} ${y} L${x - 2.5} ${y - 2.5} Z`} />
      ))}
    </svg>
  );
}

// Цены конкурента на 15.11.2026 (docs/ECONOMICS.md). Сравнение должно быть проверяемым: дата в сноске
const RIVAL = { week: 349, month: 1049 };

type Pub = { products: Product[]; bot_username: string; legal: { name: string; inn: string; status: string; email: string } };
type Item = Record<string, string>;
export type Texts = {
  hero: { eyebrow: string; title: string; lead: string; cta: string; cta2: string; trust: Item[] };
  card: { name: string; role: string; contacts: string; summary: string; highlights: Item[]; job: string; bullets: string;
    score: string; score_title: string; score_note: string };
  band: { title: string; items: Item[] };
  how: { title: string; subtitle: string; quick_label: string; quick_title: string; quick_time: string; quick_steps: Item[];
    long_label: string; long_title: string; long_time: string; long_steps: Item[] };
  example: { title: string; subtitle: string; items: Item[] };
  get: { title: string; items: Item[] };
  prices: { title: string; subtitle: string; badge: string };
  faq: { title: string; items: Item[] };
  final: { title: string; text: string; telegram: string; footnote: string };
};

export default function Landing({ onAuthed }: { onAuthed: () => void }) {
  const [pub, setPub] = useState<Pub | null>(null);
  const [t, setT] = useState<Texts | null>(null);
  const [login, setLogin] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ex, setEx] = useState(0);  // какой пример «было / стало» открыт
  useEffect(() => { api<Pub>("/billing/public").then(setPub).catch(() => {}); }, []);
  useEffect(() => { api<Texts>("/site/texts").then(setT).catch(() => {}); }, []);
  useEffect(() => { if (new URLSearchParams(window.location.search).has("login_error")) setLogin(true); }, []);
  const price = (sku: string, fallback: number) => pub?.products.find((x) => x.sku === sku)?.price_rub ?? fallback;
  // Подстановка цен в тексты из админки: цены живут только в billing.py
  const f = (s: string) => (s || "")
    .replace(/\{week\}/g, String(price("week", 249))).replace(/\{month\}/g, String(price("month", 690)))
    .replace(/\{package\}/g, String(price("package", 59))).replace(/\{revision\}/g, String(price("revision", 29)))
    .replace(/\{rival_week\}/g, RIVAL.week.toLocaleString("ru-RU")).replace(/\{rival_month\}/g, RIVAL.month.toLocaleString("ru-RU"));
  const bot = pub?.bot_username;
  const root = useReveal(!!t);

  const start = async () => {
    setBusy(true); setErr("");
    try {
      if (!getToken()) await startGuest();  // уже начатая работа (гость вернулся на главную) не теряется
      onAuthed();
    } catch (e: any) { setErr(e.message); setLogin(true); } finally { setBusy(false); }
  };
  if (!t) return <div className="site center"><span className="spinner" /></div>;
  const Cta = ({ big }: { big?: boolean }) => (
    <button className={`btn gold ${big ? "big" : ""}`} disabled={busy} onClick={start}>{busy ? "Открываю…" : f(t.hero.cta)}</button>
  );
  const lines = (x: string) => (x || "").split("\n").map((l) => l.trim()).filter(Boolean);
  const cur = t.example.items[Math.min(ex, t.example.items.length - 1)];
  const Steps = ({ items }: { items: Item[] }) => (
    <ol className="how-steps compact">{items.map((s, i) => <li key={i}><b>{f(s.title)}</b><span>{f(s.text)}</span></li>)}</ol>
  );

  return (
    <div className="site" ref={root}>
      <header className="site-head">
        <div className="container row between">
          <a href="/" className="logo"><span className="logo-mark">J</span>JobPilot</a>
          <nav className="site-nav">
            <a href="#how">Как это работает</a>
            <a href="#example">Пример</a>
            <a href="#prices">Цены</a>
            <a href="#faq">Вопросы</a>
          </nav>
          <button className="btn ghost" onClick={() => setLogin(true)}>Войти</button>
        </div>
      </header>

      <section className="hero">
        <div className="container hero-grid">
          <div className="stack hero-text rise">
            {t.hero.eyebrow && <span className="eyebrow">{f(t.hero.eyebrow)}</span>}
            <h1>{f(t.hero.title)}</h1>
            <p className="lead">{f(t.hero.lead)}</p>
            <div className="row gap wrap"><Cta big /><button className="btn ghost big" onClick={() => setLogin(true)}>{f(t.hero.cta2)}</button></div>
            {err && <p className="error small">{err}</p>}
            <ul className="trust">{t.hero.trust.map((x, i) => <li key={i}>{f(x.text)}</li>)}</ul>
          </div>
          <div className="hero-visual">
          <HeroScene />
          <Art name="hero" />
          <div className="hero-card" aria-label="Пример результата">
            <span className="pill">пример</span>
            <div className="cv-mini">
              <div className="cv-head">
                <b className="cv-name">{t.card.name}</b>
                <span className="cv-role">{t.card.role}</span>
                <span className="cv-contacts">{t.card.contacts}</span>
              </div>
              <p className="cv-summary">{t.card.summary}</p>
              {t.card.highlights.length > 0 && (
                <div className="cv-highlights">
                  {t.card.highlights.map((h, i) => <div key={i}><b>{h.value}</b><span>{h.label}</span></div>)}
                </div>
              )}
              <div className="cv-job">{t.card.job}</div>
              <ul className="cv-bullets">{lines(t.card.bullets).map((b, i) => <li key={i}>{b}</li>)}</ul>
            </div>
            <div className="hero-score">
              <div className="score-ring"><span>{t.card.score}</span></div>
              <div className="stack small-gap">
                <b>{t.card.score_title}</b>
                <span className="muted small">{t.card.score_note}</span>
              </div>
            </div>
          </div>
          </div>
        </div>
      </section>

      <section className="band">
        <Art name="band" />
        <div className="container">
          <h2 className="section-title reveal">{f(t.band.title)}</h2>
          <div className="grid3">
            {t.band.items.map((x, i) => (
              <div key={i} className="feature reveal" style={{ transitionDelay: `${i * 90}ms` }}>
                {x.icon && <span className="feature-icon">{x.icon}</span>}
                <h3>{f(x.title)}</h3>
                <p>{f(x.text)}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="sec" id="how">
        <div className="container">
          <h2 className="section-title reveal">{f(t.how.title)}</h2>
          <p className="muted center-text">{f(t.how.subtitle)}</p>
          <div className="paths reveal">
            <div className="path">
              <div className="path-head"><span className="pill">{t.how.quick_label}</span><b>{f(t.how.quick_title)}</b>
                <span className="path-time">{f(t.how.quick_time)}</span></div>
              <Steps items={t.how.quick_steps} />
            </div>
            <div className="path gold-path">
              <div className="path-head"><span className="pill gold">{t.how.long_label}</span><b>{f(t.how.long_title)}</b>
                <span className="path-time">{f(t.how.long_time)}</span></div>
              <Steps items={t.how.long_steps} />
            </div>
          </div>
          <div className="center-row"><Cta /></div>
        </div>
      </section>

      <section className="sec alt" id="example">
        <div className="container">
          <h2 className="section-title reveal">{f(t.example.title)}</h2>
          <p className="muted center-text">{f(t.example.subtitle)}</p>
          {t.example.items.length > 1 && (
            <div className="ex-tabs">
              {t.example.items.map((x, i) => <button key={i} className={i === ex ? "on" : ""} onClick={() => setEx(i)}>{x.role}</button>)}
            </div>
          )}
          {cur && (
            <div className="compare reveal in" key={ex}>
              <div className="compare-card before">
                <span className="pill">было</span>
                <p>{f(cur.before)}</p>
              </div>
              <div className="compare-card after">
                <span className="pill gold">стало</span>
                <ul>{lines(cur.after).map((x, i) => <li key={i}>{f(x)}</li>)}</ul>
              </div>
            </div>
          )}
        </div>
      </section>

      <section className="sec">
        <div className="container">
          <h2 className="section-title reveal">{f(t.get.title)}</h2>
          <div className="grid4">
            {t.get.items.map((x, i) => <div key={i} className="feature reveal" style={{ transitionDelay: `${i * 90}ms` }}><h3>{f(x.title)}</h3><p>{f(x.text)}</p></div>)}
          </div>
        </div>
      </section>

      <section className="sec alt" id="prices">
        <div className="container">
          <h2 className="section-title reveal">{f(t.prices.title)}</h2>
          <p className="muted center-text">{f(t.prices.subtitle)}</p>
          <div className="price-grid reveal">
            {(pub?.products || []).map((x) => (
              <div key={x.sku} className={`price ${x.sku === "week" ? "best" : ""}`}>
                {x.sku === "week" && t.prices.badge && <span className="badge">{t.prices.badge}</span>}
                <b>{x.title}</b>
                <span className="price-num">{x.price_rub} ₽</span>
                <span className="muted small">{x.desc}</span>
                {x.sku in RIVAL && <span className="rival small">у конкурента {RIVAL[x.sku as keyof typeof RIVAL].toLocaleString("ru-RU")} ₽</span>}
              </div>
            ))}
          </div>
          <div className="center-row"><Cta /></div>
        </div>
      </section>

      <section className="sec" id="faq">
        <div className="container narrow-wrap">
          <h2 className="section-title reveal">{f(t.faq.title)}</h2>
          <div className="faq reveal">
            {t.faq.items.map((x, i) => <details key={i}><summary>{f(x.q)}</summary><p>{f(x.a)}</p></details>)}
          </div>
        </div>
      </section>

      <section className="final">
        <Art name="final" />
        <div className="container stack center-text reveal">
          <h2>{f(t.final.title)}</h2>
          <p>{f(t.final.text)}</p>
          <div className="center-row"><Cta big /></div>
          {bot && t.final.telegram && <p className="small"><a href={`https://t.me/${bot}`}>{f(t.final.telegram)}</a></p>}
        </div>
      </section>

      <footer className="site-foot">
        <div className="container stack small-gap">
          <div className="row between wrap gap">
            <span className="logo small-logo"><span className="logo-mark">J</span>JobPilot</span>
            <span className="row gap wrap small"><Link to="/offer">Оферта</Link><Link to="/privacy">Политика обработки персональных данных</Link>
              {pub?.legal.email && <a href={`mailto:${pub.legal.email}`}>{pub.legal.email}</a>}</span>
          </div>
          {pub?.legal.name && <span className="small muted">{pub.legal.status === "индивидуальный предприниматель" ? "ИП" : ""} {pub.legal.name}, ИНН {pub.legal.inn}</span>}
          {t.final.footnote && <span className="small muted">{f(t.final.footnote)}</span>}
        </div>
      </footer>

      {login && (
        <div className="modal" onClick={(e) => { if (e.target === e.currentTarget) setLogin(false); }}>
          <div className="modal-body">
            <button className="modal-close" aria-label="Закрыть" onClick={() => setLogin(false)}>×</button>
            <LoginBox onDone={onAuthed} />
          </div>
        </div>
      )}
    </div>
  );
}
