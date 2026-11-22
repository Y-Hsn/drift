// Drift phone: the app opens with no internet, and every song on the shelf plays from the phone.
// Songs live in the 'drift-songs' cache under ./s/<id>; covers under ./c/<id>. Audio asks for byte ranges, so
// ranges are cut from the cached file here (Safari needs 206 answers for <audio>).
const SHELL = 'drift-shell-v8';
const SONGS = 'drift-songs';
const FILES = ['./', 'index.html', 'app.js', 'store.js', 'sync.js', 'player.js', 'scape.js', 'style.css',
               'manifest.json', 'icon-192.png', 'icon-512.png'];
FILES.push('native.js');                            // Drift for Android hooks (off in a browser)

self.addEventListener('install', e => {
  // straight from the server, never the browser's 10-minute copy (or a new version would keep old files)
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES.map(f => new Request(f, { cache: 'reload' }))))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k.startsWith('drift-shell') && k !== SHELL).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

function cut(request, full) {
  const range = request.headers.get('range');
  if (!range) return full;
  return full.arrayBuffer().then(buf => {
    const size = buf.byteLength, m = /bytes=(\d*)-(\d*)/.exec(range);
    let start = m && m[1] !== '' ? +m[1] : size - +(m && m[2] || 0);
    let end = m && m[1] !== '' && m[2] !== '' ? Math.min(+m[2], size - 1) : size - 1;
    if (!m || start < 0 || start >= size || end < start) {
      return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${size}` } });
    }
    return new Response(buf.slice(start, end + 1), { status: 206, headers: {
      'Content-Type': full.headers.get('Content-Type') || 'audio/mp4', 'Accept-Ranges': 'bytes',
      'Content-Range': `bytes ${start}-${end}/${size}`, 'Content-Length': String(end - start + 1) } });
  });
}

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  const scope = new URL(self.registration.scope).pathname;
  const rel = url.pathname.startsWith(scope) ? url.pathname.slice(scope.length) : url.pathname;
  if (rel.startsWith('s/') || rel.startsWith('c/')) {
    e.respondWith(caches.open(SONGS).then(c => c.match(scope + rel)).then(hit =>
      hit ? cut(e.request, hit) : new Response(null, { status: 404 })));
    return;
  }
  // the app itself: cached copy first (instant, offline), refreshed in the background
  e.respondWith(caches.open(SHELL).then(async c => {
    const key = rel === '' ? './' : rel;
    const hit = await c.match(key, { ignoreSearch: true });
    const fresh = fetch(e.request, { cache: 'no-cache' }).then(r => { if (r.ok && FILES.includes(key)) c.put(key, r.clone()); return r; })
      .catch(() => hit || new Response('Offline', { status: 503 }));
    return hit || fresh;
  }));
});
