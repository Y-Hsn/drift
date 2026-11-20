"""Taste pickup for Explore mixes and Drift radio."""
import json
import math
import random
import re
import time
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

from .store import clean_title, fold, norm

DAY = 86400
_NOT_A_SONG = re.compile(r'\b(full album|album complet|mix(tape)?\s*20\d\d|\d+\s*(hours?|hrs?|h)\b|playlist|compilation|'
                         r'best of|top \d+|non[- ]?stop|live stream|podcast|reaction|interview|karaoke|instrumental '
                         r'version|8d audio|slowed|sped up|nightcore|tutorial|shorts?)\b', re.I)


def song_score(t, now=None):
    now = now or time.time()
    fresh = math.exp(-(now - t.last_played) / (45 * DAY)) if t.last_played else 0.3
    return 4.0 * t.liked + 1.2 * math.log1p(t.plays) * (0.5 + fresh) - 1.6 * math.log1p(t.skips)


def artist_weights(tracks):
    w = {}
    now = time.time()
    for t in tracks:
        key = norm(t.artist)
        if key:
            w[key] = w.get(key, 0.0) + song_score(t, now)
    return w


def top_artists(tracks, n=8):
    """[(display name, weight)] of the artists you like most (by what you did, then by how many songs you have)."""
    w = artist_weights(tracks)
    names = {}
    count = {}
    for t in tracks:
        key = norm(t.artist)
        if key:
            names.setdefault(key, t.artist)
            count[key] = count.get(key, 0) + 1
    ranked = sorted(w, key=lambda k: (w[k] + 0.25 * count.get(k, 0)), reverse=True)
    return [(names[k], w[k]) for k in ranked if w[k] + 0.25 * count.get(k, 0) > 0][:n]


def pick_seed(tracks, avoid=()):
    """A song to start 'more like this' from: likes and played-through songs, weighted, never one you keep skipping."""
    pool = [t for t in tracks if t.uid not in avoid and t.skips <= max(2, t.plays)]
    if not pool:
        return None
    weights = [max(0.05, 1 + song_score(t)) for t in pool]
    return random.choices(pool, weights=weights, k=1)[0]


def accept(t, recent_keys, disliked_artists):
    """Is this radio suggestion a real song you have not heard just now, by an artist you do not skip?"""
    if t.duration and not (75_000 <= t.duration <= 9 * 60_000):
        return False
    if _NOT_A_SONG.search(t.title):
        return False
    if norm(t.title) in recent_keys or t.uid in recent_keys:
        return False
    return norm(t.artist) not in disliked_artists


def disliked(tracks):
    """Artists you skip far more than you play."""
    per = {}
    for t in tracks:
        k = norm(t.artist)
        if k:
            p, s = per.get(k, (0, 0))
            per[k] = (p + t.plays + 3 * t.liked, s + t.skips)
    return {k for k, (p, s) in per.items() if s >= 4 and s > 2 * p}


def offline_order(tracks, recent_keys=(), n=20):
    """Saved songs to play when there is no internet: weighted by taste, nothing heard just now, no artist twice in a
    row."""
    pool = [t for t in tracks if t.uid not in recent_keys and norm(t.title) not in recent_keys] or list(tracks)
    weights = {t.uid: max(0.1, 1.5 + song_score(t)) for t in pool}
    out, last = [], None
    while pool and len(out) < n:
        choices = [t for t in pool if norm(t.artist) != last] or pool
        pick = random.choices(choices, weights=[weights[t.uid] for t in choices], k=1)[0]
        out.append(pick)
        pool.remove(pick)
        last = norm(pick.artist)
    return out


def credit(track):
    # YouTube Music joins the primary and featured credits with a comma.
    return norm(fold(track.artist.split(', ', 1)[0]))


def recording(track):
    artist = credit(track)
    title = norm(fold(clean_title(track.title)))
    return (artist, title) if artist and title else ('', track.uid)


def listening(folder):
    """The core writes a bounded journal even when the window is closed."""
    try:
        rows = json.loads((Path(folder) / 'radio-listening.json').read_text(encoding='utf-8'))
        return [(r['uid'], r['at'], r['kind']) for r in rows
                if isinstance(r['uid'], str) and isinstance(r['at'], (float, int))
                and r['kind'] in ('listen', 'skip')]
    except (OSError, ValueError, TypeError, KeyError):
        return []


def early_skip(ms, duration):
    return 1500 <= ms <= 25_000 and (not duration or ms * 10 < duration * 3)


