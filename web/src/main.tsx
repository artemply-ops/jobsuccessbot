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

createRoot(document.getElementById("root")!).render(
  <BrowserRouter>
    <App />
  </BrowserRouter>,
);
