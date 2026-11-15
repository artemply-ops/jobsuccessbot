// Админка владельца: расходы на ИИ, запросы, воронка, деньги, ошибки. Данные: GET /api/admin/stats (только админ).
import { useEffect, useState } from "react";
import { api, getConfig } from "../api";

type Day = { day: string; cost_usd?: number; packages?: number; revisions?: number; targets?: number; new_users?: number;
  errors?: number; revenue_rub?: number };
type Src = { source: string; channel: string; users: number; profile: number; documents: number; paid: number;
  revenue_rub: number; revenue_stars: number; paid_rate: number };
type Stats = {
  from: string; to: string; usd_rub: number;
  kpi: { users_total: number; users_new: number; users_active: number; packages: number; revisions: number; targets: number;
    cost_usd: number; cost_rub: number; cost_per_package_usd: number | null; revenue_rub: number; revenue_stars: number;
    promo_redemptions: number; errors: number };
  series: Day[];
  actions: { action: string; runs: number; total_usd: number; avg_usd: number; max_usd: number }[];
  models: { model: string; calls: number; prompt_tokens: number; completion_tokens: number; cost_usd: number }[];
  durations: Record<string, { runs: number; avg_s: number; max_s: number }>;
  channels: Record<string, number>;
  funnel: { step: string; users: number }[];
  billed: Record<string, number>;
  sources: Src[];
  errors: { at: string; kind: string; note: string }[];
};

const ACTION: Record<string, string> = { package: "Пакет документов", revision: "Правка", improve: "Доработка по покрытию",
  profile: "Сборка профиля", review: "Проверка резюме", other: "Прочее", target: "Разбор вакансии", questions: "Уточнения" };
const iso = (d: Date) => d.toISOString().slice(0, 10);
const daysAgo = (n: number) => { const d = new Date(); d.setDate(d.getDate() - n); return iso(d); };
const usd = (v: number) => `$${v.toFixed(v < 1 ? 4 : 2)}`;

function Bars({ data, field, label, fmt }: { data: Day[]; field: keyof Day; label: string; fmt: (v: number) => string }) {
  const vals = data.map((d) => Number(d[field] || 0));
  const max = Math.max(...vals, 0);
  const total = vals.reduce((a, b) => a + b, 0);
  const [hover, setHover] = useState<number | null>(null);
  const w = 100 / Math.max(vals.length, 1);
  return (
    <div className="card stack small-gap">
      <div className="row between"><b>{label}</b><span className="muted small">за период: {fmt(total)}</span></div>
      <div className="chart" onMouseLeave={() => setHover(null)}>
        <svg viewBox="0 0 100 40" preserveAspectRatio="none" role="img" aria-label={label}>
          {vals.map((v, i) => (
            <rect key={i} x={i * w + w * 0.15} width={w * 0.7} y={max ? 40 - (v / max) * 38 : 40} height={max ? (v / max) * 38 : 0}
              rx={0.6} className={hover === i ? "bar on" : "bar"} onMouseEnter={() => setHover(i)} />
          ))}
        </svg>
        {max === 0 && <span className="chart-empty muted small">Нет данных за период</span>}
      </div>
      <div className="row between muted small">
        <span>{data[0]?.day.slice(5)}</span>
        <span>{hover != null ? `${data[hover].day}: ${fmt(vals[hover])}` : ""}</span>
        <span>{data[data.length - 1]?.day.slice(5)}</span>
      </div>
    </div>
  );
}

// Метки: латиница, цифры, «_» и «-», как пускает Telegram в ?start= (services/attribution.py на бэкенде)
const cleanTag = (s: string) => s.trim().toLowerCase().replace(/\s+/g, "_").replace(/[^a-z0-9_-]/g, "").slice(0, 64);

function groupBy(rows: Src[], byChannel: boolean): Src[] {
  if (!byChannel) return rows;
  const m = new Map<string, Src>();
  for (const r of rows) {
    const g = m.get(r.channel) || { source: r.channel, channel: r.channel, users: 0, profile: 0, documents: 0, paid: 0,
      revenue_rub: 0, revenue_stars: 0, paid_rate: 0 };
    g.users += r.users; g.profile += r.profile; g.documents += r.documents; g.paid += r.paid;
    g.revenue_rub += r.revenue_rub; g.revenue_stars += r.revenue_stars;
    m.set(r.channel, g);
  }
  return [...m.values()].map((g) => ({ ...g, paid_rate: g.users ? g.paid / g.users : 0 })).sort((a, b) => b.users - a.users);
}