class RadioCatalog:
    """Reuse the provider's ranked neighborhoods when a station circles back to a seed."""

    def __init__(self, related):
        self.related = related
        self.cache = OrderedDict()

    def __call__(self, seed):
        now = time.monotonic()
        cached = self.cache.pop(seed.uid, None)
        if cached and cached[0] > now:
            self.cache[seed.uid] = cached
            return cached[1]
        try:
            tracks = self.related(seed, 25)
        except Exception:
            return []
        self.cache[seed.uid] = (now + (1200 if tracks else 45), tracks)
        if len(self.cache) > 12:
            self.cache.popitem(last=False)
        return tracks


class Radio:
    """Follow one musical direction at a time. Use a second seed only when the first points to it."""

    def __init__(self, known, events=(), current=None, queue=(), seed=None, now=None):
        self.now = time.time() if now is None else now
        self.known = {t.uid: t for t in known}
        self.versions = defaultdict(list)
        for t in known:
            self.versions[recording(t)].append(t)
        self.current, self.queue, self.seed = current, list(queue), seed
        self.excluded = {recording(t) for t in [current, seed, *queue] if t}
        self.excluded_ids = {t.uid for t in [current, seed, *queue] if t}
        self.last = {key: max(t.last_played for t in versions) for key, versions in self.versions.items()}
        self.latest = {}
        self.listens = Counter()
        self.skips = Counter()
        self.session = defaultdict(float)
        for uid, at, kind in sorted(events, key=lambda e: e[1]):
            t = self.known.get(uid)
            if t is None or not 0 <= self.now - at < 90 * DAY:
                continue
            key = recording(t)
            old = self.latest.get(key)
            if old and old[1] == kind and at - old[0] < 2:
                continue  # a just-sent window action can already be in the core's journal
            self.latest[key] = at, kind
            self.last[key] = max(at, self.last.get(key, 0))
            (self.listens if kind == 'listen' else self.skips)[key] += 1
            if self.now - at < 3600 and credit(t):
                self.session[credit(t)] += (1.0 if kind == 'listen' else -0.85) * math.exp((at - self.now) / 1400)
        self.affinity = defaultdict(float)
        self.quality = {}
        self.familiar = []
        self.favorites = set()
        for key, versions in self.versions.items():
            plays = max(self.listens[key], max(t.plays for t in versions))
            skips = max(self.skips[key], max(t.skips for t in versions))
            liked = any(t.liked for t in versions)
            quality = 2.2 * liked + 1.15 * math.log1p(plays) - 1.5 * math.log1p(skips)
            self.quality[key] = quality
            artist = key[0]
            if artist:
                self.affinity[artist] += max(-0.8, min(1.4, quality))
            if quality >= .7:
                playable = [t for t in versions if t.local or t.page]
                if playable:
                    best = max(playable, key=lambda t: (t.local, bool(t.liked), t.plays, bool(t.video_id), t.uid))
                    self.familiar.append(best)
            if plays >= 2 or liked:
                self.favorites.add(key)

    @classmethod
    def from_store(cls, store, current=None, queue=(), seed=None, immediate=()):
        events = listening(store.folder) + list(immediate)
        ids = tuple({uid for uid, _, _ in events})
        where = 'liked=1 or plays>0 or skips>0 or path is not null or last_played>0'
        if ids:
            where += ' or uid in (' + ','.join('?' for _ in ids) + ')'
        return cls(store.query(where, ids), events, current, queue, seed)

    def track_preference(self, t):
        return self.quality.get(recording(t), 0.0)

    def eligible(self, t):
        key = recording(t)
        if t.uid in self.excluded_ids or key in self.excluded or not t.title:
            return False
        at, kind = self.latest.get(key, (0, ''))
        return (self.now - self.last.get(key, 0) >= 75 * 60
                and (kind != 'skip' or self.now - at >= 8 * 3600)
                and self.track_preference(t) > -1.25)

    def anchor(self, saved):
        if self.seed:
            return self.seed
        current = self.current
        current_ok = False
        if current:
            at, kind = self.latest.get(recording(current), (0, ''))
            current_ok = (kind != 'skip' or self.now - at >= 10 * 60) and self.track_preference(current) > -1.25
            if current_ok and (not current.extra.get('radio') or (kind == 'listen' and self.now - at < 1200)):
                return current
        heard = [t for t in self.known.values() if t.uid not in self.excluded_ids and
                 self.latest.get(recording(t), (0, ''))[1] == 'listen'
                 and self.now - self.latest[recording(t)][0] < 3600]
        if heard:
            return max(heard, key=lambda t: self.latest[recording(t)][0])
        if current_ok:
            return current
        pool = [t for t in self.familiar if t.uid not in self.excluded_ids] or saved
        return max(pool, key=lambda t: (self.track_preference(t), self.last.get(recording(t), 0), t.uid),
                   default=current)

    def plan(self, catalog, saved, n=3):
        anchor = self.anchor(saved)
        if not anchor:
            return []
        first = catalog(anchor)
        vicinity = {credit(anchor): 1.0} if credit(anchor) else {}
        for i, t in enumerate(first):
            if credit(t):
                vicinity.setdefault(credit(t), 1 / (1 + i / 7))

        # Another favorite gets a vote only if the current sound already leads toward it.
        bridges = [t for t in self.familiar if recording(t) != recording(anchor)
                   and vicinity.get(credit(t), 0) >= 0.4 and self.track_preference(t) >= 1.15
                   and self.latest.get(recording(t), (0, ''))[1] != 'skip']
        bridge = max(bridges, key=lambda t: vicinity[credit(t)] + min(3, self.track_preference(t)) * .15
                     + max(-.4, min(.4, self.session[credit(t)] * .2)), default=None)
        if bridge is None and first:
            bridge = next((t for t in first[:10] if credit(t) and credit(t) != credit(anchor)
                           and self.eligible(t) and accept(t, (), ())), None)
        if bridge is None and not first:
            bridge = next((t for t in self.familiar if credit(anchor) and credit(t) == credit(anchor)
                           and recording(t) != recording(anchor)), None)
        second = catalog(bridge) if bridge else []

        candidates, scores = {}, {}
        for tracks, weight in ((first, 1.7), (second, .82)):
            seen = set()
            anchored = len(candidates) >= n
            for i, t in enumerate(tracks):
                key = recording(t)
                if key in seen or not self.eligible(t) or not accept(t, (), ()):
                    continue
                seen.add(key)
                candidates.setdefault(key, t)
                prior = scores.get(key, 0)
                outside = .55 if anchored and not prior and credit(t) not in vicinity else 1.0
                scores[key] = prior + outside * weight / (1 + i / 8) + (.38 if prior else 0)

        # A long-unheard favorite fits when its artist is genuinely in the current neighborhood.
        returns = {}
        for t in self.familiar:
            key = recording(t)
            fit = vicinity.get(credit(t), 0)
            if (key in self.favorites and self.eligible(t) and fit >= .45
                    and self.now - self.last.get(key, 0) >= 2 * DAY):
                returns[key] = t
                scores.setdefault(key, .45 + .7 * fit)
                candidates.setdefault(key, t)
        picks = self._sequence(candidates, scores, n, returns)
        local_versions = {recording(t): t for t in saved}
        return [local_versions.get(recording(t), t) for t in picks]

    def offline(self, saved, n=3):
        candidates = {}
        for t in saved:
            key = recording(t)
            candidates.setdefault(key, t)
        fresh = {k: t for k, t in candidates.items() if self.eligible(t)}
        if fresh:
            candidates = fresh
        elif candidates:
            choices = {k: t for k, t in candidates.items() if k not in self.excluded and t.uid not in self.excluded_ids}
            # A one-song library may loop, but never put a second copy of an already queued item after it.
            candidates = choices or ({} if self.queue else candidates)
        anchor = self.seed or self.current
        scores = {k: (1.0 if anchor and credit(t) and credit(t) == credit(anchor) else 0.0)
                  + min(1.0, (self.now - self.last.get(k, 0)) / (2 * DAY)) for k, t in candidates.items()}
        return self._sequence(candidates, scores, n)

    def _sequence(self, candidates, scores, n, returns=None):
        out = []
        before = ([self.current] if self.current else []) + self.queue
        artists = [credit(t) for t in before[-3:]]
        returns = returns or {}
        order = dict(candidates)
        rediscovered = False
        while order and len(out) < n:
            normal = {k: t for k, t in order.items() if k not in returns}
            rotation = {k: t for k, t in order.items() if k in returns and credit(t) not in artists[-2:]}
            rediscover = (len(out) + len(self.queue) == 2 and not rediscovered and bool(rotation))
            choices = rotation if rediscover else normal or ({} if rediscovered else order)
            if not choices:
                break

            def rank(key):
                t = choices[key]
                artist = credit(t)
                space = sum(p for a, p in zip(reversed(artists[-3:]), (1.30, .45, .19)) if artist and artist == a)
                personal = max(-.70, min(.55, .25 * self.track_preference(t)))
                direction = max(-.80, min(.50, .36 * self.session[artist]))
                affinity = .22 * math.tanh(self.affinity[artist] / 3)
                return scores[key] + personal + direction + affinity - space, key

            key = max(choices, key=rank)
            out.append(returns.get(key, order[key]))
            artists.append(credit(order.pop(key)))
            rediscovered |= key in returns
        return out
