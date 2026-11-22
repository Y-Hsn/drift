// Plays songs from the phone itself: one <audio> element (keeps playing when the phone is locked, tested on iOS 18.7),
// lock-screen controls through Media Session, and Drift radio over the shelf when the queue runs out.
// [Drift for Android] inside the app, native.js stands in for the <audio> element (a native player that keeps
// going with the phone locked) and adds YouTube to the radio. Outside the app every native.* here is off.
import * as native from './native.js';
import { songs, kv, files } from './store.js';
import { events } from './sync.js';

const audio = native.nativeAudio() || new Audio();
audio.preload = 'auto';
audio.setAttribute('playsinline', '');

export const player = {
  queue: [], at: -1, radio: true, song: null, listeners: new Set(),
  get playing() { return !audio.paused && !audio.ended; },
  get time() { return audio.currentTime || 0; },
  get length() { return isFinite(audio.duration) ? audio.duration : (this.song?.secs || 0); },
};
const tell = what => {
  if (audio.native && (what === 'song' || what === 'queue')) audio.follow(player.queue, player.at);   // [Drift for Android]
  player.listeners.forEach(fn => { try { fn(what); } catch (e) {} });
};

// ---- radio over the shelf ----
// Taste from the PC (song and artist scores) plus what happened here: higher scores come sooner, an artist does not
// come back within 4 songs, nothing plays twice in a run, recent plays wait their turn.
const recent = [];
const FRESH = 8 * 3600e3;                 // a shelf song played in the last 8 hours waits
async function radioPick(n = 8, freshOnly = false) {
  const playedAt = (await kv.get('playedAt', {})) || {}, now = Date.now();
  let all = (await songs.all()).filter(s => s.ready);
  if (freshOnly) all = all.filter(s => !recent.includes(s.id) && !(now - (playedAt[s.id] || 0) < FRESH));
  if (!all.length) return [];
  const taste = (await kv.get('taste', {})) || {};
  const local = (await kv.get('localTaste', {})) || {};
  const played = new Set([...recent, ...player.queue.map(s => s.id)]);
  const score = s => {
    const a = (s.artist || '').toLowerCase();
    let v = (s.score ?? taste.songs?.[s.id] ?? 0.5) + 0.3 * (taste.artists?.[a] ?? 0) + (local[a] || 0) + (s.liked ? 0.25 : 0);
    if (played.has(s.id)) v -= 5;
    return v + Math.random() * 0.6;
  };
  const ranked = all.map(s => [score(s), s]).sort((x, y) => y[0] - x[0]).map(x => x[1]);
  const out = [], lastArtists = player.queue.slice(-4).map(s => (s.artist || '').toLowerCase());
  for (const s of ranked) {
    const a = (s.artist || '').toLowerCase();
    if (out.length >= n) break;
    if (lastArtists.slice(-4).includes(a) && ranked.length > 6) continue;
    out.push(s); lastArtists.push(a);
  }
  return out.length ? out : ranked.slice(0, n);
}

// Drift radio over the shelf: songs that have not had their turn lately first, then round again.
let topping = null;
function topUp() { return topping ||= fill().finally(() => { topping = null; }); }
async function fill() {
  if (await native.fill(player, radioPick)) return tell('queue');    // [Drift for Android] YouTube drift
  if (!player.radio || player.queue.length - player.at > 3) return;
  let more = await radioPick(8, true);
  if (more.length < 3) more = [...more, ...(await radioPick(8)).filter(s => !more.some(m => m.id === s.id))];
  player.queue.push(...more.map(s => ({ ...s, why: s.why || 'Drift radio' })));
  tell('queue');
}

// ---- playing ----

let startedAt = 0, heard = 0;
function counted() {
  const s = player.song;
  if (!s) return;
  heard += Math.max(0, audio.currentTime - startedAt);
  const len = player.length;
  const meta = s.remote ? about(s) : undefined;
  if (heard >= Math.min(30, len * 0.5)) events.play(s.id, heard, meta);
  else if (heard < Math.min(30, len * 0.5) && audio.currentTime > 0) {
    events.skip(s.id, audio.currentTime, meta);
    const a = (s.artist || '').toLowerCase();
    kv.get('localTaste', {}).then(t => { t[a] = Math.max(-1, (t[a] || 0) - 0.08); return kv.set('localTaste', t); });
  }
  heard = 0;
}

async function load(i, play = true) {
  if (i < 0 || i >= player.queue.length) return;
  if (player.song) counted();
  player.at = i;
  const s = player.queue[i];
  player.song = s;
  recent.push(s.id); if (recent.length > 40) recent.shift();
  kv.get('playedAt', {}).then(p => { p = p || {}; p[s.id] = Date.now(); return kv.set('playedAt', p); });
  audio.src = s.remote ? s.stream : files.songUrl(s.id);
  startedAt = 0; heard = 0;
  kv.set('playing', s.id);
  saveResume(0, i);
  setSession(s);
  tell('song');
  if (play) {
    try { await audio.play(); } catch (e) { tell('blocked'); }
  }
  topUp();
}

function saveResume(t, from) {
  const q = player.queue.slice(from, player.at + 20);
  kv.set('resume', { id: player.song.id, t, queue: q.map(x => x.id), remote: q.filter(x => x.remote) });
}