function Sources({ rows }: { rows: Src[] }) {
  const [byChannel, setByChannel] = useState(true);
  const [tag, setTag] = useState("");
  const [bot, setBot] = useState("");
  useEffect(() => { getConfig().then((c) => setBot(c.bot_username)).catch(() => {}); }, []);
  const t = cleanTag(tag);
  const pct = (a: number, b: number) => (b ? `${Math.round((a / b) * 100)}%` : "—");
  const copy = (s: string) => { navigator.clipboard?.writeText(s).catch(() => {}); };
  const links = t ? [["Бот", bot ? `https://t.me/${bot}?start=${t}` : ""], ["Сайт", `${window.location.origin}/?src=${t}`]] : [];
  return (
    <div className="card stack small-gap">
      <div className="row between wrap gap">
        <b>Источники новых пользователей</b>
        <div className="chips">
          <button className={byChannel ? "on" : ""} onClick={() => setByChannel(true)}>по каналам</button>
          <button className={!byChannel ? "on" : ""} onClick={() => setByChannel(false)}>по меткам</button>
        </div>
      </div>
      <div className="tbl-wrap">
        <table className="tbl"><thead><tr><th>{byChannel ? "Канал" : "Метка"}</th><th>Пришло</th><th>Профиль</th>
          <th>Документы</th><th>Оплатили</th><th>Конверсия</th><th>Выручка</th></tr></thead>
          <tbody>{groupBy(rows, byChannel).map((r) => <tr key={r.source}><td>{r.source === "direct" ? "без метки" : r.source}</td>
            <td>{r.users}</td><td>{r.profile} · {pct(r.profile, r.users)}</td><td>{r.documents} · {pct(r.documents, r.users)}</td>
            <td>{r.paid}</td><td>{pct(r.paid, r.users)}</td>
            <td>{r.revenue_rub} ₽{r.revenue_stars ? ` + ${r.revenue_stars} ⭐` : ""}</td></tr>)}
            {!rows.length && <tr><td colSpan={7} className="muted">Новых пользователей нет</td></tr>}</tbody></table>
      </div>
      <p className="muted small">Когорта: кто пришёл за период. Выручка этой когорты за всё время. Канал это часть метки до «_»:
        friend_ivan и friend_olga попадут в канал friend.</p>
      <label className="small">Ссылка с меткой <input value={tag} placeholder="например friend_ivan или tg_posev1"
        onChange={(e) => setTag(e.target.value)} /></label>
      {links.map(([k, v]) => v && <div key={k} className="row gap wrap small"><span className="muted">{k}:</span>
        <code>{v}</code><button className="link" onClick={() => copy(v)}>копировать</button></div>)}
    </div>
  );
}

