// Pairing with the PC and syncing the shelf. The PC is a sync partner: when both are online, it sends new songs and
// playlists and takes the phone's events. Every request is signed with this phone's key (docs/phone-sync.md).
import { kv, songs, playlists, outbox, files } from './store.js';

const enc = new TextEncoder();
const b64 = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const unb64 = s => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((s.length + 3) % 4)), c => c.charCodeAt(0));
const hex = buf => [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
const sha256 = async data => hex(await crypto.subtle.digest('SHA-256', typeof data === 'string' ? enc.encode(data) : data));

export const state = { pc: null, online: false, busy: false, note: '', progress: null, listeners: new Set() };
const tell = () => state.listeners.forEach(fn => { try { fn(state); } catch (e) {} });
const say = note => { state.note = note; tell(); };

// ---- pairing ----

export function invite() {
  const h = new URLSearchParams(location.hash.slice(1));
  if (!h.get('i')) return null;
  history.replaceState(null, '', location.pathname);       // the invite leaves the address bar at once
  return { invite: h.get('i'), url: new TextDecoder().decode(unb64(h.get('u') || '')), topic: h.get('t') || '' };
}

const standalone = () => navigator.standalone === true || matchMedia('(display-mode: standalone)').matches;
const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent);
const deviceName = () => isIOS() ? 'iPhone' : /Android/.test(navigator.userAgent) ? 'Android phone' : 'Phone';

function cookie(name, value, maxAge) {
  document.cookie = `${name}=${value ? encodeURIComponent(value) : ''}; Max-Age=${value ? maxAge : 0}; Path=${new URL('./', location.href).pathname}; Secure; SameSite=Strict`;
}
function readCookie(name) {
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return m ? decodeURIComponent(m[1]) : '';
}

