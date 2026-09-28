import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icon.svg"],
      manifest: {
        name: "JobPilot — поиск работы с ИИ",
        short_name: "JobPilot",
        description: "Профиль, подбор вакансий и CV под каждую вакансию",
        lang: "ru",
        start_url: "/",
        display: "standalone",
        background_color: "#ffffff",
        theme_color: "#2F6FEB",
        icons: [{ src: "icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any maskable" }],
      },
      workbox: {
        navigateFallbackDenylist: [/^\/api/, /^\/health/],
        runtimeCaching: [],
      },
    }),
  ],
  server: { proxy: { "/api": "http://localhost:8000" } },
});