export default function AdminPage() {
  const [from, setFrom] = useState(daysAgo(29));
  const [to, setTo] = useState(iso(new Date()));
  const [st, setSt] = useState<Stats | null>(null);
  const [err, setErr] = useState("");
  const load = () => { setErr(""); api<Stats>(`/admin/stats?date_from=${from}&date_to=${to}`).then(setSt).catch((e) => setErr(e.message)); };
  useEffect(load, [from, to]);
  const preset = (n: number) => { setFrom(daysAgo(n - 1)); setTo(iso(new Date())); };

  return (
    <div className="stack admin">
      <div className="row between wrap gap">
        <h2>Админка</h2>
        <a className="link" href="/profile">← В кабинет</a>
      </div>
      <div className="card row gap wrap period">
        <label>от <input type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>до <input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} /></label>
        <div className="chips">
          {[7, 30, 90].map((n) => <button key={n} onClick={() => preset(n)}>{n} дн.</button>)}
        </div>
      </div>
      {err && <div className="banner error">{err}</div>}
      {!st && !err && <div className="card"><span className="spinner" /></div>}
      {st && <>
        <div className="kpis">
          <div className="kpi"><span>Расходы на ИИ</span><b>{usd(st.kpi.cost_usd)}</b><small>≈ {st.kpi.cost_rub} ₽ по курсу {st.usd_rub}</small></div>
          <div className="kpi"><span>Пакет в среднем</span><b>{st.kpi.cost_per_package_usd != null ? usd(st.kpi.cost_per_package_usd) : "—"}</b><small>себестоимость ИИ</small></div>
          <div className="kpi"><span>Выручка</span><b>{st.kpi.revenue_rub} ₽</b><small>{st.kpi.revenue_stars ? `+ ${st.kpi.revenue_stars} ⭐` : "звёзд нет"}</small></div>
          <div className="kpi"><span>Пакетов</span><b>{st.kpi.packages}</b><small>правок {st.kpi.revisions}</small></div>
          <div className="kpi"><span>Вакансий</span><b>{st.kpi.targets}</b><small>добавлено</small></div>
          <div className="kpi"><span>Новых</span><b>{st.kpi.users_new}</b><small>всего {st.kpi.users_total}, активных {st.kpi.users_active}</small></div>
          <div className="kpi"><span>Промокодов</span><b>{st.kpi.promo_redemptions}</b><small>активаций</small></div>
          <div className={`kpi ${st.kpi.errors ? "bad" : ""}`}><span>Ошибок</span><b>{st.kpi.errors}</b><small>в фоновых задачах</small></div>
        </div>

        <div className="grid2">
          <Bars data={st.series} field="cost_usd" label="Расходы на ИИ по дням" fmt={usd} />
          <Bars data={st.series} field="packages" label="Пакеты по дням" fmt={(v) => String(v)} />
          <Bars data={st.series} field="new_users" label="Новые пользователи" fmt={(v) => String(v)} />
          <Bars data={st.series} field="revenue_rub" label="Выручка, ₽" fmt={(v) => `${v} ₽`} />
        </div>

        <div className="grid2">
          <div className="card stack small-gap">
            <b>Расходы по действиям</b>
            <table className="tbl"><thead><tr><th>Действие</th><th>Раз</th><th>Всего</th><th>Среднее</th><th>Макс.</th></tr></thead>
              <tbody>{st.actions.map((a) => <tr key={a.action}><td>{ACTION[a.action] || a.action}</td><td>{a.runs}</td>
                <td>{usd(a.total_usd)}</td><td>{usd(a.avg_usd)}</td><td>{usd(a.max_usd)}</td></tr>)}
                {!st.actions.length && <tr><td colSpan={5} className="muted">Нет вызовов ИИ за период</td></tr>}</tbody></table>
          </div>
          <div className="card stack small-gap">
            <b>Модели</b>
            <table className="tbl"><thead><tr><th>Модель</th><th>Вызовов</th><th>Токены вход/выход</th><th>Сумма</th></tr></thead>
              <tbody>{st.models.map((m) => <tr key={m.model}><td>{m.model.split("/").pop()}</td><td>{m.calls}</td>
                <td>{(m.prompt_tokens / 1000).toFixed(1)}k / {(m.completion_tokens / 1000).toFixed(1)}k</td><td>{usd(m.cost_usd)}</td></tr>)}
                {!st.models.length && <tr><td colSpan={4} className="muted">—</td></tr>}</tbody></table>
          </div>
        </div>

        <div className="grid2">
          <div className="card stack small-gap">
            <b>Воронка новых пользователей</b>
            {st.funnel.map((f, i) => {
              const base = st.funnel[0].users || 1;
              return (
                <div key={f.step} className="funnel-row">
                  <span className="small">{f.step}</span>
                  <div className="funnel-bar"><i style={{ width: `${(f.users / base) * 100}%` }} /></div>
                  <b className="small">{f.users}{i > 0 && st.funnel[0].users ? ` · ${Math.round((f.users / base) * 100)}%` : ""}</b>
                </div>
              );
            })}
          </div>
          <div className="card stack small-gap">
            <b>Каналы и скорость</b>
            <ul className="small">{Object.entries(st.channels).map(([k, v]) => <li key={k}>{k}: {v}</li>)}
              {!Object.keys(st.channels).length && <li className="muted">Новых пользователей нет</li>}</ul>
            <ul className="small">{Object.entries(st.durations).map(([k, d]) => (
              <li key={k}>{ACTION[k] || k}: в среднем {d.avg_s} с, максимум {d.max_s} с ({d.runs} шт.)</li>))}</ul>
          </div>
        </div>

        <Sources rows={st.sources || []} />

        {st.errors.length > 0 && (
          <div className="card stack small-gap">
            <b>Последние ошибки</b>
            <ul className="small">{st.errors.map((e, i) => <li key={i}>{new Date(e.at).toLocaleString("ru-RU")} · {ACTION[e.kind] || e.kind}: {e.note}</li>)}</ul>
          </div>
        )}
      </>}
    </div>
  );
}
