const CACHE = "visitor-registry-shell-v4";

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

// Reģistrācijas lietotne nedrīkst rādīt novecojušu personas datu formu.
// Tādēļ navigāciju un statiskos failus vienmēr iegūstam no servera.