async function post(url, path, body) {
  const r = await fetch(url.replace(/\/$/, '') + path, { method: 'POST', body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json' } });
  if (!r.ok) throw new Error((await r.text().catch(() => '')) || `PC answered ${r.status}`);
  return r.json();
}

// Step 1 (Safari or the browser): trade the QR's invite for a one-use install ticket.
// On an iPhone the ticket rides into the home-screen app in a cookie (iOS copies cookies, not app data, there).
export async function redeem(inv) {
  const r = await post(inv.url, '/pair', { invite: inv.invite, name: deviceName() });
  const ticket = [r.ticket, b64(enc.encode(inv.url)), inv.topic, r.pc || '', b64(enc.encode(r.pcName || 'your PC'))].join('|');
  if (isIOS() && !standalone()) {
    cookie('drift_ticket', ticket, 86400);
    return { needsHomeScreen: true, pcName: pcLabel(r.pcName) };
  }
  await finish(ticket);
  return { needsHomeScreen: false, pcName: pcLabel(r.pcName) };
}

// Step 2 (inside the installed app): make this phone's own key and register it with the PC.
export async function finish(ticket = readCookie('drift_ticket')) {
  if (!ticket) return false;
  const [t, u, topic, pc, name] = ticket.split('|');
  const url = new TextDecoder().decode(unb64(u));
  const key = crypto.getRandomValues(new Uint8Array(32));
  const r = await post(url, '/pair/finish', { ticket: t, key: b64(key), name: deviceName() });
  await kv.set('pair', { device: r.device, key: b64(key), link: r.link, topic: topic || r.topic, pc: pc || r.pc,
                         pcName: r.pcName || new TextDecoder().decode(unb64(name || '')) || 'your PC', url, gen: 0 });
  cookie('drift_ticket', '', 0);
  return true;
}

// Each phone pairs with its owner's own PC. Windows' made-up names come as "My PC" / "My laptop" from 1.1.0 PCs:
// on the phone that reads as someone else's computer, so it says "your PC" / "your laptop".
export const pcLabel = n => ({ 'My PC': 'your PC', 'My laptop': 'your laptop' })[n] || n || 'your PC';
export const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
export const paired = () => kv.get('pair').then(p => p && { ...p, pcName: pcLabel(p.pcName) });
export const pendingTicket = () => !!readCookie('drift_ticket');

export async function forget() {
  await kv.del('pair');
}

// ---- signed requests ----

let hmacKey = null;
async function signer(pair) {
  if (!hmacKey || hmacKey.raw !== pair.key) {
    hmacKey = { raw: pair.key, key: await crypto.subtle.importKey('raw', unb64(pair.key), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']) };
  }
  return hmacKey.key;
}

async function call(pair, url, method, path, body = null, timeout = 15000, extra = {}) {
  const text = body ? JSON.stringify(body) : '';
  const time = String(Date.now());
  const sig = hex(await crypto.subtle.sign('HMAC', await signer(pair), enc.encode([method, path, time, await sha256(text)].join('|'))));
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeout);
  try {
    const r = await fetch(url.replace(/\/$/, '') + path, { method, body: body ? text : undefined, signal: ctl.signal,
      headers: { 'X-Drift-Device': pair.device, 'X-Drift-Time': time, 'X-Drift-Sign': sig, ...extra,
                 ...(body ? { 'Content-Type': 'application/json' } : {}) } });
    if (r.status === 401 || r.status === 403) throw Object.assign(new Error('This phone is no longer paired'), { unpaired: true });
    if (!r.ok) throw Object.assign(new Error(`PC answered ${r.status}`), { status: r.status });
    return r;
  } finally { clearTimeout(timer); }
}

// ---- finding the PC ----
// The PC's link address changes when it restarts. It posts the new one, encrypted with the pairing's link key, to a
// private ntfy.sh topic; the phone keeps the newest one it can open and remembers the last one that worked.

async function announced(pair) {
  if (!pair.topic || !pair.link) return [];
  try {
    const r = await fetch(`https://ntfy.sh/${encodeURIComponent(pair.topic)}/json?poll=1&since=12h`, { cache: 'no-store' });
    if (!r.ok) return [];
    const key = await crypto.subtle.importKey('raw', unb64(pair.link), 'AES-GCM', false, ['decrypt']);
    const out = [];
    for (const line of (await r.text()).split('\n')) {
      try {
        const msg = JSON.parse(line).message, [iv, data] = msg.split('.');
        const plain = JSON.parse(new TextDecoder().decode(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: unb64(iv) }, key, unb64(data))));
        if (plain.exp > Date.now() && plain.gen > 0) out.push(plain);
      } catch (e) { /* not ours or broken: skip */ }
    }
    return out.sort((a, b) => b.gen - a.gen);
  } catch (e) { return []; }
}

async function reach(pair) {
  const tries = [pair.url];
  for (const a of await announced(pair)) if (a.gen >= (pair.gen || 0) && !tries.includes(a.url)) tries.push(a.url);
  for (const url of tries) {
    try {
      const hello = await (await call(pair, url, 'GET', '/hello', null, 6000)).json();
      if (url !== pair.url) {
        const gen = (await announced(pair)).find(a => a.url === url)?.gen || pair.gen;
        Object.assign(pair, { url, gen });
        await kv.set('pair', pair);
      }
      return { url, hello };
    } catch (e) {
      if (e.unpaired) throw e;
    }
  }
  return null;
}

// ---- the sync itself ----

const CHUNK = 4 * 1024 * 1024, AT_ONCE = 3;
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function fetchSong(pair, url, s) {
  const path = `/song/${encodeURIComponent(s.id)}`, total = s.bytes || 0;
  const piece = async (range, timeout) => {
    for (let n = 0; ; n++) {
      try {
        return new Uint8Array(await (await call(pair, url, 'GET', path, null, timeout, range ? { Range: range } : {})).arrayBuffer());
      } catch (e) {
        if (e.unpaired || e.status === 404 || n >= 3) throw e;
        await sleep(1500 * (n + 1));
      }
    }
  };
  if (!total || total <= CHUNK) return (await piece('', 120000)).buffer;
  const out = new Uint8Array(total);
  for (let at = 0; at < total; at += CHUNK) {
    const end = Math.min(total, at + CHUNK) - 1;
    const b = await piece(`bytes=${at}-${end}`, 60000);
    if (b.length !== end - at + 1) throw new Error('song arrived short');
    out.set(b, at);
  }
  return out.buffer;
}

async function getCover(pair, url, s) {
  try {
    await files.putCover(s.id, await (await call(pair, url, 'GET', `/cover/${encodeURIComponent(s.id)}`)).arrayBuffer());
    return true;
  } catch (e) { return false; }
}

export async function syncNow({ budget } = {}) {
  if (state.busy) return;
  const pair = await paired();
  if (!pair) return;
  if (!navigator.onLine) { state.online = false; say('Offline: playing what is on this phone'); return; }
  state.busy = true; say(`Looking for ${pair.pcName}…`);
  try {
    const found = await reach(pair);
    if (!found) { state.online = false; say(`${cap(pair.pcName)} is off or offline. Your songs here keep playing.`); return; }
    state.online = true; state.pc = found.hello;
    const url = found.url;

    // 1. what happened on the phone goes to the PC first
    const events = await outbox.all();
    if (events.length) {
      const r = await (await call(pair, url, 'POST', '/events', { device: pair.device, events })).json();
      if (r.ack) await outbox.ack(r.ack);
    }

    // 2. the shelf the PC picked for this phone
    const size = budget || (await kv.get('budget', 2 * 1024 ** 3));
    const have = await kv.get('rev', '');
    // the PC may need a moment to pick the shelf (first time: it reads every song once)
    const shelf = await (await call(pair, url, 'GET', `/shelf?size=${size}&have=${encodeURIComponent(have)}`, null, 95000)).json();
    if (shelf.filling) syncSoon([45000]);             // the PC is fetching more radio picks: collect them soon
    if (shelf.same) { say(`In sync with ${pair.pcName}`); return; }
    await playlists.replace(shelf.playlists || []);
    if (shelf.taste) await kv.set('taste', shelf.taste);

    const mine = new Map((await songs.all()).map(s => [s.id, s]));
    const wanted = new Set(shelf.songs.map(s => s.id));
    const playing = await kv.get('playing', '');
    for (const [id, s] of mine) {                      // songs the PC no longer picks for this phone make room
      if (!wanted.has(id) && id !== playing && !s.pinned) { await files.drop(id); await songs.del(id); }
    }
    const todo = shelf.songs.filter(s => { const m = mine.get(s.id); return !(m && m.ready && m.sha256 === s.sha256); });
    const covers = [];
    for (const s of shelf.songs) {                     // keep what is already here, with fresh details
      const m = mine.get(s.id);
      if (m && m.ready && m.sha256 === s.sha256) {
        await songs.put({ ...m, ...s, ready: true, pinned: m.pinned, cover: m.cover && s.cover });
        if (s.cover && !m.cover) covers.push(s);
      }
    }
    // songs in the PC's order (playlists and likes first), a few at a time; each in 4 MB pieces, a failed piece is
    // asked for again, a song only shows once all of it arrived and its fingerprint matches
    let done = 0, full = false, next = 0;
    const one = async s => {
      const bytes = await fetchSong(pair, url, s);
      if (s.sha256 && await sha256(bytes) !== s.sha256) throw new Error('song arrived damaged');
      await files.putSong(s.id, bytes, s.type);
      const cover = s.cover && await getCover(pair, url, s);
      await songs.put({ ...s, cover: !!cover, ready: true, added: Date.now() });
    };
    const worker = async () => {
      while (!full && next < todo.length) {
        const s = todo[next++];
        try { await one(s); done++; } catch (e) {
          if (e.unpaired) throw e;
          if (e.name === 'QuotaExceededError') { full = true; say('This phone is full. Pick a smaller shelf in Settings.'); }
        }
        state.progress = { done, total: todo.length };
        if (!full) say(`Getting songs from ${pair.pcName}: ${done} of ${todo.length}`);
        tell();
      }
    };
    if (todo.length) say(`Getting songs from ${pair.pcName}: 0 of ${todo.length}`);
    await Promise.all(Array.from({ length: Math.min(AT_ONCE, todo.length) }, worker));
    for (const s of covers) {
      if (await getCover(pair, url, s)) { const m = await songs.get(s.id); if (m) await songs.put({ ...m, cover: true }); }
    }
    if (done === todo.length) await kv.set('rev', shelf.rev);
    state.progress = null;
    say(todo.length ? `${done} new songs from ${pair.pcName}` : `In sync with ${pair.pcName}`);
  } catch (e) {
    if (e.status === 503) { say(`${cap((await paired())?.pcName || 'your PC')} is getting your songs ready…`); syncSoon([15000]); return; }
    state.online = false;
    say(e.unpaired ? 'This phone was unpaired on the PC. Pair it again from Drift on your PC.' : `Sync stopped: ${e.message}`);
    if (e.unpaired) await forget();
  } finally {
    state.busy = false; state.progress = null; tell();
  }
}

// A song the phone wants that is not on the shelf yet. It travels as an event; the PC finds and fetches it and it
// arrives with a following sync (within seconds when the PC is on: the phone checks again a few times).
export async function wish(query) {
  const ev = await outbox.add('wish', { query });
  const wishes = await kv.get('wishes', []);
  wishes.unshift({ query, at: ev.at, seq: ev.seq });
  await kv.set('wishes', wishes.slice(0, 50));
  syncSoon([1500, 12000, 30000, 60000]);
  return ev;
}

let soon = [];
export function syncSoon(delays = [1500]) {
  soon.forEach(clearTimeout);
  soon = delays.map(d => setTimeout(() => syncNow(), d));
}

export const events = {
  // meta: a streamed song's details (title, artist, page...) so the PC can know it
  play: (song, secs, meta) => outbox.add('play', { song, secs: Math.round(secs), ...(meta && { meta }) }),
  skip: (song, at, meta) => outbox.add('skip', { song, at: Math.round(at), ...(meta && { meta }) }),
  like: (song, on, meta) => outbox.add('like', { song, on, ...(meta && { meta }) }),
};
