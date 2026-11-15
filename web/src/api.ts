// Клиент API: токен сессии, запросы, ожидание фоновых задач.

const TOKEN_KEY = "jp_token";

declare global {
  interface Window {
    Telegram?: { WebApp: any };
    onTelegramAuth?: (user: Record<string, unknown>) => void;
  }
}

export const tg = () => window.Telegram?.WebApp;
export const inTelegram = () => Boolean(tg()?.initData);

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(t: string | null) {
  try {
    t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* приватный режим: живём без сохранения */
  }
}

export class ApiError extends Error {
  // data: машинные поля ответа, например { need: "package" } при 402 (нужна оплата)
  constructor(public status: number, message: string, public data: any = null) {
    super(message);
  }
}

export async function api<T = any>(path: string, opts: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers as Record<string, string>) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body = opts.body;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  const r = await fetch(`/api${path}`, { ...opts, headers, body });
  if (r.status === 401) {
    setToken(null);
    window.dispatchEvent(new Event("jp-logout"));
  }
  if (!r.ok) {
    let msg = `Ошибка ${r.status}`;
    let data = null;
    try {
      const d = await r.json();
      if (typeof d.detail === "string") msg = d.detail;
      else if (d.detail && typeof d.detail.message === "string") { msg = d.detail.message; data = d.detail; }
    } catch {
      /* не JSON */
    }
    throw new ApiError(r.status, msg, data);
  }
  const ct = r.headers.get("content-type") || "";
  return (ct.includes("application/json") ? r.json() : r.blob()) as Promise<T>;
}

export async function upload<T = any>(path: string, file: Blob, name: string): Promise<T> {
  const fd = new FormData();
  fd.append("file", file, name);
  return api<T>(path, { method: "POST", body: fd });
}

export type Job = { id: number; status: string; note?: string; result: any };

export async function waitJob(jobId: number, onTick?: (j: Job) => void): Promise<Job> {
  for (;;) {
    const j = await api<Job>(`/jobs/${jobId}`);
    onTick?.(j);
    if (j.status === "done") return j;
    if (j.status === "error") throw new ApiError(500, j.note || "Задача завершилась с ошибкой");
    await new Promise((r) => setTimeout(r, 2000));
  }
}

export async function download(path: string, filename: string) {
  const blob = await api<Blob>(path);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export type Cfg = { bot_username: string; dev_login: boolean; ai: boolean; voice: boolean;
  login: { email: boolean; yandex: boolean } };
let cfgPromise: Promise<Cfg> | null = null;
export const getConfig = () => (cfgPromise ??= api<Cfg>("/config"));

// Одноразовая ссылка из бота (/web) или возврат с Яндекс ID: ?login=<токен>. Гость на сайте вливается в кабинет
async function linkLogin(): Promise<void> {
  const url = new URL(window.location.href);
  const t = url.searchParams.get("login");
  if (!t) return;
  url.searchParams.delete("login");
  window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  const { token } = await api<{ token: string }>("/auth/token", { method: "POST", json: { token: t } });
  setToken(token);
}

// Начать на сайте без входа: гостевая учётка, документы собираются после входа
export async function startGuest(): Promise<void> {
  const { token } = await api<{ token: string }>("/auth/guest", { method: "POST" });
  setToken(token);
}

export async function login(): Promise<boolean> {
  await linkLogin();
  if (getToken()) return true;
  const w = tg();
  if (w?.initData) {
    const { token } = await api<{ token: string }>("/auth/telegram-webapp", { method: "POST", json: { init_data: w.initData } });
    setToken(token);
    return true;
  }
  return false;
}
