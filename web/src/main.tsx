import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";

const w = window.Telegram?.WebApp;
if (w?.initData) {
  w.ready();
  w.expand();
  document.documentElement.classList.add("in-telegram");
}

// Новая версия после выкатки: service worker с autoUpdate активируется сам, страницу перезагружаем один раз,
// иначе старая сборка из кэша остаётся на экране до следующего открытия
if ("serviceWorker" in navigator) {
  const hadController = !!navigator.serviceWorker.controller;
  let reloaded = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (hadController && !reloaded) { reloaded = true; window.location.reload(); }
  });
  // проверка обновления при каждом открытии и возвращении в окно
  const check = () => navigator.serviceWorker.getRegistration().then((r) => r?.update()).catch(() => {});
  document.addEventListener("visibilitychange", () => { if (!document.hidden) check(); });
  check();
}

createRoot(document.getElementById("root")!).render(
  <BrowserRouter>
    <App />
  </BrowserRouter>,
);
