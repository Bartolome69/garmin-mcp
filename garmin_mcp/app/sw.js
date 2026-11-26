// Daash app service worker: the shell comes from Daash first, so a deploy shows
// on the next open, and from the copy on the phone when the network is slow or
// gone. The plan itself is never cached here (the app keeps its own last copy).
const SHELL = "daash-shell-v10";
// How long the network gets before the saved copy opens instead.
const WAIT_MS = 2500;
// The script and styles are kept by their build's address as they're first
// used (below), not here: the page asks for them by build.
const FILES = ["/app/", "/app/manifest.webmanifest",
  "/app/fonts/Geist.woff2", "/app/fonts/GeistMono.woff2", "/app/icons/favicon.svg", "/app/icons/icon-512.png",
  "/app/icons/maskable-192.png"];

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
  // Nov 15: the script and styles at a build's address never change, so the
  // phone's copy answers without asking Daash. A deploy is a new address (the
  // page names it), fetched once and kept; older builds' copies go.
  if (/^\/app\/app\.(js|css)$/.test(url.pathname) && url.searchParams.get("v")) {
    event.respondWith(caches.open(SHELL).then(async (cache) => {
      const hit = await cache.match(event.request);
      if (hit) return hit;
      const res = await fetch(event.request);
      // Kept only when Daash says it is this build's file for good: an older
      // page asking for its own build is answered with today's file.
      if (res.ok && (res.headers.get("cache-control") || "").includes("immutable")) {
        const copy = res.clone();
        event.waitUntil((async () => {
          for (const key of await cache.keys()) {
            const k = new URL(key.url);
            if (k.pathname === url.pathname && k.search !== url.search) await cache.delete(key);
          }
          await cache.put(event.request, copy);
        })());
      }
      return res;
    }));
    return;
  }
  // Nov 13: the copy on the phone came first and Daash's latest was saved for
  // next time, so a deploy (light mode) only showed on the second open.
  const saved = caches.match(event.request, { ignoreSearch: url.pathname === "/app/" });
  const fresh = fetch(event.request).then((res) => {
    if (!res.ok) return saved.then((hit) => hit || res);
    const copy = res.clone();
    event.waitUntil(caches.open(SHELL).then((c) => c.put(event.request, copy)));
    return res;
  });
  event.waitUntil(fresh.catch(() => {}));
  const slow = new Promise((r) => setTimeout(r, WAIT_MS)).then(() => saved).then((hit) => hit || fresh);
  event.respondWith(Promise.race([fresh, slow])
    .catch(() => saved.then((hit) => hit || caches.match("/app/")).then((hit) => hit || Response.error())));
});

// Notifications (Nov 10): Daash's morning heads-up and a run's verdict. What
// arrives is already worded; this only shows it, and a tap opens the screen it
// names, in the app if it's open, else a fresh window.
self.addEventListener("push", (event) => {
  let said = {};
  try { said = event.data ? event.data.json() : {}; } catch { said = { body: event.data ? event.data.text() : "" }; }
  const url = typeof said.url === "string" && said.url.startsWith("/app/") ? said.url : "/app/#/today";
  event.waitUntil(self.registration.showNotification(said.title || "Daash", {
    body: said.body || "", tag: said.tag || undefined, data: { url },
    icon: "/app/icons/icon-512.png", badge: "/app/icons/icon-512.png",
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/app/#/today";
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((open) => {
    const app = open.find((c) => new URL(c.url).pathname.startsWith("/app"));
    if (app) return app.navigate(url).then((c) => (c || app).focus()).catch(() => app.focus());
    return self.clients.openWindow(url);
  }));
});
