// Web Push: подключается в service worker сборки (vite.config.ts, workbox.importScripts).
// Показывает уведомление, даже когда сайт закрыт. Если вкладка открыта и на экране, не дублируем: всё видно в окне.
self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) { d = { body: event.data && event.data.text() }; }
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    if (wins.some((w) => w.visibilityState === "visible" && w.focused)) return;
    await self.registration.showNotification(d.title || "JobPilot", {
      body: d.body || "", icon: "/icon.svg", badge: "/icon.svg", tag: d.tag || "jobpilot", renotify: true,
      data: { url: d.url || "/make" },
    });
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/make";
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if (new URL(w.url).origin === self.location.origin) { await w.focus(); return w.navigate(url).catch(() => {}); }
    }
    return self.clients.openWindow(url);
  })());
});
