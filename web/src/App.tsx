import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, inTelegram, login, setToken } from "./api";
import ProfilePage from "./pages/Profile";
import VacanciesPage from "./pages/Vacancies";
import VacancyPage from "./pages/Vacancy";
import CompaniesPage from "./pages/Companies";

export type Me = {
  id: number;
  first_name: string | null;
  consent: boolean;
  notify_threshold: number;
  is_admin: boolean;
  contact: { full_name: string | null; email: string | null; phone: string | null; city: string | null } | null;
};

export default function App() {
  const [state, setState] = useState<"loading" | "anon" | "ready">("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState("");

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
    window.addEventListener("jp-logout", onLogout);
    return () => window.removeEventListener("jp-logout", onLogout);
  }, [loadMe]);

  if (state === "loading") return <div className="center muted">Загрузка…</div>;
  if (state === "anon") return <Login onDone={loadMe} error={error} />;
  if (me && !me.consent) return <Consent onDone={loadMe} />;

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">JobPilot</span>
        {!inTelegram() && (
          <button className="link" onClick={() => { setToken(null); setState("anon"); }}>Выйти</button>
        )}
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<Navigate to="/profile" replace />} />
          <Route path="/profile" element={<ProfilePage me={me!} onMeChange={loadMe} />} />
          <Route path="/vacancies" element={<VacanciesPage />} />
          <Route path="/vacancies/:id" element={<VacancyPage />} />
          <Route path="/companies" element={<CompaniesPage />} />
          <Route path="*" element={<Navigate to="/profile" replace />} />
        </Routes>
      </main>
      <nav className="tabbar">
        <NavLink to="/profile">Профиль</NavLink>
        <NavLink to="/vacancies">Вакансии</NavLink>
        <NavLink to="/companies">Компании</NavLink>
      </nav>
    </div>
  );
}

function Login({ onDone, error }: { onDone: () => void; error: string }) {
  const [cfg, setCfg] = useState<{ bot_username: string; dev_login: boolean } | null>(null);
  const [err, setErr] = useState(error);
  const widgetRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api("/config").then(setCfg).catch(() => setCfg({ bot_username: "", dev_login: false }));
  }, []);

  useEffect(() => {
    if (!cfg?.bot_username || !widgetRef.current) return;
    window.onTelegramAuth = async (user) => {
      try {
        const { token } = await api("/auth/telegram-login", { method: "POST", json: user });
        setToken(token);
        onDone();
      } catch (e: any) {
        setErr(e.message);
      }
    };
    const s = document.createElement("script");
    s.src = "https://telegram.org/js/telegram-widget.js?22";
    s.async = true;
    s.setAttribute("data-telegram-login", cfg.bot_username);
    s.setAttribute("data-size", "large");
    s.setAttribute("data-onauth", "onTelegramAuth(user)");
    s.setAttribute("data-request-access", "write");
    widgetRef.current.innerHTML = "";
    widgetRef.current.appendChild(s);
  }, [cfg, onDone]);

  const dev = async () => {
    const { token } = await api("/auth/dev", { method: "POST" });
    setToken(token);
    onDone();
  };

  return (
    <div className="center stack narrow">
      <img src="/icon.svg" width={64} height={64} alt="" />
      <h1>JobPilot</h1>
      <p className="muted">Профиль из резюме и голосовых, подбор вакансий с оценкой и CV под каждую вакансию.</p>
      <div ref={widgetRef} />
      {cfg && !cfg.bot_username && !cfg.dev_login && <p className="muted">Вход через Telegram ещё не настроен.</p>}
      {cfg?.dev_login && <button className="btn secondary" onClick={dev}>Войти (локальная разработка)</button>}
      {err && <p className="error">{err}</p>}
    </div>
  );
}

function Consent({ onDone }: { onDone: () => void }) {
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
      <button className="btn" onClick={accept}>Согласен</button>
    </div>
  );
}
