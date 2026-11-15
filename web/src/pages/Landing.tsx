// Лендинг для браузера без входа: слоган, обоснование, как работает, цены, вход.
// Цены берутся с сервера (/api/billing/public), чтобы не расходились с ботом и оплатой.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Product } from "./Billing";

// Цены конкурента на 15.11.2026 (docs/ECONOMICS.md). Сравнение на сайте должно быть проверяемым: дата в сноске
const RIVAL = { week: 349, month: 1049 };

export default function Landing({ login }: { login: React.ReactNode }) {
  const [products, setProducts] = useState<Product[]>([]);
  const [bot, setBot] = useState("");
  useEffect(() => {
    api<{ products: Product[]; bot_username: string }>("/billing/public")
      .then((r) => { setProducts(r.products); setBot(r.bot_username); }).catch(() => {});
  }, []);
  const p = (sku: string) => products.find((x) => x.sku === sku);

  return (
    <div className="landing stack">
      <section className="stack">
        <h1>Резюме под вакансию. Дешевле, безопаснее и точнее конкурентов</h1>
        <p>Пришлите старое резюме или наговорите опыт голосом, добавьте вакансию. Примерно через минуту получите
          резюме, версию на одну страницу и сопроводительное письмо.</p>
        <div className="stack narrow">{login}</div>
        {bot && <a className="btn secondary" href={`https://t.me/${bot}`}>Открыть бота в Telegram</a>}
      </section>

      <section className="grid3">
        <div className="card stack">
          <h2>Дешевле*</h2>
          <p>Неделя стоит {p("week")?.price_rub ?? 249} ₽, у ближайшего конкурента {RIVAL.week} ₽.
            Месяц {p("month")?.price_rub ?? 690} ₽ против {RIVAL.month.toLocaleString("ru-RU")} ₽.
            Можно заплатить только за один пакет, это {p("package")?.price_rub ?? 59} ₽.
            Первый пакет и одна правка бесплатно.</p>
        </div>
        <div className="card stack">
          <h2>Безопаснее</h2>
          <p>Имя, телефон, почту и фото нейросеть не видит, ей уходит обезличенный текст. Настоящие данные мы
            подставляем уже в готовый PDF. Хранятся они в России.</p>
        </div>
        <div className="card stack">
          <h2>Точнее</h2>
          <p>Каждый пункт резюме сверяем с вашим опытом. Что не подтверждается, убираем. Видно, сколько требований
            вакансии закрыто и чего не хватает.</p>
        </div>
      </section>

      <section className="card stack">
        <h2>Как это работает</h2>
        <ol>
          <li>Материалы: файл, скан, фото или голос.</li>
          <li>Вакансия: ссылка на сайт компании или текст.</li>
          <li>Два-три уточнения.</li>
          <li>Готовые PDF в одном стиле.</li>
        </ol>
      </section>

      {products.length > 0 && (
        <section className="card stack">
          <h2>Цены</h2>
          <div className="plans">
            {products.map((x) => (
              <div key={x.sku} className="plan">
                <b>{x.title}</b>
                <span className="plan-price">{x.price_rub} ₽</span>
                <span className="muted small">{x.desc}</span>
              </div>
            ))}
          </div>
          <p className="muted small">Бесплатно всегда: проверка резюме и покрытие требований вакансии.
            В Telegram оплата звёздами по курсу.</p>
        </section>
      )}

      <footer className="muted small stack">
        <span>* Сравнение с тарифами ближайшего по функциям сервиса подготовки резюме на 15.11.2026.</span>
        <span><Link to="/offer">Оферта</Link> · <Link to="/privacy">Политика обработки персональных данных</Link></span>
      </footer>
    </div>
  );
}
