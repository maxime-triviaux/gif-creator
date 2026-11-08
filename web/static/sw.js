// Keeps the page's own files so the app opens even on a weak connection.
// Only the files under /app/ are handled: API requests (search, GIF) always go to the network.
// Network first: a new version is picked up at the next launch, without changing this file.
"use strict";

const CACHE = "kaamelott-gif-web-v1";
const SHELL = ["./", "index.html", "app.js", "style.css", "manifest.webmanifest",
  "icon-192.png", "icon-512.png", "apple-touch-icon.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name !== CACHE) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin
      || !url.pathname.startsWith("/app/")) {
    return;
  }
  event.respondWith((async () => {
    try {
      const response = await fetch(event.request);
      if (response.ok) {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, copy));
      }
      return response;
    } catch (e) {
      const cached = await caches.match(event.request, { ignoreSearch: true });
      if (cached) return cached;
      throw e;
    }
  })());
});
