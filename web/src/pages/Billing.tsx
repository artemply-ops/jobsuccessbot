// Оплата и тариф: экран «лимит закончился», карточка тарифа в профиле, промокод, возврат с оплаты, админка промокодов.
// Внутри Telegram платим звёздами (правило Telegram для цифровых товаров), в браузере картой через ЮKassa.
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, inTelegram, tg } from "../api";

export type Product = { sku: string; title: string; desc: string; price_rub: number; price_stars: number; days: number | null };
export type BillingInfo = {
  enabled: boolean; admin: boolean; left: { package: number; revision: number };
  plan: { sku: string; title: string; until: string; left: Record<string, number> } | null;
  products: Product[]; pay: { yookassa: boolean; stars: boolean };
};

export const NEED_TEXT: Record<string, string> = {
  package: "Бесплатный пакет уже использован. Чтобы собрать новый, выберите вариант ниже или введите промокод.",
  revision: "Бесплатная правка уже использована. Чтобы внести правку, выберите вариант ниже или введите промокод.",
};

export function leftText(b: BillingInfo) {
  if (b.admin || !b.enabled) return "Без ограничений";
  const until = b.plan ? ` · тариф «${b.plan.title}» до ${new Date(b.plan.until).toLocaleDateString("ru-RU")}` : "";
  return `Осталось: пакетов ${b.left.package}, правок ${b.left.revision}${until}`;
}

const price = (p: Product) => (inTelegram() ? `${p.price_stars} ⭐` : `${p.price_rub} ₽`);

// Покупка: в Telegram счёт в звёздах внутри окна, в браузере переход на страницу оплаты ЮKassa
async function buy(p: Product, onPaid: () => void): Promise<string> {
  const provider = inTelegram() ? "stars" : "yookassa";
  const r = await api<{ payment_id: number; url?: string; invoice_link?: string }>("/billing/checkout",
    { method: "POST", json: { sku: p.sku, provider } });
  if (r.url) {
    window.location.href = r.url;
    return "Перехожу к оплате…";
  }
  return await new Promise((resolve) => {
    tg().openInvoice(r.invoice_link, async (status: string) => {
      if (status !== "paid") return resolve(status === "cancelled" ? "" : "Оплата не прошла");
      for (let i = 0; i < 10; i++) {  // грант выдаёт бот по successful_payment: ждём пару секунд
        const s = await api<{ status: string }>(`/billing/payments/${r.payment_id}`);
        if (s.status === "succeeded") break;
        await new Promise((x) => setTimeout(x, 1500));
      }
      onPaid();
      resolve(`✓ Оплачено: ${p.title}`);
    });
  });
}

function Products({ info, need, onChange }: { info: BillingInfo; need?: string; onChange: () => void }) {
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState("");
  const canPay = inTelegram() ? info.pay.stars : info.pay.yookassa;
  const list = [...info.products].sort((a, b) => Number(b.sku === need) - Number(a.sku === need));
  return (
    <div className="stack">
      <div className="plans">
        {list.map((p) => (
          <div key={p.sku} className={`plan ${p.sku === need ? "accent" : ""}`}>
            <b>{p.title}</b>
            <span className="plan-price">{price(p)}</span>
            <span className="muted small">{p.desc}</span>
            {canPay && (
              <button className="btn" disabled={!!busy} onClick={async () => {
                setBusy(p.sku); setMsg("");
                try { setMsg(await buy(p, onChange)); } catch (e: any) { setMsg(`⚠️ ${e.message}`); } finally { setBusy(""); }
              }}>{busy === p.sku ? "…" : "Купить"}</button>
            )}
          </div>
        ))}
      </div>
      {!canPay && <p className="muted small">Оплата {inTelegram() ? "звёздами" : "картой"} скоро появится. Пока можно ввести промокод.</p>}
      {msg && <div className={`banner ${msg.startsWith("✓") ? "ok" : msg.startsWith("⚠️") ? "error" : ""}`}>{msg}</div>}
      <PromoInput onDone={onChange} />
      {!inTelegram() && canPay && <p className="muted small">Нажимая «Купить», вы принимаете <Link to="/offer">оферту</Link>.</p>}
    </div>
  );
}

export function PromoInput({ onDone }: { onDone: () => void }) {
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState("");
  return (
    <div className="stack">
      <div className="row gap">
        <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="Промокод" />
        <button className="btn secondary" disabled={!code.trim()} onClick={async () => {
          try {
            const r = await api<{ title: string }>("/billing/promo", { method: "POST", json: { code: code.trim() } });
            setMsg(`✓ Активировано: ${r.title}`); setCode(""); onDone();
          } catch (e: any) { setMsg(`⚠️ ${e.message}`); }
        }}>Активировать</button>
      </div>
      {msg && <div className={`banner ${msg.startsWith("✓") ? "ok" : "error"}`}>{msg}</div>}
    </div>
  );
}

// Лимит закончился: показывается вместо ошибки, после покупки пользователь повторяет действие
export function Paywall({ need, onClose }: { need: string; onClose: () => void }) {
  const [info, setInfo] = useState<BillingInfo | null>(null);
  const load = () => api<BillingInfo>("/billing").then(setInfo).catch(() => {});
  useEffect(() => { load(); }, []);
  const ok = info && (info.left as any)[need] > 0;
  return (
    <div className="card stack">
      <h2>Нужен тариф</h2>
      <p>{NEED_TEXT[need] || NEED_TEXT.package}</p>
      {info && <p className="muted small">{leftText(info)}</p>}
      {ok && <div className="banner ok">Готово, лимит пополнен. Повторите действие.</div>}
      {info && <Products info={info} need={need} onChange={load} />}
      <button className="link" onClick={onClose}>Закрыть</button>
    </div>
  );
}

