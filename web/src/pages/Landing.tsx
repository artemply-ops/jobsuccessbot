// Сайт для браузера без входа. Логика как у сервисов-конкурентов: одна главная кнопка, как работает, пример,
// почему мы, цены, вопросы. «Сделать резюме» сразу пускает в работу гостем, вход нужен перед сборкой документов.
// Цены и реквизиты приходят с сервера (/api/billing/public): не расходятся с ботом, оплатой и офертой.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, startGuest } from "../api";
import { LoginBox } from "./Auth";
import type { Product } from "./Billing";

// Цены конкурента на 15.11.2026 (docs/ECONOMICS.md). Сравнение должно быть проверяемым: дата в сноске
const RIVAL = { week: 349, month: 1049 };

type Pub = { products: Product[]; bot_username: string; legal: { name: string; inn: string; status: string; email: string } };

const FAQ: [string, string][] = [
  ["Нейросеть не припишет мне лишнего?",
    "Нет. Мы собираем документы только из того, что вы рассказали, а потом отдельной проверкой сверяем каждый пункт с вашим опытом. Что не подтверждается, убираем."],
  ["Что будет с моими данными?",
    "Хранятся на сервере в России. Нейросеть видит обезличенный текст: имя, телефон, почту и фото мы заменяем метками и подставляем обратно только в готовый PDF."],
  ["Сколько это занимает?",
    "Быстрый путь около 2 минут: сам ИИ работает чуть больше минуты. Под конкретную вакансию 5-7 минут вместе с ответами на 2-3 вопроса."],
  ["Вы учитываете компанию, куда я подаюсь?",
    "Да. Читаем сайт компании: чем она занимается, миссию, ценности, как пишет о себе. Под это расставляем акценты в резюме и пишем письмо. Цвета резюме можно взять фирменные, их снимаем с сайта компании."],
  ["Если результат не понравился?",
    "Напишите, что поправить, одним сообщением. Первая правка бесплатная, следующие по 29 ₽ или входят в тариф."],
  ["Можно начать на сайте, а закончить в Telegram?",
    "Да. Кабинет один: войдите на сайте через Telegram или привяжите его в профиле, и все материалы и пакеты появятся в боте. И наоборот."],
  ["Как оплатить?",
    "На сайте картой, в Telegram звездами. Тариф общий: купили в одном месте, пользуетесь везде. Первый пакет бесплатно, карта для этого не нужна."],
];

