// Drift for Android. Inside the Android app (android/ in the repo) the page gets window.DriftNative, and with it:
// - a native player (ExoPlayer in a media service) that keeps playing with the phone locked, shows the song on the
//   lock screen and goes from song to song by itself; nativeAudio() wraps it in the <audio> element's shape so
//   player.js keeps its whole logic (queue, radio, counts) and only says "follow" instead of setting audio.src;
// - YouTube found and streamed on the phone itself (online.search / online.similar, same shape as open.js);
// - the shelf's song files kept by the app, where the native player can read them (files).
// Everywhere else (Safari on an iPhone, any browser) DriftNative is missing and everything here stays off.

const N = globalThis.DriftNative;
export const on = !!N;

// The pairing page on an Android phone without the app offers it (app.js welcome 'android').
export const APK_PAGE = 'https://github.com/Y-Hsn/drift/releases/latest';
export const isAndroidBrowser = () => !on && /Android/i.test(navigator.userAgent);
// drift://pair?<the QR's i=..&u=..&t=..>, as an intent: link so Chrome opens the release page when the app is missing
export const appLink = hash => `intent://pair?${hash.replace(/^#/, '')}#Intent;scheme=drift;package=io.github.yhsn.drift;` +
  `S.browser_fallback_url=${encodeURIComponent(APK_PAGE)};end`;

// ---- messages from the app: window.__drift({ev, ...}) ----
const waiting = new Map();
let asked = 0, audio = null;
if (on) globalThis.__drift = msg => {
  if (msg.ev === 'reply') {
    const w = waiting.get(msg.id); waiting.delete(msg.id);
    if (w) msg.ok ? w.ok(msg.data) : w.fail(new Error(msg.error || 'failed'));
    return;
  }
  audio?.hear(msg);
};

function ask(method, args = {}, timeout = 20000) {
  return new Promise((ok, fail) => {
    const id = ++asked;
    waiting.set(id, { ok, fail });
    setTimeout(() => { if (waiting.delete(id)) fail(new Error('the app did not answer')); }, timeout);
    N.call(id, method, JSON.stringify(args));
  });
}

// ---- YouTube on the phone (the same calls as open.js) ----
export const online = on ? {
  search: (q, n = 10) => ask('search', { q, n }),
  similar: (seed, n = 6, avoid = new Set()) => ask('similar', { seed, n, avoid: [...avoid].slice(-300) }, 30000),
  liked: () => {},                                   // the app keeps its own list of what was heard
  heard: () => {},
} : null;

// Self-update hook: resolves to {versionCode, url, tag} when GitHub has a newer APK, else null. Not shown yet.
export const update = on ? {
  check: () => ask('update', {}, 30000),
  install: url => N.install(url),
  version: () => N.version(),
} : null;

// ---- the native player in the <audio> element's shape ----
// Events: play, pause, timeupdate, loadedmetadata, ended like <audio>, plus 'moved' (detail = the song the player
// went on to by itself: end of a song, lock-screen next/previous, a pick of its own while the page slept).
class NativeAudio extends EventTarget {
  constructor() {
    super();
    this.native = true; this.paused = true; this.ended = false; this.preload = 'auto';
    this._t = 0; this._d = NaN; this.id = null;
    try { this.hear(JSON.parse(N.state())); } catch (e) {}
  }
  get currentTime() { return this._t; }
  set currentTime(t) { this._t = t; N.seek(t); }
  get duration() { return this._d; }
  set src(v) {}                                      // songs are picked with follow()
  setAttribute() {}
  play() {
    this.ended = false;
    if (this._q) this._q.play = true; else N.play();  // load() picked a song and plays it: one message for both
    return Promise.resolve();
  }
  pause() { N.pause(); }

  // The page's queue -> the player: queue[at] plays (or keeps playing), what comes after it follows. Sent a moment
  // later, so a play() right after (player.js load) goes with it. Without play the player keeps what it plays now
  // and tells the page ('moved') if that is another song: after a sleep the page follows the player, not back.
  follow(queue, at) {
    if (this._q) { this._q.queue = queue; this._q.at = at; return; }
    this._q = { queue, at, play: false };
    queueMicrotask(() => {
      const q = this._q; this._q = null;
      if (q.at < 0 || q.at >= q.queue.length) { if (q.play) N.play(); return; }
      const from = Math.max(0, q.at - 5), items = q.queue.slice(from, q.at + 40);
      N.queue(JSON.stringify({ at: q.at - from, play: q.play, items }));
    });
  }

