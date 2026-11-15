import React, { useCallback, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api, inTelegram, login, setToken } from "./api";
import { LoginBox } from "./pages/Auth";
import { disablePush } from "./push";
import ProfilePage from "./pages/Profile";
import MakePage from "./pages/Make";
import Landing from "./pages/Landing";
import { Offer, Privacy } from "./pages/Legal";
import { PayReturn } from "./pages/Billing";
import AdminPage from "./pages/Admin";
import AdminTexts from "./pages/AdminTexts";

// Страницы без входа: юридические тексты нужны ЮKassa и пользователю до оплаты
const PUBLIC: Record<string, React.ComponentType> = { "/offer": Offer, "/privacy": Privacy };

export type Me = {
  id: number;
  first_name: string | null;
  consent: boolean;
  guest: boolean;  // начал на сайте без входа: документы соберёт после входа
  telegram: boolean;
  notify_threshold: number;
  is_admin: boolean;
  contact: { full_name: string | null; email: string | null; phone: string | null; city: string | null } | null;
};

export default function App() {
  const [state, setState] = useState<"loading" | "anon" | "ready">("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState("");
  const [loginOpen, setLoginOpen] = useState(false);

  const loadMe = useCallback(async () => {
    try {
      setMe(await api<Me>("/me"));
      setState("ready");
    } catch {
      setState("anon");
    }
  }, []);

  useEffect(() => {
    login()
      .then((ok) => (ok ? loadMe() : setState("anon")))
      .catch((e) => {
        setError(e.message);
        setState("anon");
      });
    const onLogout = () => setState("anon");
    const onMe = () => loadMe();  // вход внутри приложения (гость вошёл): обновить шапку и профиль
    const onLogin = () => setLoginOpen(true);  // «Сохранить в кабинете» из мастера
    window.addEventListener("jp-logout", onLogout);
    window.addEventListener("jp-me", onMe);
    window.addEventListener("jp-login", onLogin);
    return () => {
      window.removeEventListener("jp-logout", onLogout);
      window.removeEventListener("jp-me", onMe);
      window.removeEventListener("jp-login", onLogin);
    };
  }, [loadMe]);

  const { pathname } = useLocation();
  const Pub = PUBLIC[pathname];
  if (Pub) return <div className="content"><Pub /><p><a href="/">← JobPilot</a></p></div>;
  // Предпросмотр лендинга для админа, который уже вошёл (иначе он видит приложение, а не сайт)
  if (pathname === "/site") return <Landing onAuthed={() => { window.location.href = "/make"; }} />;
  if (state === "loading") return <div className="center muted">Загрузка…</div>;
  if (state === "anon") return inTelegram()
    ? <div className="center narrow">{error && <p className="error">{error}</p>}<LoginBox onDone={loadMe} /></div>
    : <Landing onAuthed={loadMe} />;
  if (me && !me.consent) return <Consent onDone={loadMe} onCancel={me.guest ? () => { setToken(null); setState("anon"); } : undefined} />;

  return (
    <div className="app">
      <header className="topbar">
        <a href="/make" className="logo" onClick={() => { try { localStorage.removeItem("jp_make"); } catch { /* */ } }}>
          <span className="logo-mark">J</span>JobPilot</a>
        {!inTelegram() && (me?.guest
          ? <button className="btn gold" onClick={() => setLoginOpen(true)}>Сохранить в кабинете</button>
          : <button className="link" onClick={async () => {
              await disablePush().catch(() => {});  // чужие пуши на общем компьютере не нужны
              setToken(null); setState("anon");
            }}>Выйти</button>)}
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<Navigate to="/make" replace />} />
          <Route path="/make" element={<MakePage guest={!!me?.guest} />} />
          <Route path="/profile" element={<ProfilePage me={me!} onMeChange={loadMe} />} />
          <Route path="/pay/return" element={<PayReturn />} />
          {me?.is_admin && <Route path="/admin" element={<AdminPage />} />}
          {me?.is_admin && <Route path="/admin/texts" element={<AdminTexts />} />}
          <Route path="*" element={<Navigate to="/make" replace />} />
        </Routes>
      </main>
      <nav className="tabbar">
        {/* Пивот 08.11: поиск вакансий и каталог компаний скрыты (pages/Vacancies, Vacancy, Companies не подключены) */}
        <NavLink to="/make">Резюме</NavLink>
        <NavLink to="/profile">Кабинет</NavLink>
      </nav>
      {loginOpen && (
        <div className="modal" onClick={(e) => { if (e.target === e.currentTarget) setLoginOpen(false); }}>
          <div className="modal-body">
            <button className="modal-close" aria-label="Закрыть" onClick={() => setLoginOpen(false)}>×</button>
            <LoginBox onDone={() => { setLoginOpen(false); loadMe(); }} title="Войдите, чтобы сохранить работу"
              note="Всё, что вы уже загрузили, останется в кабинете." />
          </div>
        </div>
      )}
    </div>
  );
}

function Consent({ onDone, onCancel }: { onDone: () => void; onCancel?: () => void }) {
  const accept = async () => {
    await api("/me/consent", { method: "POST" });
    onDone();
  };
  return (
    <div className="center stack narrow">
      <h2>Согласие на обработку данных</h2>
      <p>
        Чтобы собрать профиль и подготовить CV, сервис обрабатывает ваши персональные данные: резюме, контакты,
        голосовые сообщения. Данные хранятся в России. Во внешнюю ИИ-модель уходит только обезличенный текст, без
        имени и контактов.
      </p>
      <p className="small">Подробно: <a href="/privacy" target="_blank">политика обработки персональных данных</a>.</p>
      <button className="btn" onClick={accept}>Согласен</button>
      {onCancel && <button className="link" onClick={onCancel}>Вернуться на главную</button>}
    </div>
  );
}
