const CACHE_NAME = 'koba-app-v1';
const ASSETS_TO_CACHE = [
    '/',
    '/static/manifest.json',
    '/static/img/icons/icon-192x192.png',
    '/static/img/icons/icon-512x512.png',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                return cache.addAll(ASSETS_TO_CACHE);
            })
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cache) => {
                    if (cache !== CACHE_NAME) {
                        return caches.delete(cache);
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    // Para las peticiones de navegación o de API, usamos estrategia Network First
    event.respondWith(
        fetch(event.request)
            .then((response) => {
                // Hacemos una copia para guardar en caché
                const responseClone = response.clone();
                caches.open(CACHE_NAME).then((cache) => {
                    // Evitamos guardar en caché POST/PUT o extensiones raras si es necesario
                    if(event.request.method === 'GET' && !event.request.url.includes('/api/')){
                        cache.put(event.request, responseClone);
                    }
                });
                return response;
            })
            .catch(() => {
                // Fallback a caché si no hay red
                return caches.match(event.request);
            })
    );
});
