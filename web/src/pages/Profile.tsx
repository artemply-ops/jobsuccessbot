import { useCallback, useEffect, useRef, useState } from "react";
import { api, upload, waitJob } from "../api";
import type { Me } from "../App";

type Source = { id: number; kind: string; filename: string | null; status: string; error: string | null; preview: string };
type Skill = { name: string; level: string; years: number | null; source_ids: number[] };
type ProfileData = {
  headline: string; summary: string; target_roles: string[]; seniority: string; skills: Skill[];
  experience: { company: string; role: string; period: string; industry: string | null; achievements: { text: string }[] }[];
  industries: string[]; education: string[]; languages: string[];
  expectations: { salary_min: number | null; currency: string; cities: string[]; formats: string[]; relocation: boolean | null };
  values: string[]; stop_factors: string[]; writing_style: string;
};
type Profile = { version: number; data: ProfileData; questions: { field: string; question: string }[]; confirmed: boolean };

const KIND: Record<string, string> = { file: "Файл", voice: "Голос", text: "Текст", link: "Ссылка" };
const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

export default function ProfilePage({ me, onMeChange }: { me: Me; onMeChange: () => void }) {
  const [sources, setSources] = useState<Source[]>([]);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [text, setText] = useState("");
  const [answer, setAnswer] = useState("");
  const [edit, setEdit] = useState<ProfileData | null>(null);

  const load = useCallback(async () => {
    const [s, p] = await Promise.all([api<Source[]>("/sources"), api<Profile | null>("/profile")]);
    setSources(s);
    setProfile(p);
  }, []);
  useEffect(() => { load().catch((e) => setErr(e.message)); }, [load]);

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setErr("");
    try {
      await fn();
      await load();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy("");
    }
  };

  const onFiles = (files: FileList | null) =>
    files && run("Загружаю файлы…", async () => {
      for (const f of Array.from(files)) await upload("/sources/file", f, f.name);
    });

  const build = () => run("Собираю профиль, до минуты…", async () => {
    const { job_id } = await api("/profile/build", { method: "POST", json: {} });
    await waitJob(job_id);
  });

  const sendAnswer = () => run("Обновляю профиль…", async () => {
    const { job_id } = await api("/profile/answers", { method: "POST", json: { text: answer } });
    await waitJob(job_id);
    setAnswer("");
  });

  const save = (confirm: boolean) => edit && run("Сохраняю…", async () => {
    await api("/profile", { method: "PUT", json: { data: edit, confirm } });
    setEdit(null);
  });

  return (
    <div className="stack">
      {busy && <div className="banner">{busy}</div>}
      {err && <div className="banner error">{err}</div>}

      <section className="card stack">
        <h2>1. Материалы о вас</h2>
        <p className="muted small">Резюме, описание проектов, отзывы и рекомендации. PDF, DOCX или TXT, до 15 МБ.</p>
        <label className="btn secondary file">
          Загрузить файлы
          <input type="file" multiple accept=".pdf,.docx,.txt,.md" onChange={(e) => onFiles(e.target.files)} hidden />
        </label>
        <VoiceRecorder onDone={(blob, name) => run("Распознаю голос…", () => upload("/sources/voice", blob, name))} />
        <textarea rows={3} placeholder="Или напишите о себе: чем занимались, чего хотите, что важно" value={text}
          onChange={(e) => setText(e.target.value)} />
        <button className="btn secondary" disabled={!text.trim()}
          onClick={() => run("Сохраняю…", async () => { await api("/sources/text", { method: "POST", json: { text } }); setText(""); })}>
          Добавить текст
        </button>
        {sources.length > 0 && (
          <ul className="sources">
            {sources.map((s) => (
              <li key={s.id} className={s.status === "error" ? "error" : ""}>
                <span className="tag">{KIND[s.kind] || s.kind}</span>
                <span className="grow ellipsis">{s.filename || s.preview}</span>
                {s.error && <span className="small">{s.error}</span>}
                <button className="link" onClick={() => run("Удаляю…", () => api(`/sources/${s.id}`, { method: "DELETE" }))}>✕</button>
              </li>
            ))}
          </ul>
        )}
        <button className="btn" disabled={!sources.some((s) => s.status === "parsed") || !!busy} onClick={build}>
          {profile ? "Пересобрать профиль по всем материалам" : "Собрать профиль"}
        </button>
      </section>

      <Contacts me={me} onSaved={onMeChange} />

      {profile && !edit && (
        <section className="card stack">
          <div className="row">
            <h2 className="grow">2. Профиль · v{profile.version}</h2>
            {profile.confirmed ? <span className="tag ok">подтверждён</span> : <span className="tag warn">не подтверждён</span>}
          </div>
          <ProfileView d={profile.data} />
          <div className="row gap">
            <button className="btn secondary" onClick={() => setEdit(structuredClone(profile.data))}>Редактировать</button>
            {!profile.confirmed && (
              <button className="btn" onClick={() => run("Сохраняю…", () =>
                api("/profile", { method: "PUT", json: { data: profile.data, confirm: true } }))}>Всё верно</button>
            )}
          </div>
        </section>
      )}
      {edit && <ProfileEditor d={edit} onChange={setEdit} onSave={save} onCancel={() => setEdit(null)} />}

      {profile && profile.questions.length > 0 && (
        <section className="card stack">
          <h2>3. Уточните, пожалуйста</h2>
          <ul>{profile.questions.map((q, i) => <li key={i}>{q.question}</li>)}</ul>
          <textarea rows={4} placeholder="Ответьте в свободной форме, можно на все вопросы сразу" value={answer}
            onChange={(e) => setAnswer(e.target.value)} />
          <button className="btn" disabled={!answer.trim() || !!busy} onClick={sendAnswer}>Отправить ответы</button>
        </section>
      )}
    </div>
  );
}

