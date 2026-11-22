// The phone's own data: IndexedDB for the shelf index, playlists, settings and the outbox of events for the PC;
// Cache Storage ('drift-songs') for song files and covers. Everything here works with no internet.
// [Drift for Android] inside the app, song files and covers live with the app instead (native.js files).
import { files as kept } from './native.js';
const DB = 'drift', VERSION = 1;
let dbp = null;

function open() {
  if (dbp) return dbp;
  dbp = new Promise((ok, fail) => {
    const req = indexedDB.open(DB, VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      db.createObjectStore('kv');
      db.createObjectStore('songs', { keyPath: 'id' });
      db.createObjectStore('playlists', { keyPath: 'id' });
      db.createObjectStore('outbox', { keyPath: 'seq' });
    };
    req.onsuccess = () => ok(req.result);
    req.onerror = () => fail(req.error);
  });
  return dbp;
}

async function tx(store, mode, fn) {
  const db = await open();
  return new Promise((ok, fail) => {
    const t = db.transaction(store, mode), s = t.objectStore(store);
    let out;
    Promise.resolve(fn(s)).then(v => { out = v; });
    t.oncomplete = () => ok(out);
    t.onerror = t.onabort = () => fail(t.error);
  });
}

const req = r => new Promise((ok, fail) => { r.onsuccess = () => ok(r.result); r.onerror = () => fail(r.error); });

export const kv = {
  get: (k, fallback = null) => tx('kv', 'readonly', s => req(s.get(k))).then(v => v === undefined ? fallback : v),
  set: (k, v) => tx('kv', 'readwrite', s => { s.put(v, k); }),
  del: k => tx('kv', 'readwrite', s => { s.delete(k); }),
};

export const songs = {
  all: () => tx('songs', 'readonly', s => req(s.getAll())),
  get: id => tx('songs', 'readonly', s => req(s.get(id))),
  put: song => tx('songs', 'readwrite', s => { s.put(song); }),
  del: id => tx('songs', 'readwrite', s => { s.delete(id); }),
};

export const playlists = {
  all: () => tx('playlists', 'readonly', s => req(s.getAll())),
  put: pl => tx('playlists', 'readwrite', s => { s.put(pl); }),
  replace: list => tx('playlists', 'readwrite', s => { s.clear(); list.forEach(p => s.put(p)); }),
};

// Events for the PC. seq grows per device; the PC answers with the highest seq it has stored.
export const outbox = {
  async add(kind, data) {
    const seq = (await kv.get('seq', 0)) + 1;
    await kv.set('seq', seq);
    const ev = { seq, at: Date.now(), kind, ...data };
    await tx('outbox', 'readwrite', s => { s.put(ev); });
    return ev;
  },
  all: () => tx('outbox', 'readonly', s => req(s.getAll())),
  ack: upto => tx('outbox', 'readwrite', s => { s.delete(IDBKeyRange.upperBound(upto)); }),
};

// Song files. A song shows as ready only after its bytes are stored and checked.
const base = new URL('./', location.href).pathname;
export const files = {
  songUrl: id => `${base}s/${encodeURIComponent(id)}`,
  coverUrl: id => `${base}c/${encodeURIComponent(id)}`,
  async putSong(id, bytes, type) {
    if (kept) return kept.put(id, bytes, 'song');
    const c = await caches.open('drift-songs');
    await c.put(files.songUrl(id), new Response(bytes, { headers: { 'Content-Type': type || 'audio/mp4' } }));
  },
  async putCover(id, bytes) {
    if (kept) return kept.put(id, bytes, 'cover');
    const c = await caches.open('drift-songs');
    await c.put(files.coverUrl(id), new Response(bytes, { headers: { 'Content-Type': 'image/jpeg' } }));
  },
  async drop(id) {
    if (kept) return kept.drop(id);
    const c = await caches.open('drift-songs');
    await Promise.all([c.delete(files.songUrl(id)), c.delete(files.coverUrl(id))]);
  },
  async has(id) {
    if (kept) return kept.has(id);
    const c = await caches.open('drift-songs');
    return !!(await c.match(files.songUrl(id)));
  },
};

export async function space() {
  try {
    const e = await navigator.storage.estimate();
    return { used: (e.usage || 0) + (kept ? kept.used() : 0), quota: e.quota || 0 };
  } catch (e) { return { used: 0, quota: 0 }; }
}

export async function keep() {
  try { return navigator.storage && navigator.storage.persist ? await navigator.storage.persist() : false; }
  catch (e) { return false; }
}
