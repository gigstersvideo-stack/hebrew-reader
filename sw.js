const CACHE_NAME = "ivrit-reader-v2";
const SHELL_URL = "./reader-prototype.html";
const ASSETS = [SHELL_URL, "./manifest.json", "./icon-192.png", "./icon-512.png"];

// Вытеснение кэша по книгам (см. ROADMAP.md, "вес читалки на телефоне") —
// раньше cache.put() копил КАЖДЫЙ GET навечно (json+mp3+картинки), кэш рос с
// каждой прочитанной книгой без предела. Теперь держим только текущую + одну
// предыдущую книгу; оболочка (html/js/manifest/иконки) в этот учёт не попадает
// (bookIdFromUrl вернёт null) и остаётся в кэше всегда, как раньше.
const MAX_BOOKS_CACHED = 2;
const META_KEY = new URL("__sw-meta__", self.location).toString();
const BOOK_URL_RE = /\/books\/([^/]+)\//;

function bookIdFromUrl(url) {
  const m = url.match(BOOK_URL_RE);
  return m ? m[1] : null;
}

async function getRecentBooks(cache) {
  const res = await cache.match(META_KEY);
  return res ? res.json() : [];
}

async function touchBookAndEvict(cache, bookId) {
  let recent = (await getRecentBooks(cache)).filter((b) => b !== bookId);
  recent.unshift(bookId);
  const evict = recent.slice(MAX_BOOKS_CACHED);
  recent = recent.slice(0, MAX_BOOKS_CACHED);
  await cache.put(META_KEY, new Response(JSON.stringify(recent)));
  if (!evict.length) return;
  for (const req of await cache.keys()) {
    if (evict.includes(bookIdFromUrl(req.url))) await cache.delete(req);
  }
}

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Network-first so a real connection always gets the latest build;
// falls back to the cached shell when offline. Locally-imported book
// data lives only in localStorage/IndexedDB, never fetched over the
// network, so it needs no special-casing here.
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then(async (cache) => {
          await cache.put(event.request, copy);
          const bookId = bookIdFromUrl(event.request.url);
          if (bookId) await touchBookAndEvict(cache, bookId);
        });
        return response;
      })
      .catch(() =>
        caches.match(event.request).then((cached) => cached || caches.match(SHELL_URL))
      )
  );
});