function ProfileView({ d }: { d: ProfileData }) {
  const ex = d.expectations;
  return (
    <div className="stack small-gap">
      <strong>{d.headline}</strong>
      <p>{d.summary}</p>
      <Field k="Целевые роли" v={d.target_roles.join(", ")} />
      <Field k="Уровень" v={d.seniority} />
      <Field k="Навыки" v={d.skills.map((s) => `${s.name} (${s.level}${s.years ? `, ${s.years} г.` : ""})`).join(", ")} />
      <Field k="Отрасли" v={d.industries.join(", ")} />
      <Field k="Ожидания" v={`от ${ex.salary_min ?? "?"} ${ex.currency}; ${ex.cities.join(", ") || "город ?"}; ${ex.formats.join(", ") || "формат ?"}`} />
      <Field k="Ценности" v={d.values.join(", ")} />
      <Field k="Стоп-факторы" v={d.stop_factors.join(", ")} />
      <details>
        <summary>Опыт ({d.experience.length})</summary>
        {d.experience.map((e, i) => (
          <div key={i} className="exp">
            <strong>{e.role}</strong> — {e.company} <span className="muted">{e.period}</span>
            <ul>{e.achievements.map((a, j) => <li key={j}>{a.text}</li>)}</ul>
          </div>
        ))}
      </details>
    </div>
  );
}

const Field = ({ k, v }: { k: string; v: string }) => (
  <div className="field"><span className="muted">{k}</span><span>{v || "—"}</span></div>
);

