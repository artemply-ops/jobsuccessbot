// Админка владельца: расходы на ИИ, запросы, воронка, деньги, ошибки. Данные: GET /api/admin/stats (только админ).
import { useEffect, useState } from "react";
import { api } from "../api";

type Day = { day: string; cost_usd?: number; packages?: number; revisions?: number; targets?: number; new_users?: number;
  errors?: number; revenue_rub?: number };
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
