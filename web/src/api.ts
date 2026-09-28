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
  constructor(public status: number, message: string) {
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
    try {
      const d = await r.json();
      msg = typeof d.detail === "string" ? d.detail : msg;
    } catch {
      /* не JSON */
    }
    throw new ApiError(r.status, msg);
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

export async function login(): Promise<boolean> {
  if (getToken()) return true;
  const w = tg();
  if (w?.initData) {
    const { token } = await api<{ token: string }>("/auth/telegram-webapp", { method: "POST", json: { init_data: w.initData } });
    setToken(token);
    return true;
  }
  return false;
}