export default function Landing({ onAuthed }: { onAuthed: () => void }) {
  const [pub, setPub] = useState<Pub | null>(null);
  const [login, setLogin] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { api<Pub>("/billing/public").then(setPub).catch(() => {}); }, []);
  useEffect(() => { if (new URLSearchParams(window.location.search).has("login_error")) setLogin(true); }, []);
  const p = (sku: string) => pub?.products.find((x) => x.sku === sku);
  const bot = pub?.bot_username;

  const start = async () => {
    setBusy(true); setErr("");
    try { await startGuest(); onAuthed(); } catch (e: any) { setErr(e.message); setLogin(true); } finally { setBusy(false); }
  };
  const Cta = ({ big }: { big?: boolean }) => (
    <button className={`btn gold ${big ? "big" : ""}`} disabled={busy} onClick={start}>
      {busy ? "Открываю…" : "Сделать резюме бесплатно"}
    </button>
  );

  return (
    <div className="site">
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
          <div className="stack hero-text">
            <span className="eyebrow">Резюме за 2 минуты, под вакансию за 5-7</span>
            <h1>Ваш реальный опыт на языке работодателя</h1>
            <p className="lead">Пришлите старое резюме или расскажите об опыте голосом, добавьте вакансию. Получите резюме,
              одностраничник и сопроводительное письмо, собранные под требования работодателя.</p>
            <div className="row gap wrap"><Cta big /><button className="btn ghost big" onClick={() => setLogin(true)}>У меня есть кабинет</button></div>
            {err && <p className="error small">{err}</p>}
            <ul className="trust">
              <li>Первый пакет и правка бесплатно</li>
              <li>Без карты и регистрации на старте</li>
              <li>Данные хранятся в России</li>
            </ul>
          </div>
          <div className="hero-card" aria-label="Пример результата">
            <span className="pill">пример</span>
            <div className="doc">
              <div className="doc-line w60 strong" />
              <div className="doc-line w40" />
              <div className="doc-sep" />
              <div className="doc-line w90" /><div className="doc-line w80" /><div className="doc-line w85" />
              <div className="doc-sep" />
              <div className="doc-line w70" /><div className="doc-line w90" /><div className="doc-line w60" />
            </div>
            <div className="hero-score">
              <div className="score-ring"><span>9/10</span></div>
              <div className="stack small-gap">
                <b>Требования вакансии закрыты</b>
                <span className="muted small">Видно, чего не хватает, и можно дописать опыт</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="band">
        <div className="container">
          <h2 className="section-title">Дешевле, безопаснее и точнее конкурентов</h2>
          <div className="grid3">
            <div className="feature">
              <span className="feature-icon">₽</span>
              <h3>Дешевле*</h3>
              <p>Неделя {p("week")?.price_rub ?? 249} ₽ против {RIVAL.week} ₽ у ближайшего конкурента, месяц {p("month")?.price_rub ?? 690} ₽
                против {RIVAL.month.toLocaleString("ru-RU")} ₽. Можно заплатить за один пакет: {p("package")?.price_rub ?? 59} ₽.</p>
            </div>
            <div className="feature">
              <span className="feature-icon">🔒</span>
              <h3>Безопаснее</h3>
              <p>Нейросеть не видит ваше имя, телефон, почту и фото, ей уходит обезличенный текст. Настоящие данные
                появляются только в готовом PDF на нашем сервере в России.</p>
            </div>
            <div className="feature">
              <span className="feature-icon">✓</span>
              <h3>Никаких фантазий</h3>
              <p>Не придумываем опыт, за который вам потом отвечать на собеседовании. Каждый пункт отдельно сверяем
                с тем, что вы рассказали, а неподтвержденное убираем.</p>
            </div>
            <div className="feature">
              <span className="feature-icon">?</span>
              <h3>Верные вопросы и честная оценка</h3>
              <p>Спрашиваем только то, что важно для этой вакансии. Честно показываем, сколько требований закрывает
                ваш опыт и чего не хватает.</p>
            </div>
            <div className="feature">
              <span className="feature-icon">◎</span>
              <h3>Под компанию</h3>
              <p>Учитываем сферу компании, ее миссию, ценности и то, как она пишет о себе. Резюме можно оформить
                в фирменных цветах компании.</p>
            </div>
            <div className="feature">
              <span className="feature-icon">⏱</span>
              <h3>Быстро</h3>
              <p>Резюме из опыта примерно за 2 минуты. Под конкретную вакансию с проработкой требований за 5-7 минут.</p>
            </div>
          </div>
        </div>
      </section>

      <section className="sec" id="how">
        <div className="container">
          <h2 className="section-title">Как это работает</h2>
          <p className="muted center-text">Два пути: быстро собрать резюме из опыта или подготовиться к конкретной вакансии.</p>
          <div className="paths">
            <div className="path">
              <div className="path-head"><span className="pill">быстро</span><b>Резюме из опыта</b><span className="path-time">⏱ около 2 минут</span></div>
              <ol className="how-steps compact">
                <li><b>Закиньте материалы</b><span>Старое резюме, заметки, рассказ голосом. Можно все вперемешку.</span></li>
                <li><b>Соберем профиль</b><span>Выделим опыт и достижения, около 30 секунд.</span></li>
                <li><b>Получите документы</b><span>Резюме и одностраничник в одном стиле, около 45 секунд.</span></li>
              </ol>
            </div>
            <div className="path gold-path">
              <div className="path-head"><span className="pill gold">под вакансию</span><b>Проработка вакансии</b><span className="path-time">⏱ 5-7 минут</span></div>
              <ol className="how-steps compact">
                <li><b>Материалы о себе</b><span>Как в быстром пути.</span></li>
                <li><b>Вакансия и компания</b><span>Ссылка или текст вакансии, по желанию сайт компании: миссия, ценности, фирменные цвета.</span></li>
                <li><b>Оценка соответствия</b><span>Покажем, какие требования ваш опыт уже закрывает, а каких не хватает.</span></li>
                <li><b>2-3 вопроса</b><span>Уточним то, что важно для этой вакансии, но не попало в резюме.</span></li>
                <li><b>Документы под вакансию</b><span>Резюме, одностраничник и письмо на языке вакансии, каждый факт проверен.</span></li>
                <li><b>Доработка</b><span>Покрытие требований до и после, правки одним сообщением.</span></li>
              </ol>
            </div>
          </div>
          <div className="center-row"><Cta /></div>
        </div>
      </section>

      <section className="sec alt" id="example">
        <div className="container">
          <h2 className="section-title">Те же факты, другая подача</h2>
          <p className="muted center-text">Пример. Мы ничего не добавляем от себя, а переписываем ваш опыт языком вакансии.</p>
          <div className="compare">
            <div className="compare-card before">
              <span className="pill">было</span>
              <p>Работал менеджером по продажам. Занимался клиентами, звонки, встречи, CRM. Сделали план в прошлом году,
                взял трех новых крупных клиентов. Обучал новичков.</p>
            </div>
            <div className="compare-card after">
              <span className="pill gold">стало</span>
              <ul>
                <li>Вел клиентов целиком: звонки, встречи, сделки в CRM</li>
                <li>Привлек 3 новых крупных клиентов за год</li>
                <li>Выполнил годовой план</li>
                <li>Обучал новых менеджеров</li>
              </ul>
            </div>
          </div>
        </div>
      </section>

      <section className="sec">
        <div className="container">
          <h2 className="section-title">Что вы получите</h2>
          <div className="grid4">
            <div className="feature"><h3>Резюме</h3><p>Под требования конкретной вакансии, с ключевыми словами из объявления.</p></div>
            <div className="feature"><h3>Одностраничник</h3><p>Когда просят коротко или для первого знакомства.</p></div>
            <div className="feature"><h3>Сопроводительное письмо</h3><p>Почему вы подходите именно на эту позицию, без шаблонных фраз.</p></div>
            <div className="feature"><h3>Покрытие требований</h3><p>Что из вакансии уже закрыто, чего не хватает и как это добавить. Цвета резюме можно взять фирменные.</p></div>
          </div>
        </div>
      </section>

      <section className="sec alt" id="prices">
        <div className="container">
          <h2 className="section-title">Цены</h2>
          <p className="muted center-text">Первый пакет и одна правка бесплатно. Проверка резюме бесплатна всегда.</p>
          <div className="price-grid">
            {(pub?.products || []).map((x) => (
              <div key={x.sku} className={`price ${x.sku === "week" ? "best" : ""}`}>
                {x.sku === "week" && <span className="badge">выгоднее</span>}
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
          <h2 className="section-title">Вопросы</h2>
          <div className="faq">
            {FAQ.map(([q, a]) => <details key={q}><summary>{q}</summary><p>{a}</p></details>)}
          </div>
        </div>
      </section>

      <section className="final">
        <div className="container stack center-text">
          <h2>Следующее собеседование начинается с резюме</h2>
          <p>Первый пакет бесплатно. Резюме за 2 минуты, под вакансию за 5-7.</p>
          <div className="center-row"><Cta big /></div>
          {bot && <p className="small">Удобнее в Telegram? <a href={`https://t.me/${bot}`}>Откройте бота</a>, кабинет тот же.</p>}
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
          <span className="small muted">* Сравнение с тарифами ближайшего по функциям сервиса подготовки резюме на 15.11.2026.</span>
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
