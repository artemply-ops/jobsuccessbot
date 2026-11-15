// Web Push в браузере: уведомление о готовых документах, даже когда сайт закрыт (backend/app/services/push.py).
// В Telegram Mini App не нужен: там пишет бот. На iPhone работает только для сайта, добавленного на экран «Домой».
import { api, inTelegram } from "./api";

export function pushSupported(): boolean {
  return !inTelegram() && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

const b64 = (s: string) => {
  const raw = atob((s + "=".repeat((4 - (s.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
};

async function current(): Promise<PushSubscription | null> {
  const reg = await navigator.serviceWorker.getRegistration();
  return reg ? reg.pushManager.getSubscription() : null;
}

export async function pushEnabled(): Promise<boolean> {
  if (!pushSupported() || Notification.permission !== "granted") return false;
  return !!(await current().catch(() => null));
}

// Включить: спросить разрешение (только по клику пользователя), подписаться, отдать подписку серверу
export async function enablePush(): Promise<"on" | "denied" | "off" | "unsupported"> {
  if (!pushSupported()) return "unsupported";
  const { key } = await api<{ key: string | null }>("/push/key");
  if (!key) return "off";
  const perm = await Notification.requestPermission();
  if (perm !== "granted") return "denied";
  const reg = await navigator.serviceWorker.ready;
  const sub = (await reg.pushManager.getSubscription())
    || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(key) });
  await api("/push/subscribe", { method: "POST", json: sub.toJSON() });
  return "on";
}

export async function disablePush(): Promise<void> {
  const sub = await current().catch(() => null);
  if (!sub) return;
  await api("/push/unsubscribe", { method: "POST", json: { endpoint: sub.endpoint } }).catch(() => {});
  await sub.unsubscribe().catch(() => {});
}

// Перед долгим этапом: если пользователь ещё не решал, спросить один раз; если уже разрешил, обновить подписку
export function offerPush(): void {
  if (!pushSupported()) return;
  if (Notification.permission === "default" || Notification.permission === "granted") enablePush().catch(() => {});
}
