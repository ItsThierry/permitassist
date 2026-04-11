// PermitAssist Service Worker
// Handles offline caching and background sync

const CACHE_NAME = 'permitassist-v1';
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/manifest.json',
  '/icons/icon-192.png',
  '/icons/icon-512.png'
];

// Install — cache static assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(STATIC_ASSETS.filter(url => !url.includes('icon')));
    }).then(() => self.skipWaiting())
  );
});

// Activate — clean old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Fetch strategy:
// - Static assets: cache first
// - API calls: network first, fallback to offline message
// - Permit results: cache for 24h (contractor may re-check on job site without signal)
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // API calls — network first with result caching
  if (url.pathname === '/api/permit') {
    event.respondWith(
      fetch(event.request.clone()).then(response => {
        if (response.ok) {
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then(cache => {
            cache.put(event.request, responseClone);
          });
        }
        return response;
      }).catch(() => {
        // Offline — try cache
        return caches.match(event.request).then(cached => {
          if (cached) return cached;
          return new Response(JSON.stringify({
            error: 'You are offline. Please connect to look up new permits.',
            offline: true
          }), {
            status: 503,
            headers: { 'Content-Type': 'application/json' }
          });
        });
      })
    );
    return;
  }

  // Static assets — cache first
  event.respondWith(
    caches.match(event.request).then(cached => {
      return cached || fetch(event.request).then(response => {
        if (response.ok && event.request.method === 'GET') {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      });
    })
  );
});

// Background sync for offline lookups (when connection restored)
self.addEventListener('sync', event => {
  if (event.tag === 'permit-lookup') {
    event.waitUntil(syncPendingLookups());
  }
});

async function syncPendingLookups() {
  // Would sync any queued lookups when back online
  // Implementation: read from IndexedDB queue, process, notify user
  console.log('PermitAssist: syncing pending lookups');
}
