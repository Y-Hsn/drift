"""What plays: the queue, history, shuffle/repeat and Drift radio (the queue never runs dry: songs like the ones you
love while online, your saved songs by taste while offline). The sound itself is made by drift-core (core.py), which
keeps playing, and keeps the queue going, after this window is closed. Also saves songs for offline."""
import random
import time
from dataclasses import replace

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from . import library, online, taste, tasks
from .core import Core
from .store import Track, norm

SAVE_AT = 0.6          # a streamed song played this far is saved for offline (when save_played is on)
SKIP_BEFORE = 0.35     # leaving a song before this point counts as a skip (taste)


class Engine(QObject):
    track_changed = pyqtSignal(object)        # Track or None
    playing_changed = pyqtSignal(bool)
    position_changed = pyqtSignal(int, int)   # ms, duration ms
    queue_changed = pyqtSignal()
    loading = pyqtSignal(bool)
    message = pyqtSignal(str)
    online_changed = pyqtSignal(bool)
    track_updated = pyqtSignal(object)        # liked/saved state of a song changed
    library_changed = pyqtSignal()
    downloads_changed = pyqtSignal(int, float)  # songs waiting, progress of the current one 0..1
    show_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    wave_ready = pyqtSignal(str, list)        # uid, the song's loudness shape (0..255 bars) from the core

    def __init__(self, store, parent=None, core=None):
        super().__init__(parent)
        self.store = store
        self.current = None
        self.queue = []          # up next
        self.history = []        # played before current, newest last
        self.repeat = 'off'      # off | all | one
        self.shuffle = False
        self.drift = bool(store.get('drift'))
        self.online = True
        self.playing = False
        self.position = 0
        self.duration = 0
        self.muted = False
        self.core_loaded = False  # the core has (or is opening) the current song
        self._loading = False
        self._counted = False    # current song already counted as played
        self._saved_req = False
        self._refilling = None
        self._play_after = False
        self._radio_revision = 0
        self._radio_waiting = None
        self._radio_catalog = taste.RadioCatalog(lambda t, n: online.related(t, n, match=taste.recording))
        self._radio_events = []
        self._listened = 0
        self._resume_at = 0
        self._before_link = None
        self.active_link = None
        self._loading_link = False
        self.downloads = []      # tracks waiting to be saved
        self.wave = ('', [])     # (uid, shape) of the song playing, once the core measured it
        self._downloading = None
        self.volume = max(0.0, min(1.0, float(store.get('volume', 0.6))))
        self.core = core or Core(self)
        self.core.message.connect(self._event)
        self.core.ready.connect(self._ready)
        self.queue_changed.connect(self._sync_queue)
        self.net_timer = QTimer(self, interval=20_000, timeout=self._check_net)
        self.net_timer.start()

    def start(self):
        self.core.start()

    # ---- the core ------------------------------------------------------------------------------------------------
    def item(self, t, resolve=True):
        """A song as the core knows it: a file path, or (for online songs) a stream link when one is at hand."""
        src, headers = (t.path, {}) if t.local else (online.cached_link(t.uid) if resolve else (None, {}))
        return {'id': t.uid, 'src': src, 'headers': headers or {}, 'page': t.page, 'title': t.title,
                'artist': t.artist, 'duration': t.duration or 0, 'save': bool(self.store.get('save_played', False)),
                'thumb': t.thumb or None, 'radio': bool(t.extra.get('radio'))}

    def _ready(self):
        self.core.send('volume', v=self.volume)
        self.core.send('repeat', mode=self.repeat)
        self.core.send('drift', on=self.drift)
        self.send_sound()
        self.core.send('state')
        self.core.send('wave')

    def send_sound(self):
        self.core.send('sound', level=bool(self.store.get('sound_level', True)), tone=bool(self.store.get('sound_tone', True)))

    def _sync_queue(self):
        self.core.send('upcoming', items=[self.item(t) for t in self.queue])

    def _track_for(self, item):
        uid = (item or {}).get('id') or ''
        generated = bool(item.get('radio'))
        for t in self.queue + self.history[-20:]:
            if t.uid == uid and bool(t.extra.get('radio')) == generated:
                return t
        t = self.store.track(uid) or Track(uid=uid, title=item.get('title') or '', artist=item.get('artist') or '',
                                           duration=int(item.get('duration') or 0), page=item.get('page'), source='youtube')
        return self._generated(t) if generated else t

    def _event(self, ev):
        kind = ev.get('ev')
        if kind == 'pos':
            if self.current and ev.get('audio_id') == self.current.uid:
                self._listened = int(ev.get('listened') or 0)
            self._on_pos(int(ev.get('ms') or 0), int(ev.get('dur') or 0), bool(ev.get('playing')),
                         bool(ev.get('loading')))
        elif kind == 'state':
            self._on_state(ev)
        elif kind == 'track':
            self._on_core_track(ev.get('item') or {}, bool(ev.get('back')))
        elif kind == 'played':
            self._count_play()
        elif kind == 'ended':
            self.core_loaded = False
            self._set_playing(False)
            if self.drift:
                self._refill(play_after=True)
        elif kind == 'need':
            if self.drift and len(self.queue) < 3:
                self._refill()
        elif kind == 'resolve':
            self._resolve(ev.get('req'), ev.get('item') or {})
        elif kind == 'error':
            self._on_error(ev)
        elif kind == 'wave':
            self.wave = (ev.get('id') or '', list(ev.get('wave') or []))
            self.wave_ready.emit(*self.wave)
        elif kind == 'show':
            self.show_requested.emit()
        elif kind == 'quit':
            self.quit_requested.emit()

    def _on_state(self, ev):
        """The window opened while the core already plays: take over its song and queue."""
        cur = ev.get('current')
        if not cur:
            return
        track = self._track_for(cur)
        self.current = track
        self.queue = [self._track_for(i) for i in ev.get('upcoming') or []]
        self._listened = int(ev.get('listened') or 0)
        self.core_loaded = bool(ev.get('loaded') or ev.get('loading'))
        self.repeat = ev.get('repeat') or self.repeat
        self.track_changed.emit(track)
        self.queue_changed.emit()
        self._on_pos(int(ev.get('pos') or 0), int(ev.get('dur') or 0), bool(ev.get('playing')), bool(ev.get('loading')))
        if self.core_loaded and len(self.queue) < 3:
            self._refill()

    def _on_core_track(self, item, back):
        """The core moved on by itself (song ended, media key, tray)."""
        track = self._track_for(item)
        old = self.current
        if back:
            if self.history and self.history[-1].uid == track.uid:
                self.history.pop()
            if old:
                self.queue.insert(0, old)
        else:
            if old:
                self.history.append(old)
                self.history = self.history[-300:]
            i = next((k for k, t in enumerate(self.queue) if t.uid == track.uid), -1)
            if i >= 0:
                del self.queue[i]
        self._became_current(track)
        self.queue_changed.emit()
        if self.drift and len(self.queue) < 3:
            self._refill()

    def _became_current(self, track):
        self.current = track
        self.core_loaded = True
        self._counted = self._saved_req = False
        self._listened = 0
        self.position, self.duration = 0, track.duration or 0
        self.store.upsert(track)
        self.track_changed.emit(track)
        self._prefetch()

    def _resolve(self, req, item):
        track = self._track_for(item)
        if not track.local and (not self.online or online.blocked(track)):
            self.core.send('reply', req=req, id=item.get('id'), error='offline')
            return

        def ok(r):
            self.core.send('reply', req=req, id=item.get('id'), src=r[0], headers=r[1] or {}, duration=track.duration)

        def bad(e):
            self.core.send('reply', req=req, id=item.get('id'), error=str(e)[:200] or 'cannot play')

        tasks.run(online.stream, track, done=ok, failed=bad)

    def _on_error(self, ev):
        t = self.current
        if t and ev.get('id') == t.uid and not t.local:
            online.forget_stream(t.uid)

        def judged(on):
            self._set_online(on)
            stuck = on and t and online.blocked(t)
            if stuck or not on:
                self.message.emit('YouTube is pausing streams for a while: playing your saved songs.' if stuck
                                  else 'Offline: playing your saved songs.')
                self.queue = [q for q in self.queue if q.local]
                if not self.queue:
                    self._offline_fallback(play_after=not self.core_loaded)
                else:
                    self.queue_changed.emit()
            elif t:
                self.message.emit(f'“{t.title}” cannot be played right now; next one.')

        tasks.run(online.is_online, done=judged)

    def _on_pos(self, ms, dur, playing, loading):
        self.position = ms
        if dur > 0:
            self.duration = dur
        if loading != self._loading:
            self._loading = loading
            self.loading.emit(loading)
        self._set_playing(playing)
        self.position_changed.emit(ms, self.duration)
        t = self.current
        if not t or self.duration <= 0:
            return
        if not t.duration:
            t.duration = self.duration
        frac = ms / self.duration
        if not self._counted and frac >= 0.5:
            self._count_play()
        if (not self._saved_req and frac >= SAVE_AT and not t.local and t.page
                and self.store.get('save_played', False)):
            self._saved_req = True
            self.save(t, quiet=True)

    def _count_play(self):
        t = self.current
        if t and not self._counted:
            self._counted = True
            self.store.played(t.uid)
            t.plays += 1
            t.last_played = time.time()

    def _set_playing(self, on):
        if on != self.playing:
            self.playing = on
            self.playing_changed.emit(on)

    # ---- volume -----------------------------------------------------------------------------------------------
    def set_volume(self, v):
        self.volume = max(0.0, min(1.0, v))
        self.core.send('volume', v=self.volume)
        self.store.set('volume', round(self.volume, 3))

    def toggle_mute(self):
        self.muted = not self.muted
        self.core.send('mute', on=self.muted)

    # ---- what plays --------------------------------------------------------------------------------------------
    def play(self, track, context=None):
        """Play `track` now; with `context` (the list it was picked from) the rest of that list becomes up next."""
        self.finish_link(self.active_link)
        self.active_link = None
        self._leave_current()
        self._reset_radio()
        if self.current:
            self.history.append(self.current)
        if context:
            i = next((k for k, t in enumerate(context) if t.uid == track.uid), -1)
            rest = list(context[i + 1:]) if i >= 0 else []
            if self.shuffle:
                random.shuffle(rest)
            self.queue = rest
        else:
            self.queue = [t for t in self.queue if not t.extra.get('radio')]
        self._load(replace(track, extra={k: v for k, v in track.extra.items() if k != 'radio'})
                   if track.extra.get('radio') else track)
        self.queue_changed.emit()

    def play_list(self, tracks, shuffle=None):
        if not tracks:
            return
        tracks = list(tracks)
        if shuffle if shuffle is not None else self.shuffle:
            random.shuffle(tracks)
        self.queue = tracks[1:]
        self.play(tracks[0])

    def play_link(self, track, request):
        if self.current and self.active_link is None and self._before_link is None:
            self._before_link = (self.current, self.position, self.queue[:])
        self.queue = []
        self.play(track)
        self.active_link = request
        self._loading_link = True
        self.core.send('drift', on=False)

    def finish_link(self, request):
        if self._loading_link and self.active_link is request:
            self._loading_link = False
            self.core.send('drift', on=self.drift)

    @property
    def can_resume_link(self):
        return self._before_link is not None

    def resume_before_link(self):
        if self._before_link is None:
            return False
        self.finish_link(self.active_link)
        self._reset_radio()
        track, position, self.queue = self._before_link
        self._before_link = self.active_link = None
        self._load(track, position)
        self.queue_changed.emit()
        return True

    def play_next(self, tracks):
        self.queue[0:0] = list(tracks)
        self.queue_changed.emit()
        if not self.current:
            self.next()

    def enqueue(self, tracks):
        self.queue.extend(tracks)
        self.queue_changed.emit()
        if not self.current:
            self.next()

    def remove_from_queue(self, index):
        if 0 <= index < len(self.queue):
            del self.queue[index]
            self.queue_changed.emit()

    def clear_queue(self):
        self._reset_radio()
        self.queue.clear()
        self.queue_changed.emit()

    def toggle(self):
        if not self.current:
            if self.queue:
                self.next()
            else:
                self.start_radio()
        elif self.core_loaded:
            self.core.send('toggle')
            self._set_playing(not self.playing)     # shows at once; the core confirms within 250 ms
        else:
            self._load(self.current, self._resume_at)

    def next(self, auto=False):
        if not auto:
            self._leave_current()
        if not self.queue:
            if self.repeat == 'all' and self.history:
                self.queue = self.history[-200:] + ([self.current] if self.current else [])
                self.history = []
            elif self.drift:
                self._refill(play_after=True)
                return
            else:
                self.core.send('stop')
                self.core_loaded = False
                return
        if self.current:
            self.history.append(self.current)
            self.history = self.history[-300:]
        self._load(self.queue.pop(0))
        self.queue_changed.emit()

    def previous(self):
        if self.position > 3000 or not self.history:
            self.seek(0)
            return
        self._leave_current()
        if self.current:
            self.queue.insert(0, self.current)
        self._load(self.history.pop())
        self.queue_changed.emit()

    def seek(self, ms):
        self.position = int(max(0, ms))
        if self.core_loaded:
            self.core.send('seek', ms=self.position)
        else:
            self._resume_at = self.position
        self.position_changed.emit(self.position, self.duration)

    def set_shuffle(self, on):
        self.shuffle = on
        if on:
            random.shuffle(self.queue)
            self.queue_changed.emit()

    def cycle_repeat(self):
        self.repeat = {'off': 'all', 'all': 'one', 'one': 'off'}[self.repeat]
        self.core.send('repeat', mode=self.repeat)
        return self.repeat

    def set_drift(self, on):
        self.drift = on
        self.store.set('drift', on)
        self.core.send('drift', on=on and not self._loading_link)
        if not on:
            self._reset_radio()
        if on and self.current and len(self.queue) < 3:
            self._refill()

    # ---- radio ---------------------------------------------------------------------------------------------------
    def start_radio(self, seed=None, artist=None):
        """Endless music: from a song, an artist, or (nothing given) from your taste."""
        self.finish_link(self.active_link)
        self.active_link = None
        self._reset_radio()
        self.queue.clear()
        self.queue_changed.emit()
        self.drift = True
        self.store.set('drift', True)
        self.core.send('drift', on=True)
        if artist and not self.online:      # offline: the artist's saved songs first, then the rest by taste
            saved = self.store.local_tracks()
            own = [t for t in saved if norm(t.artist) == norm(artist)]
            order = taste.offline_order(own, (), len(own))
            if order:
                self.message.emit(f'{artist} mix, from this PC.')
                self.play(order[0], order)
            elif saved:
                self._offline_fallback(True)
            else:
                self.message.emit('No internet, and no saved songs yet.')
            return
        if artist:
            self.message.emit(f'Starting a {artist} mix…')
            self.loading.emit(True)
            revision = self._radio_revision
            tasks.run(lambda: online.search(artist, 'ytmusic', 6),
                      done=lambda hits: self._artist_seeds(hits) if revision == self._radio_revision else None,
                      failed=lambda e: self._offline_fallback(True) if revision == self._radio_revision else None)
            return
        self._refill(play_after=True, seed=seed)

    def _artist_seeds(self, hits):
        hits = [h for h in hits if h.video_id]
        if not hits:
            self._offline_fallback(play_after=True)
            return
        first = hits[0]
        self.store.upsert(first)
        self.queue = hits[1:3]
        self.play(first)

    def _reset_radio(self):
        self._radio_revision += 1
        self._radio_waiting = None
        self._play_after = False

    @staticmethod
    def _generated(t):
        return replace(t, extra={**t.extra, 'radio': True})

    def _radio(self, seed=None):
        now = time.time()
        self._radio_events = [event for event in self._radio_events if now - event[1] < 30]
        return taste.Radio.from_store(self.store, self.current, self.queue, seed, self._radio_events)

    def _refill(self, play_after=False, seed=None):
        if self._loading_link or not self.drift:
            return
        if self._refilling is not None:
            if self._refilling != self._radio_revision:
                prior = self._radio_waiting or (False, None)
                self._radio_waiting = (play_after or prior[0], seed or prior[1])
            else:
                self._play_after |= play_after
            return
        if not self.online or online.blocked():
            self._offline_fallback(play_after, seed)
            return
        saved = self.store.local_tracks()
        radio = self._radio(seed)
        if not radio.anchor(saved):
            self.message.emit('Search for a song to start your radio.')
            return
        revision = self._radio_revision
        self._refilling = revision
        if play_after:
            self.loading.emit(True)

        def done(picks):
            self._refilling = None
            if revision != self._radio_revision:
                waiting, self._radio_waiting = self._radio_waiting, None
                if waiting:
                    self._refill(*waiting)
                return
            play_after_now = play_after or self._play_after
            self._play_after = False
            if self._loading_link or not self.drift:
                return
            recent = [t for t in [self.current, *self.queue, *self.history[-3:]] if t]
            ids = {t.uid for t in recent}
            excluded = {taste.recording(t) for t in recent}
            picks = [t for t in picks if t.uid not in ids and taste.recording(t) not in excluded]
            if not picks:
                self._offline_fallback(play_after_now, seed)
                return
            self.queue.extend(self._generated(self.store.refresh(t)) for t in picks)
            self.queue_changed.emit()
            if play_after_now:
                self.next(auto=True)
            else:
                self._prefetch()

        tasks.run(radio.plan, self._radio_catalog, saved, done=done, failed=lambda e: done([]))

    def _skip_unsaved(self, why):
        """Streams cannot play now: keep only saved songs in the queue and go on with the next one."""
        self.message.emit(why)
        self.queue = [q for q in self.queue if q.local]
        if not self.queue:
            self._offline_fallback(play_after=True)
        else:
            self.queue_changed.emit()
            QTimer.singleShot(0, lambda: self.next(auto=True))

    def _offline_fallback(self, play_after, seed=None):
        self.loading.emit(False)
        saved = self.store.local_tracks()
        if not saved:
            self.message.emit('No internet, and no saved songs yet.')
            return
        if not self.online:
            self.message.emit('Offline: playing your saved songs.')
        picks = self._radio(seed).offline(saved)
        self.queue.extend(self._generated(t) for t in picks)
        self.queue_changed.emit()
        if play_after and self.queue:
            self.next(auto=True)

    # ---- loading a song ----------------------------------------------------------------------------------------
    def _load(self, track, start_ms=0):
        if not track.local and not track.page:
            self.message.emit(f'Cannot play {track.title}: the file is gone.')
            self.current = track
            QTimer.singleShot(0, lambda: self.next(auto=True))
            return
        if not track.local and (not self.online or online.blocked(track)):
            self.current = track
            self._skip_unsaved('Offline: skipping songs that are not saved.' if not self.online else
                               'YouTube is pausing streams for a while: playing your saved songs.')
            return
        self._became_current(track)
        self.position = start_ms
        self.core.send('load', item=self.item(track), start=int(start_ms))
        self._set_playing(True)
        if self.drift and len(self.queue) < 3:
            self._refill()

    def _prefetch(self):
        """Look up the next streamed song's link now, so it starts at once (the core gets it with the queue)."""
        nxt = next((t for t in self.queue[:1] if not t.local and t.page), None)
        if nxt and self.online and not online.blocked(nxt) and not online.cached_link(nxt.uid)[0]:
            tasks.run(online.stream, nxt, done=lambda r: self._sync_queue(), failed=lambda e: None)

    def _leave_current(self):
        """Counts a skip when you leave a song early (it teaches taste)."""
        t = self.current
        if not t:
            return
        self.core.send('leave', id=t.uid)
        dur = self.duration or t.duration
        if self.drift and taste.early_skip(self._listened, dur):
            event = (t.uid, time.time(), 'skip')
            if not self._radio_events or self._radio_events[-1][0] != t.uid or event[1] - self._radio_events[-1][1] > 2:
                self._radio_events.append(event)
            self._listened = 0
            # If the next automatic pick has the same lead artist, give a different artist a turn now.
            if self.queue and self.queue[0].extra.get('radio') and taste.credit(self.queue[0]) == taste.credit(t):
                for i, candidate in enumerate(self.queue[1:], 1):
                    if not candidate.extra.get('radio'):
                        break
                    if taste.credit(candidate) != taste.credit(t):
                        self.queue.insert(0, self.queue.pop(i))
                        break
        if dur and 5000 < self.position < dur * SKIP_BEFORE and not self._counted:
            self.store.skipped(t.uid)
            t.skips += 1

    # ---- likes and saving ---------------------------------------------------------------------------------------
    def like(self, track, on=None):
        on = (not track.liked) if on is None else on
        track.liked = int(on)
        self.store.upsert(track)
        self.store.set_field(track.uid, liked=int(on))
        self.track_updated.emit(track)   # a like is only a like: saving to this PC is its own button

    def saving(self, track):
        """True while the song waits for or is being saved to this PC."""
        return any(t.uid == track.uid for t in self.downloads) or bool(
            self._downloading and self._downloading.uid == track.uid)

    def unsave(self, track):
        """Take a song off this PC: the file goes to the Recycle Bin. An online song stays in Drift (it streams
        again); your own file is forgotten. Returns True when done."""
        self.downloads = [t for t in self.downloads if t.uid != track.uid]
        self.downloads_changed.emit(len(self.downloads) + bool(self._downloading), 0.0)
        if not track.path:
            return True
        try:
            library.recycle(track.path)
        except OSError:
            self.message.emit('Could not remove it now (Windows has the file open). Try again after this song.')
            return False
        title = track.title
        if track.page:
            track.path = None
            self.store.set_field(track.uid, path=None)
        else:
            self.store.drop_track(track.uid)
        self.track_updated.emit(track)
        self.library_changed.emit()
        self.message.emit(f'Removed “{title}” from this PC (it is in the Recycle Bin).')
        return True

    def save(self, track, quiet=False):
        if track.local or not track.page:
            return
        if any(t.uid == track.uid for t in self.downloads) or (self._downloading and self._downloading.uid == track.uid):
            return
        self.store.upsert(track)
        self.downloads.append(track)
        if not quiet:
            self.message.emit(f'Saving “{track.title}” for offline.')
        self._next_download()

    def _next_download(self):
        if self._downloading or not self.downloads:
            self.downloads_changed.emit(len(self.downloads), 0.0)
            return
        track = self._downloading = self.downloads.pop(0)
        folder = self.store.get('music_folder')
        self.downloads_changed.emit(len(self.downloads) + 1, 0.0)
        last = [0.0]

        def progress(done, total):
            if total and done / total - last[0] > 0.05:
                last[0] = done / total
                # signals are thread-safe: the bar updates on the window's thread
                self.downloads_changed.emit(len(self.downloads) + 1, done / total)

        def ok(path):
            track.path = path
            self.store.upsert(track)
            self.store.set_field(track.uid, path=path)
            self._downloading = None
            self.track_updated.emit(track)
            self.library_changed.emit()
            self._next_download()

        def bad(_e):
            self._downloading = None
            self._next_download()

        tasks.run(online.download, track, folder, progress, done=ok, failed=bad)

    # ---- internet -----------------------------------------------------------------------------------------------
    def _set_online(self, value):
        if value != self.online:
            self.online = value
            self.online_changed.emit(value)
            if value:
                self.message.emit('Back online.')

    def _check_net(self):
        tasks.run(online.is_online, done=self._set_online)

    # ---- session ------------------------------------------------------------------------------------------------
    def save_session(self):
        if self.current:
            self.store.set('session', {'current': self.current.uid, 'pos': self.position,
                                       'queue': [t.uid for t in self.queue[:100]],
                                       'radio': [i for i, t in enumerate(self.queue[:100]) if t.extra.get('radio')]})

    def restore_session(self):
        """Last time's song, paused where you left it (unless the core is still playing: then that wins)."""
        if self.current:
            return
        s = self.store.get('session') or {}
        cur = self.store.track(s.get('current') or '')
        if not cur:
            return
        generated = set(s.get('radio') or [])
        self.queue = [self._generated(t) if i in generated else t for i, u in enumerate(s.get('queue') or [])
                      if (t := self.store.track(u))]
        self.current = cur
        self._resume_at = self.position = int(s.get('pos') or 0)
        self.duration = cur.duration or 0
        self.track_changed.emit(cur)
        self.position_changed.emit(self._resume_at, cur.duration)
        self.queue_changed.emit()
