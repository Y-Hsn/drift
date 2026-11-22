// Drift on the phone: pairing, the Now / Shelf / Search screens and the settings sheet.
import * as native from './native.js';          // [Drift for Android] the app's own player, YouTube, the APK offer
import { kv, songs, playlists, files, space, keep } from './store.js';
import * as sync from './sync.js';
import { player, playList, playRadio, next, previous, toggle, seek, like, playNext, resume } from './player.js';
import { Scape } from './scape.js';

const $ = id => document.getElementById(id);
const show = (el, on = true) => { el.hidden = !on; };
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const clock = t => { t = Math.max(0, t | 0); return `${(t / 60) | 0}:${String(t % 60).padStart(2, '0')}`; };
const gb = n => n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.round(n / 1e6)} MB`;

// ---------- start ----------

async function start() {
  // [Drift for Android] the app has its files in the APK and its songs with the native player: no service worker
  if ('serviceWorker' in navigator && !native.on) {
    // a new version of the app took over (it installs in the background at launch): show it now, once
    const had = !!navigator.serviceWorker.controller;
    let reloaded = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (had && !reloaded && !player.playing) { reloaded = true; location.reload(); }
    });
    navigator.serviceWorker.register('sw.js').catch(() => {});
  }
  const hash = location.hash;
  const inv = sync.invite();
  let pair = await sync.paired();
  if (inv && native.isAndroidBrowser()) return offerApp(hash, inv);   // [Drift for Android]
  if (inv) return pairWith(inv);
  if (!pair && sync.pendingTicket()) {
    try { await sync.finish(); pair = await sync.paired(); } catch (e) { return welcome('error', e.message); }
  }
  if (!pair && !native.skipped()) return welcome('scan');
  openMain();
}

// ---------- pairing ----------

let welcomeScape = null;
function welcome(mode, detail = '') {
  show($('welcome')); show($('main'), false);
  if (!welcomeScape) {
    welcomeScape = new Scape($('welcome-scape'));
    welcomeScape.set({ id: 'drift-welcome', title: 'Drift' }, null);
    welcomeScape.draw(0.62, true);
  }
  const title = $('welcome-title'), note = $('welcome-note');
  show($('homescreen-steps'), mode === 'homescreen'); show($('welcome-error'), mode === 'error');
  show($('android-app'), mode === 'android'); show($('app-skip'), native.on && mode === 'scan');   // [Drift for Android]
  if (mode === 'android') { title.textContent = 'Drift for Android'; note.textContent = 'Get the free Drift app: it pairs with your PC like this page, and plays any song from YouTube too, also with the phone locked. Have it already? Tap Open in the Drift app.'; }
  if (mode === 'scan') { title.textContent = 'Your music, on your phone'; note.textContent = 'Open Drift on your PC, go to Settings > Phones > Pair a phone, and scan the code with your camera.'; }
  if (mode === 'pairing') { title.textContent = 'Pairing…'; note.textContent = 'Saying hello to your PC.'; }
  if (mode === 'homescreen') { title.textContent = `Paired with ${detail}`; note.textContent = 'One more step: put Drift on your home screen. Your songs then play from there, also without internet.'; }
  if (mode === 'error') { title.textContent = 'That did not work'; note.textContent = 'Show a new code on your PC (Settings > Phones > Pair a phone) and scan it again.'; $('welcome-error').textContent = detail; }
}

async function pairWith(inv) {
  welcome('pairing');
  try {
    const r = await sync.redeem(inv);
    if (r.needsHomeScreen) return welcome('homescreen', r.pcName);
    openMain();
  } catch (e) {
    welcome('error', e.message === 'Failed to fetch' ? 'Your PC could not be reached. Is Drift running there?' : e.message);
  }
}

// [Drift for Android] an Android phone opened the PC's code in the browser: offer the app (same invite), or go on here.
function offerApp(hash, inv) {
  welcome('android');
  $('app-open').href = native.appLink(hash);
  $('app-get').href = native.APK_PAGE;
  $('app-web').onclick = () => pairWith(inv);
}

// ---------- main ----------

let scape = null, page = 'now', shelfTab = 'songs';
async function openMain() {
  show($('welcome'), false); show($('main'));
  scape = new Scape($('scape'));
  wire();
  if (!(await resume())) setNow(null);
  keep();
  sync.state.listeners.add(st => { $('sync-line').textContent = st.note; if (!st.busy) refresh(); });
  sync.syncNow();
  addEventListener('online', () => sync.syncSoon([800]));
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') sync.syncSoon([800]); });
  setInterval(() => document.visibilityState === 'visible' && sync.syncNow(), 5 * 60 * 1000);
  refresh();
}

function wire() {
  document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => go(b.dataset.page));
  $('play').onclick = () => toggle();
  $('next').onclick = () => next();
  $('prev').onclick = () => previous();
  $('radio').onclick = () => playRadio(player.song);
  $('like').onclick = () => player.song && like(player.song);
  $('open-settings').onclick = openSheet;
  $('close-sheet').onclick = () => show($('sheet'), false);
  $('sheet').onclick = e => { if (e.target === $('sheet')) show($('sheet'), false); };
  $('sync-now').onclick = () => { sync.syncNow(); show($('sheet'), false); };
  $('budget').onclick = async e => {
    const b = e.target.closest('[data-gb]'); if (!b) return;
    await kv.set('budget', +b.dataset.gb * 1024 ** 3); await kv.set('rev', ''); openSheet(); sync.syncSoon([300]);
  };
  $('shelf-chips').onclick = e => { const b = e.target.closest('[data-shelf]'); if (b) { shelfTab = b.dataset.shelf; refresh(); } };
  $('search-form').onsubmit = e => { e.preventDefault(); $('search').blur(); search(); };
  $('search').oninput = () => search();
  $('wish-btn').onclick = async () => {
    const q = $('search').value.trim(); if (!q) return;
    await sync.wish(q); search();
  };
  // seek on the hills
  const sc = $('scape');
  const at = e => { const r = sc.getBoundingClientRect(); return Math.max(0, Math.min(1, ((e.touches?.[0] || e).clientX - r.left) / r.width)); };
  let dragging = false;
  sc.addEventListener('pointerdown', e => { if (!player.song) return; dragging = true; sc.setPointerCapture(e.pointerId); scape.draw(at(e)); });
  sc.addEventListener('pointermove', e => { if (dragging) { scape.draw(at(e)); $('now-at').textContent = clock(at(e) * player.length); } });
  sc.addEventListener('pointerup', e => { if (dragging) { dragging = false; seek(at(e) * player.length); } });
  player.listeners.add(what => {
    if (what === 'song') setNow(player.song);
    if (what === 'state') $('play-glyph').innerHTML = player.playing ? '<path d="M8 5v14M16 5v14" stroke="currentColor" stroke-width="3"/>' : '<path d="M8 5l11 7-11 7z"/>';
    if (what === 'time' && !dragging) {
      $('now-at').textContent = clock(player.time); $('now-len').textContent = clock(player.length);
      if (page === 'now') scape.draw(player.length ? player.time / player.length : 0);
    }
    if (what === 'queue' || what === 'song') drawQueue();
    if (what === 'blocked') $('sync-line').textContent = 'Tap play to start';
  });
}

function go(p) {
  page = p;
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.page === p));
  ['now', 'shelf', 'search'].forEach(x => show($('page-' + x), x === p));
  $('page-eyebrow').textContent = { now: 'Now', shelf: 'On this phone', search: 'Find' }[p];
  $('page-title').textContent = { now: 'Drift', shelf: 'Shelf', search: 'Search' }[p];
  if (p === 'search') setTimeout(() => $('search').focus(), 50);
  refresh();
}

function setNow(s) {
  $('now-title').textContent = s ? s.title : 'Nothing playing';
  $('now-artist').textContent = s ? [s.artist, s.album].filter(Boolean).join(' · ') : (sync.state.online ? 'Tap play for Drift radio' : 'Tap play for Drift radio from your shelf');
  $('now-why').textContent = s?.why || '';
  $('like').classList.toggle('on', !!s?.liked);
  $('now-len').textContent = clock(s?.secs || 0);
  if (!s) { scape.set({ id: 'drift-empty' }, null); return; }
  const img = new Image();
  img.onload = () => scape.set(s, img);
  img.onerror = () => scape.set(s, null);
  img.crossOrigin = 'anonymous';
  if (s.remote && s.art) img.src = s.art; else if (s.cover) img.src = files.coverUrl(s.id); else scape.set(s, null);
  if (document.title !== s.title) document.title = `${s.title} · Drift`;
}

function row(s, extra = '') {
  const pic = s.remote ? s.art : s.cover ? files.coverUrl(s.id) : '';
  const art = pic ? `style="background-image:url('${esc(pic)}')"` : '';
  return `<li data-id="${esc(s.id)}" class="${player.song?.id === s.id ? 'playing' : ''}"><div class="art" ${art}></div>` +
         `<div class="t"><b>${esc(s.title)}</b><span>${esc(s.artist || '')}</span></div><span class="tag">${extra}</span></li>`;
}

function drawQueue() {
  const up = player.queue.slice(player.at + 1, player.at + 8);
  $('queue').innerHTML = up.map(s => row(s, s.remote ? 'online' : s.why === 'Drift radio' ? 'radio' : '')).join('');
  $('queue').onclick = e => { const li = e.target.closest('li'); if (li) { const i = player.queue.findIndex((s, k) => k > player.at && s.id === li.dataset.id); if (i > 0) playList(player.queue, i, player.radio); } };
}

async function refresh() {
  const all = (await songs.all()).filter(s => s.ready);
  if (page === 'shelf') {
    document.querySelectorAll('#shelf-chips .chip').forEach(c => c.classList.toggle('on', c.dataset.shelf === shelfTab));
    let html = '', quiet = '';
    if (shelfTab === 'playlists') {
      const pls = await playlists.all(), byId = new Map(all.map(s => [s.id, s]));
      html = pls.map(p => {
        const n = (p.items || []).filter(it => byId.has(it.song)).length;
        return `<li data-pl="${esc(p.id)}"><div class="art"></div><div class="t"><b>${esc(p.name)}</b><span>${n} songs here</span></div><span class="tag"></span></li>`;
      }).join('');
      quiet = 'Playlists from your PC show up here after a sync.';
      $('shelf').onclick = e => {
        const li = e.target.closest('li'); if (!li) return;
        const p = pls.find(x => x.id === li.dataset.pl);
        const list = (p.items || []).map(it => byId.get(it.song)).filter(Boolean);
        if (list.length) { playList(list.map(s => ({ ...s, why: p.name })), 0, true); go('now'); }
      };
    } else {
      const list = (shelfTab === 'liked' ? all.filter(s => s.liked) : all).sort((a, b) => (a.artist || '').localeCompare(b.artist || '') || a.title.localeCompare(b.title));
      html = list.map(s => row(s, s.pinned ? 'kept' : '')).join('');
      quiet = shelfTab === 'liked' ? 'Songs you like show up here.' : 'No songs on this phone yet. They arrive when your PC is on.';
      $('shelf').onclick = e => { const li = e.target.closest('li'); if (!li) return; const i = list.findIndex(s => s.id === li.dataset.id); playList(list, i, true); go('now'); };
    }
    $('shelf').innerHTML = html; $('shelf-quiet').textContent = quiet; show($('shelf-quiet'), !html);
  }
  if (page === 'search') search();
  if (page === 'now') drawQueue();
}

let searchAll = null;
async function search() {
  const q = $('search').value.trim().toLowerCase();
  searchAll = searchAll && !sync.state.busy ? searchAll : (await songs.all()).filter(s => s.ready);
  const hits = q ? searchAll.filter(s => `${s.title} ${s.artist} ${s.album}`.toLowerCase().includes(q)).slice(0, 50) : [];
  $('results').innerHTML = hits.map(s => row(s)).join('');
  $('results').onclick = e => { const li = e.target.closest('li'); if (!li) return; const s = hits.find(x => x.id === li.dataset.id); if (s) { playRadio(s); go('now'); } };
  const box = $('wish-box');
  native.youtube(q, { after: $('results'), row, current: () => $('search').value.trim().toLowerCase(),   // [Drift for Android]
                      play: s => { playRadio({ ...s, why: 'Streaming' }); go('now'); } });
  show(box, q.length > 1);
  if (q.length > 1) {
    const wished = (await kv.get('wishes', [])).some(w => w.query.trim().toLowerCase() === q);
    $('wish-text').textContent = wished
      ? (sync.state.online ? 'Your PC is getting it. It shows up here in a moment.' : 'It arrives the next time this phone and your PC are both online.')
      : hits.length ? 'Not the one? Your PC can find it.' : 'Not on this phone yet. Your PC can find it and send it over.';
    $('wish-btn').disabled = wished; $('wish-btn').textContent = wished ? 'Wished' : 'Get it from my PC';
  }
  refreshWishes();
}

async function refreshWishes() {
  const w = (await kv.get('wishes', [])).slice(0, 8);
  show($('wishes-label'), w.length > 0);
  $('wishes').innerHTML = w.map(x => `<li><div class="t"><b>${esc(x.query)}</b></div><span class="tag">${new Date(x.at).toLocaleDateString()}</span></li>`).join('');
}

async function openSheet() {
  const pair = await sync.paired(), sp = await space(), budget = await kv.get('budget', 2 * 1024 ** 3);
  const count = (await songs.all()).filter(s => s.ready).length;
  $('set-pc').textContent = pair ? `${sync.cap(pair.pcName)}${sync.state.online ? ' · online' : ''}` : '—';
  $('set-space').textContent = `${count} songs · ${gb(sp.used)}`;
  document.querySelectorAll('#budget .chip').forEach(c => c.classList.toggle('on', +c.dataset.gb * 1024 ** 3 === budget));
  show($('sheet'));
}

$('app-skip').onclick = () => { native.skip(); openMain(); };   // [Drift for Android] stream before pairing
start();
