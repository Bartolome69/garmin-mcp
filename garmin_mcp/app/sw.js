// Daash app service worker: the shell opens from the phone at once, and
// updates itself in the background, so a deploy shows from the next launch.
// The plan itself is never cached here (the app keeps its own last copy).
const SHELL = "daash-shell-v2";
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
      || url.pathname.startsWith("/app/api/") || url.pathname.startsWith("/app/callback")) return;
  // The copy on the phone straight away; Daash's latest saved for next time.
  const update = fetch(event.request).then((res) => {
    if (res.ok) {
      const copy = res.clone();
      caches.open(SHELL).then((c) => c.put(event.request, copy));
    }
    return res;
  });
  event.respondWith(caches.match(event.request, { ignoreSearch: url.pathname === "/app/" })
    .then((hit) => {
      if (hit) { event.waitUntil(update.catch(() => {})); return hit; }
      return update.catch(() => caches.match("/app/"));
    }));
});