function ProfileEditor({ d, onChange, onSave, onCancel }: {
  d: ProfileData; onChange: (d: ProfileData) => void; onSave: (confirm: boolean) => void; onCancel: () => void;
}) {
  const set = (patch: Partial<ProfileData>) => onChange({ ...d, ...patch });
  const setEx = (patch: Partial<ProfileData["expectations"]>) => set({ expectations: { ...d.expectations, ...patch } });
  return (
    <section className="card stack">
      <h2>Редактирование профиля</h2>
      <label>Заголовок<input value={d.headline} onChange={(e) => set({ headline: e.target.value })} /></label>
      <label>О себе<textarea rows={4} value={d.summary} onChange={(e) => set({ summary: e.target.value })} /></label>
      <label>Целевые роли, через запятую
        <input value={d.target_roles.join(", ")} onChange={(e) => set({ target_roles: list(e.target.value) })} /></label>
      <label>Уровень<select value={d.seniority} onChange={(e) => set({ seniority: e.target.value })}>
        {["", "junior", "middle", "senior", "lead", "head", "c-level"].map((x) => <option key={x}>{x}</option>)}
      </select></label>
      <label>Навыки, через запятую
        <input value={d.skills.map((s) => s.name).join(", ")}
          onChange={(e) => set({ skills: list(e.target.value).map((name) => d.skills.find((s) => s.name === name) || { name, level: "confident", years: null, source_ids: [] }) })} /></label>
      <label>Зарплата от, в месяц
        <input type="number" value={d.expectations.salary_min ?? ""} onChange={(e) => setEx({ salary_min: e.target.value ? Number(e.target.value) : null })} /></label>
      <label>Города, через запятую<input value={d.expectations.cities.join(", ")} onChange={(e) => setEx({ cities: list(e.target.value) })} /></label>
      <div className="row gap wrap">
        {["office", "hybrid", "remote"].map((f) => (
          <label key={f} className="check">
            <input type="checkbox" checked={d.expectations.formats.includes(f)}
              onChange={(e) => setEx({ formats: e.target.checked ? [...d.expectations.formats, f] : d.expectations.formats.filter((x) => x !== f) })} />
            {{ office: "Офис", hybrid: "Гибрид", remote: "Удалёнка" }[f]}
          </label>
        ))}
      </div>
      <label>Ценности, через запятую<input value={d.values.join(", ")} onChange={(e) => set({ values: list(e.target.value) })} /></label>
      <label>Стоп-факторы, через запятую<input value={d.stop_factors.join(", ")} onChange={(e) => set({ stop_factors: list(e.target.value) })} /></label>
      <div className="row gap">
        <button className="btn" onClick={() => onSave(true)}>Сохранить и подтвердить</button>
        <button className="btn secondary" onClick={() => onSave(false)}>Сохранить</button>
        <button className="link" onClick={onCancel}>Отмена</button>
      </div>
    </section>
  );
}

function Contacts({ me, onSaved }: { me: Me; onSaved: () => void }) {
  const [c, setC] = useState(me.contact || { full_name: "", email: "", phone: "", city: "" });
  const [open, setOpen] = useState(false);
  const save = async () => {
    await api("/contact", { method: "PUT", json: c });
    setOpen(false);
    onSaved();
  };
  return (
    <section className="card stack">
      <div className="row">
        <h2 className="grow">Контакты для CV</h2>
        <button className="link" onClick={() => setOpen(!open)}>{open ? "Свернуть" : "Изменить"}</button>
      </div>
      <p className="muted small">Попадают только в итоговые файлы. В ИИ-модель не отправляются.</p>
      {!open && <p>{[c.full_name, c.email, c.phone, c.city].filter(Boolean).join(" · ") || "Не заданы"}</p>}
      {open && (
        <>
          <input placeholder="ФИО" value={c.full_name || ""} onChange={(e) => setC({ ...c, full_name: e.target.value })} />
          <input placeholder="Email" value={c.email || ""} onChange={(e) => setC({ ...c, email: e.target.value })} />
          <input placeholder="Телефон" value={c.phone || ""} onChange={(e) => setC({ ...c, phone: e.target.value })} />
          <input placeholder="Город" value={c.city || ""} onChange={(e) => setC({ ...c, city: e.target.value })} />
          <button className="btn" onClick={save}>Сохранить</button>
        </>
      )}
    </section>
  );
}

function VoiceRecorder({ onDone }: { onDone: (b: Blob, name: string) => void }) {
  const [rec, setRec] = useState<MediaRecorder | null>(null);
  const [sec, setSec] = useState(0);
  const timer = useRef<number | undefined>(undefined);

  const start = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mr = new MediaRecorder(stream);
    const chunks: Blob[] = [];
    mr.ondataavailable = (e) => chunks.push(e.data);
    mr.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      clearInterval(timer.current);
      const type = mr.mimeType || "audio/webm";
      onDone(new Blob(chunks, { type }), `voice.${type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm"}`);
      setRec(null);
    };
    mr.start();
    setRec(mr);
    setSec(0);
    timer.current = window.setInterval(() => setSec((s) => {
      if (s + 1 >= 30) mr.stop();
      return s + 1;
    }), 1000);
  };

  if (!navigator.mediaDevices?.getUserMedia) return null;
  return rec ? (
    <button className="btn danger" onClick={() => rec.stop()}>● Остановить ({30 - sec} с)</button>
  ) : (
    <button className="btn secondary" onClick={() => start().catch(() => alert("Нет доступа к микрофону"))}>🎙 Надиктовать (до 30 с)</button>
  );
}