  hear(m) {
    const fire = (type, detail) => this.dispatchEvent(detail === undefined ? new Event(type) : new CustomEvent(type, { detail }));
    if (m.ev === 'time') {
      this.id = m.id; this._t = m.pos || 0; if (m.dur > 0) this._d = m.dur;
      if (this.paused === m.playing) { this.paused = !m.playing; fire(m.playing ? 'play' : 'pause'); }
      fire('timeupdate');
    } else if (m.ev === 'state') {
      if (this.paused === m.playing) { this.paused = !m.playing; if (m.playing) this.ended = false; fire(m.playing ? 'play' : 'pause'); }
    } else if (m.ev === 'ready') {
      if (m.dur > 0) this._d = m.dur;
      fire('loadedmetadata');
    } else if (m.ev === 'moved') {
      this._t = m.prev || 0;                         // where the last song stopped, for player.js's counts
      this.id = m.song?.id;
      fire('moved', m.song);
      this._t = 0; this._d = m.song?.secs || NaN;
    } else if (m.ev === 'ended') {
      this.ended = true; this.paused = true;
      fire('ended');
    }
  }
}
export const nativeAudio = () => on ? (audio ||= new NativeAudio()) : null;

// ---- Drift radio in the app (player.js fill) ----
// The shelf first; online, YouTube songs like the one playing (a streamed song keeps the drift on YouTube, with a
// shelf song now and then); offline round the shelf again. Returns true when it did fill()'s job (in the app).
export async function fill(player, radioPick) {
  if (!on) return false;
  if (!player.radio || player.queue.length - player.at > 3) return true;
  const fresh = await radioPick(8, true);
  const streaming = !!player.song?.remote;
  let more = fresh;
  if (navigator.onLine && (fresh.length < 3 || streaming)) {
    const seed = player.song || fresh[0];
    const wide = seed ? await online.similar(seed, 6, new Set(player.queue.map(s => s.id))).catch(() => []) : [];
    more = streaming && wide.length ? [...wide.slice(0, 4), ...fresh.slice(0, 1), ...wide.slice(4)] : [...fresh, ...wide];
  }
  if (!more.length) more = await radioPick(8);
  player.queue.push(...more.map(s => ({ ...s, why: s.why || 'Drift radio' })));
  return true;
}

// ---- YouTube in the Search page (app.js search) ----
// A "Stream now · YouTube" list under the songs on the phone; tapping one streams it and drifts on from it.
let yt = null;
export function youtube(q, { after, row, play, current }) {
  if (!on) return;
  if (!yt) {
    const label = document.createElement('div'), list = document.createElement('ul');
    label.className = 'label'; label.textContent = 'Stream now · YouTube'; label.hidden = true;
    list.className = 'rows';
    after.after(label, list);
    yt = { label, list, q: '', timer: 0 };
  }
  clearTimeout(yt.timer);
  if (q.length < 2 || !navigator.onLine) { yt.q = ''; yt.label.hidden = true; yt.list.innerHTML = ''; return; }
  if (q === yt.q) return;
  yt.q = ''; yt.label.hidden = true; yt.list.innerHTML = '';      // never leave the last query's songs showing
  yt.timer = setTimeout(async () => {
    const found = await online.search(q, 12).catch(() => []);
    if (current() !== q) return;
    yt.q = q;
    yt.label.hidden = !found.length;
    yt.list.innerHTML = found.map(s => row(s, 'stream')).join('');
    yt.list.onclick = e => { const li = e.target.closest('li'); const s = li && found.find(x => x.id === li.dataset.id); if (s) play(s); };
  }, 350);
}

// ---- no PC yet: the app can stream from YouTube before it is paired (welcome 'Play from YouTube now') ----
export const skipped = () => on && localStorage.getItem('drift-no-pc') === '1';
export const skip = () => localStorage.setItem('drift-no-pc', '1');

// In the app, YouTube takes the place of the open-music (Audius) list, where that is still there.
if (on) document.head.insertAdjacentHTML('beforeend', '<style>#online-label,#online{display:none!important}</style>');

// ---- shelf files, kept by the app (store.js files) ----
const CHUNK = 768 * 1024;                            // bytes per hand-over (base64 through the bridge)
const b64 = blob => new Promise((ok, fail) => {
  const r = new FileReader();
  r.onload = () => ok(String(r.result).slice(String(r.result).indexOf(',') + 1));
  r.onerror = () => fail(r.error);
  r.readAsDataURL(blob);
});
export const files = on ? {
  async put(id, bytes, kind) {
    const blob = bytes instanceof Blob ? bytes : new Blob([bytes]);
    const h = N.fileBegin(id, kind);
    try {
      for (let at = 0; at < blob.size; at += CHUNK) {
        const r = N.fileChunk(h, await b64(blob.slice(at, at + CHUNK)));
        if (r === 'full') throw new DOMException('This phone is full', 'QuotaExceededError');
        if (r !== 'ok') throw new Error('could not keep the song on this phone');
      }
    } catch (e) { N.fileEnd(h, false); throw e; }
    if (!N.fileEnd(h, true)) throw new Error('could not keep the song on this phone');
  },
  drop: id => N.fileDrop(id),
  has: id => N.fileHas(id),
  used: () => N.fileUsed(),
} : null;
