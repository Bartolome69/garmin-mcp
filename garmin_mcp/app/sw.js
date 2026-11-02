// Daash app service worker: the shell opens offline; the plan is always fetched fresh.
const SHELL = "daash-shell-v1";
const FILES = ["/app/", "/app/app.css", "/app/app.js", "/app/manifest.webmanifest",
  "/app/fonts/Geist.woff2", "/app/fonts/GeistMono.woff2", "/app/icons/favicon.svg", "/app/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin || !url.pathname.startsWith("/app")
      || url.pathname.startsWith("/app/api/")) return;
  // Network first, so a deploy shows at once; the cached shell when offline.
  event.respondWith(fetch(event.request).then((res) => {
    if (res.ok) {
      const copy = res.clone();
      caches.open(SHELL).then((c) => c.put(event.request, copy));
    }
    return res;
  }).catch(() => caches.match(event.request).then((hit) => hit || caches.match("/app/"))));
});
