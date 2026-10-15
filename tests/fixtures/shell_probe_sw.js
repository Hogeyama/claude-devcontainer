// The shell probe's service worker: it answers /sw-probe with what its own fetches got.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
const attempt = async (url) => {
  try {
    return String((await fetch(url)).status);
  } catch (error) {
    return 'error:' + error.name;
  }
};
self.addEventListener('fetch', (event) => {
  if (new URL(event.request.url).pathname === '/sw-probe') {
    event.respondWith((async () => new Response(JSON.stringify({
      external: await attempt('http://example.com/service-worker'),
      app: await attempt('http://localhost:3000/service-worker'),
    })))());
  }
});
