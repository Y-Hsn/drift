"""Name the song this PC is playing. The core listens (loopback of the speakers, never the microphone) and makes a
Shazam-format fingerprint (core/src/hear.rs); this asks Shazam's song search what it is, then finds the full song
for Drift to play. Only the fingerprint is sent, not the sound."""
import json
import random
import time
import urllib.error
import urllib.request
import uuid

from . import links, online
from .store import Track

URL = ('https://amp.shazam.com/discovery/v5/en/US/android/-/tag/{a}/{b}?sync=true&webv3=true&sampling=true'
       '&connected=&shazamapiversion=v3&sharehub=true&video=v3')
AGENTS = ('Dalvik/2.1.0 (Linux; U; Android 5.0.2; VS980 4G Build/LRX22G)',
          'Dalvik/2.1.0 (Linux; U; Android 5.1.1; SM-P905V Build/LMY47X)',
          'Dalvik/2.1.0 (Linux; U; Android 6.0.1; SM-G920F Build/MMB29K)')


class Busy(Exception):
    """Shazam asked to wait (too many tries from this connection)."""


def recognize(uri, ms, at=None):
    """Ask Shazam about one fingerprint: {'title', 'artist', 'album', 'year', 'cover'} or None (no match)."""
    at = at or int(time.time() * 1000)
    body = json.dumps({'geolocation': {'altitude': 300, 'latitude': 45, 'longitude': 2},
                       'signature': {'samplems': int(ms), 'timestamp': at, 'uri': uri},
                       'timestamp': at, 'timezone': 'Europe/Paris'}).encode()
    req = urllib.request.Request(URL.format(a=str(uuid.uuid4()).upper(), b=uuid.uuid4()), data=body, method='POST',
                                 headers={'Content-Type': 'application/json', 'Content-Language': 'en_US',
                                          'User-Agent': random.choice(AGENTS)})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            answer = json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise Busy('Too many tries. Wait a minute and try again.') from e
        raise
    return about(answer)


def about(answer):
    track = (answer or {}).get('track') if (answer or {}).get('matches') else None
    if not track or not track.get('title'):
        return None
    meta = {}
    for section in track.get('sections') or ():
        for row in section.get('metadata') or ():
            meta[(row.get('title') or '').lower()] = row.get('text') or ''
    images = track.get('images') or {}
    best = answer['matches'][0] or {}
    return {'title': track['title'], 'artist': track.get('subtitle') or '', 'album': meta.get('album', ''),
            'year': meta.get('released', ''), 'cover': images.get('coverarthq') or images.get('coverart') or '',
            'key': str(track.get('key') or ''), 'offset': float(best.get('offset') or 0),
            'timeskew': float(best.get('timeskew') or 0), 'freqskew': float(best.get('frequencyskew') or 0),
            'matches': len(answer['matches'])}


AGREE_WITHIN = 1.5      # seconds: two listens of one song point at the same place in it


def same_key(a, b):
    """Both answers name one song."""
    return bool(a and b) and (bool(a['key']) and a['key'] == b['key'] or (
        a['title'].casefold(), a['artist'].casefold()) == (b['title'].casefold(), b['artist'].casefold()))


def same_song(a, b):
    """Two answers name the same song AND the same moment in it: a real match. When Shazam doesn't know a song it
    can answer a similar-sounding one, but then the listens rarely agree on the song and where in it they are.
    `from` = where each listen started (seconds after listening began). A chorus heard twice can point at an
    earlier repeat, so a real song can fail this once: sure() also counts how often a song comes back."""
    return same_key(a, b) and abs((a['offset'] - a['from']) - (b['offset'] - b['from'])) <= AGREE_WITHIN


def sure(seen, found, final=False):
    """Name `found` now? Yes when it agrees with an earlier listen, or the song came back 3 times (2 at the end)."""
    times = 1 + sum(same_key(s, found) for s in seen)
    return any(same_song(s, found) for s in seen) or times >= (2 if final else 3)


LOG_LINES = 200


def note(ev, found, verdict, log=None):
    """One line per listen in DATA/hear.log: what Shazam answered and what Drift made of it."""
    from .store import DATA
    log = log or DATA / 'hear.log'
    what = (f"{found['artist']} - {found['title']} key={found['key']} offset={found['offset']:.1f} "
            f"timeskew={found['timeskew']:.5f} freqskew={found['freqskew']:.5f} matches={found['matches']}"
            if found else 'no match')
    line = '\t'.join([time.strftime('%Y-%m-%d %H:%M:%S'), f"try {ev.get('try')}", f"{ev.get('ms')} ms",
                      f"from {ev.get('from_ms', 0)} ms", what, verdict])
    try:
        old = log.read_text(encoding='utf-8').splitlines()[-(LOG_LINES - 1):] if log.is_file() else []
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text('\n'.join(old + [line]) + '\n', encoding='utf-8')
    except OSError:
        pass


def full_song(heard):
    """The heard song as a Drift track (YouTube Music): title + artist match, else a result with the same title."""
    item = {'__typename': 'Track', 'name': heard['title'],
            'artists': {'items': [{'profile': {'name': name.strip()}}
                                  for name in heard['artist'].replace(' & ', ', ').replace(' feat. ', ', ').split(', ')
                                  if name.strip()]}}
    track = links._match(item, prepare=True) if item['artists']['items'] else None
    if track:
        return track
    want = links._key(heard['title'])
    found = online.search(f"{heard['artist']} {heard['title']}", 'ytmusic', 5)
    return next((t for t in found if links._key(t.title) == want), None)   # never a different song


def sample_track(heard):
    """Something to show while the full song is being found."""
    return Track(uid='heard:' + heard['artist'] + '/' + heard['title'], title=heard['title'], artist=heard['artist'],
                 album=heard.get('album') or '', thumb=heard.get('cover') or None, source='web')
