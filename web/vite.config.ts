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
        name: "JobPilot: резюме под вакансию",
        short_name: "JobPilot",
        description: "Ваш реальный опыт на языке работодателя: резюме, одностраничник и письмо под вакансию",
        lang: "ru",
        start_url: "/",
        display: "standalone",
        background_color: "#F7F6F1",
        theme_color: "#14532D",
        icons: [{ src: "icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any maskable" }],
      },
      workbox: {
        navigateFallbackDenylist: [/^\/api/, /^\/health/],
        skipWaiting: true,
        clientsClaim: true,
        cleanupOutdatedCaches: true,
        runtimeCaching: [],
        importScripts: ["push-sw.js"],  // Web Push: public/push-sw.js
      },
    }),
  ],
  server: { proxy: { "/api": "http://localhost:8000" } },
});
