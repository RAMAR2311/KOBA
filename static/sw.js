const CACHE_NAME = 'koba-cache-v2';

const STATIC_PRECACHE = [
    '/static/manifest.json',
    '/static/img/KOBA.png',
    '/static/img/icons/icon-192x192.png',
    '/static/img/icons/icon-512x512.png',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                return cache.addAll(STATIC_PRECACHE).catch(err => {
                    console.warn('Algunos recursos estáticos no pudieron precargarse:', err);
                });
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
    const request = event.request;

    // Solo interceptar peticiones GET (no mutaciones ni POSTs de facturas/ventas)
    if (request.method !== 'GET') {
        return;
    }

    const url = new URL(request.url);
    const isNavigation = request.mode === 'navigate' || (request.headers.get('accept') && request.headers.get('accept').includes('text/html'));
    
    // Identificar activos estáticos: CSS, JS, fuentes, imágenes locales y CDNs
    const isStaticAsset = 
        url.pathname.startsWith('/static/') ||
        url.hostname.includes('cdn.jsdelivr.net') ||
        url.hostname.includes('cdnjs.cloudflare.com') ||
        request.destination === 'style' ||
        request.destination === 'script' ||
        request.destination === 'font' ||
        request.destination === 'image';

    if (isStaticAsset) {
        // ESTRATEGIA CACHE FIRST para estáticos:
        // Carga instantánea (0ms) en datos móviles sin consumir megas ni esperar latencia de red
        event.respondWith(
            caches.match(request).then((cachedResponse) => {
                if (cachedResponse) {
                    return cachedResponse;
                }
                return fetch(request).then((networkResponse) => {
                    if (networkResponse && networkResponse.status === 200) {
                        const copy = networkResponse.clone();
                        caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
                    }
                    return networkResponse;
                }).catch(() => {
                    return cachedResponse;
                });
            })
        );
        return;
    }

    if (isNavigation) {
        // ESTRATEGIA NETWORK FIRST para páginas HTML de la aplicación:
        // Siempre datos frescos, con fallback a caché si se pierde la conexión
        event.respondWith(
            fetch(request)
                .then((networkResponse) => {
                    if (networkResponse && networkResponse.status === 200) {
                        const copy = networkResponse.clone();
                        caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
                    }
                    return networkResponse;
                })
                .catch(() => {
                    return caches.match(request);
                })
        );
        return;
    }

    // Comportamiento por defecto
    event.respondWith(
        fetch(request).catch(() => caches.match(request))
    );
});