// Карточка в профиле: остатки, покупка, промокод; администратору выпуск промокодов и расходы
export function PlanCard() {
  const [info, setInfo] = useState<BillingInfo | null>(null);
  const load = () => api<BillingInfo>("/billing").then(setInfo).catch(() => {});
  useEffect(() => { load(); }, []);
  if (!info) return null;
  return (
    <div className="card stack">
      <h2>Тариф</h2>
      <p>{leftText(info)}</p>
      <p className="muted small">Бесплатно всегда: проверка резюме, покрытие требований, смена оформления.
        Первый пакет и одна правка бесплатно.</p>
      {!info.admin && <Products info={info} onChange={load} />}
      {info.admin && <AdminBox />}
    </div>
  );
}

type Promo = { code: string; title: string; used: number; max_uses: number; expires_at: string | null };
type Usage = { total_usd: number; actions: { action: string; runs: number; avg_usd: number; max_usd: number }[];
  billed: Record<string, number> };

function AdminBox() {
  const [promos, setPromos] = useState<Promo[]>([]);
  const [skus, setSkus] = useState<{ sku: string; title: string }[]>([]);
  const [form, setForm] = useState({ sku: "week", uses: 10, days: 30, code: "" });
  const [msg, setMsg] = useState("");
  const [usage, setUsage] = useState<Usage | null>(null);
  const load = () => api<{ promos: Promo[]; skus: { sku: string; title: string }[] }>("/admin/promo")
    .then((r) => { setPromos(r.promos); setSkus(r.skus); }).catch(() => {});
  useEffect(() => { load(); api<Usage>("/admin/usage?days=30").then(setUsage).catch(() => {}); }, []);
  return (
    <div className="stack">
      <h3>Промокоды</h3>
      <div className="row gap wrap">
        <select value={form.sku} onChange={(e) => setForm({ ...form, sku: e.target.value })}>
          {skus.map((s) => <option key={s.sku} value={s.sku}>{s.title}</option>)}
        </select>
        <label className="small">активаций <input type="number" min={1} value={form.uses}
          onChange={(e) => setForm({ ...form, uses: Number(e.target.value) })} style={{ width: 70 }} /></label>
        <label className="small">дней <input type="number" min={1} value={form.days}
          onChange={(e) => setForm({ ...form, days: Number(e.target.value) })} style={{ width: 70 }} /></label>
        <input value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="свой код (необязательно)" />
        <button className="btn" onClick={async () => {
          try {
            const r = await api<{ code: string }>("/admin/promo", { method: "POST", json: { ...form, code: form.code || null } });
            setMsg(`✓ Создан ${r.code}`); load();
          } catch (e: any) { setMsg(`⚠️ ${e.message}`); }
        }}>Создать</button>
      </div>
      {msg && <div className={`banner ${msg.startsWith("✓") ? "ok" : "error"}`}>{msg}</div>}
      <ul className="small">{promos.map((p) => <li key={p.code}><code>{p.code}</code> · {p.title} · {p.used}/{p.max_uses}
        {p.expires_at ? ` · до ${new Date(p.expires_at).toLocaleDateString("ru-RU")}` : ""}</li>)}</ul>
      {usage && (
        <>
          <h3>Расходы на ИИ за 30 дней: ${usage.total_usd}</h3>
          <ul className="small">
            {usage.actions.map((a) => <li key={a.action}>{a.action}: {a.runs} шт., в среднем ${a.avg_usd}, максимум ${a.max_usd}</li>)}
            <li>Платных действий: пакетов {usage.billed.package || 0}, правок {usage.billed.revision || 0}</li>
          </ul>
        </>
      )}
    </div>
  );
}

// Возврат со страницы ЮKassa: проверяем статус, пока платёж не подтвердится
export function PayReturn() {
  const [params] = useSearchParams();
  const [status, setStatus] = useState("pending");
  const [title, setTitle] = useState("");
  useEffect(() => {
    const pid = params.get("pid");
    if (!pid) return setStatus("error");
    let stop = false;
    (async () => {
      for (let i = 0; i < 20 && !stop; i++) {
        try {
          const r = await api<{ status: string; title: string }>(`/billing/payments/${pid}`);
          setTitle(r.title);
          if (r.status !== "pending") return setStatus(r.status);
        } catch { return setStatus("error"); }
        await new Promise((x) => setTimeout(x, 3000));
      }
    })();
    return () => { stop = true; };
  }, []);
  return (
    <div className="card stack">
      <h2>Оплата</h2>
      {status === "pending" && <p>Проверяю оплату…</p>}
      {status === "succeeded" && <div className="banner ok">✓ Оплачено: {title}. Можно продолжать.</div>}
      {status === "canceled" && <div className="banner error">Оплата отменена. Деньги не списаны.</div>}
      {status === "error" && <div className="banner error">Не удалось проверить оплату. Загляните в «Профиль → Тариф» через минуту.</div>}
      <Link className="btn" to="/make">Продолжить</Link>
    </div>
  );
}