function setSession(s) {
  if (!('mediaSession' in navigator)) return;
  const art = s.remote && s.art ? [{ src: s.art, sizes: '480x480', type: 'image/jpeg' }]
            : s.cover ? [{ src: files.coverUrl(s.id), sizes: '512x512', type: 'image/jpeg' }]
                      : [{ src: 'icon-512.png', sizes: '512x512', type: 'image/png' }];
  navigator.mediaSession.metadata = new MediaMetadata({ title: s.title, artist: s.artist || '', album: s.album || '', artwork: art });
}

export async function playList(list, start = 0, radio = true) {
  player.queue = list.slice();
  player.radio = radio;
  await load(start);
}

export async function playRadio(seed) {
  if (seed?.remote && native.on) { player.radio = true; player.queue = [seed]; return load(0); }   // [Drift for Android]
  player.radio = true;
  let picks = await radioPick(10, true);
  if (!picks.length) picks = await radioPick(10);
  player.queue = seed ? [seed, ...picks.filter(s => s.id !== seed.id)] : picks;
  await load(0);
}

// What the PC needs to know a streamed song (it can play and save it from its page).
const about = s => ({ title: s.title, artist: s.artist, secs: s.secs, page: s.page, art: s.art, source: s.source || 'audius' });

export const next = () => player.at + 1 < player.queue.length ? load(player.at + 1) : topUp().then(() => load(player.at + 1));
export const previous = () => audio.currentTime > 4 || player.at === 0 ? (audio.currentTime = 0) : load(player.at - 1);
export function toggle() {
  if (!player.song) return playRadio();
  if (audio.paused) audio.play().catch(() => tell('blocked')); else audio.pause();
}
export function seek(t) { audio.currentTime = Math.max(0, Math.min(t, player.length - 0.5)); startedAt = audio.currentTime; }
export function playNext(s) { player.queue.splice(player.at + 1, 0, s); tell('queue'); }

export async function like(s, on = !s.liked) {
  s.liked = on;
  const stored = await songs.get(s.id);
  if (stored) await songs.put({ ...stored, liked: on });
  events.like(s.id, on, s.remote ? about(s) : undefined);   // a liked streamed song: the PC saves it for the shelf
  tell('song');
}

audio.addEventListener('play', () => { startedAt = audio.currentTime; tell('state'); });
audio.addEventListener('pause', () => { heard += Math.max(0, audio.currentTime - startedAt); startedAt = audio.currentTime; tell('state'); });
audio.addEventListener('ended', () => { heard += Math.max(0, audio.currentTime - startedAt); startedAt = audio.currentTime; next(); });
audio.addEventListener('timeupdate', () => {
  tell('time');
  if ((audio.currentTime | 0) % 5 === 0 && player.song) saveResume(audio.currentTime, player.at);
});
// [Drift for Android] the native player went on by itself (end of a song, lock-screen next, a pick of its own while
// the page slept): take that song over as if it had been picked here. Never fires outside the app.
audio.addEventListener('moved', e => {
  const s = e.detail;
  if (!s || s.id === player.song?.id) return;
  heard += Math.max(0, audio.currentTime - startedAt); startedAt = audio.currentTime;
  let i = player.queue.findIndex((x, k) => k > player.at && x.id === s.id);
  if (i < 0) i = player.queue.findIndex(x => x.id === s.id);
  if (i < 0) { i = player.at + 1; player.queue.splice(i, 0, s); }
  load(i, false);
});
audio.addEventListener('error', () => {
  tell('error');
  // lost the connection on a streamed song: the rest of the online picks wait, the shelf carries on
  const s = player.song;
  if (s?.remote && navigator.onLine && !s.retried) {   // the open catalogue's servers hiccup now and then: once more
    s.retried = true; audio.src = s.stream + '&again=1'; audio.play().catch(() => {}); return;
  }
  if (s?.remote && !navigator.onLine) player.queue = player.queue.filter((s, i) => i <= player.at || !s.remote);
  setTimeout(next, 600);
});

if ('mediaSession' in navigator) {
  const ms = navigator.mediaSession;
  ms.setActionHandler('play', () => audio.play());
  ms.setActionHandler('pause', () => audio.pause());
  ms.setActionHandler('nexttrack', () => next());
  ms.setActionHandler('previoustrack', () => previous());
  try { ms.setActionHandler('seekto', d => seek(d.seekTime)); } catch (e) {}
}

// Back where it was: same song, same second, paused until a tap (iOS only lets sound start from a tap).
export async function resume() {
  const r = await kv.get('resume');
  if (!r) return false;
  const all = new Map((await songs.all()).filter(s => s.ready).map(s => [s.id, s]));
  (r.remote || []).forEach(s => all.set(s.id, s));
  const q = r.queue.map(id => all.get(id)).filter(s => s && (!s.remote || navigator.onLine));
  if (!q.length) return false;
  player.queue = q;
  await load(0, false);
  if (!(audio.native && audio.id))                 // [Drift for Android] already playing in the app: stay where it is
    audio.addEventListener('loadedmetadata', () => { if (r.t) audio.currentTime = r.t; }, { once: true });
  return true;
}
