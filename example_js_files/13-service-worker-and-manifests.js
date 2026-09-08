// Service worker + build manifests. Cheap route enumeration, often forgotten.
const PRECACHE = [
  "/", "/queue", "/admin/users", "/admin/feature-flags", "/internal/debug-console",
  "/static/js/main.4f2a91c.js", "/static/css/main.8b1.css", "/manifest.webmanifest",
];

self.__WB_MANIFEST = [
  { url: "/static/js/vendor.2c9.js", revision: null },
  { url: "/offline.html", revision: "a1b2" },
];

workbox.routing.registerRoute(new RegExp("/api/documents/.*"), new workbox.strategies.NetworkFirst());
workbox.routing.registerRoute(({ url }) => url.origin === "https://cdn.acme.io", new workbox.strategies.CacheFirst());

self.addEventListener("sync", () => fetch("/api/sw-sync", { method: "POST" }));

self.addEventListener("push", (e) =>
  fetch("https://idvs-api.acme.corp/api/Notification/ack", { method: "POST", body: e.data.text() })
);

const OFFLINE_FALLBACK = "/offline.html";
const REMOTE_CONFIG = "https://config.acme.io/idvs/prod/config.json";
