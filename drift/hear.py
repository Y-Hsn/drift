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
    return {'title': track['title'], 'artist': track.get('subtitle') or '', 'album': meta.get('album', ''),
            'year': meta.get('released', ''), 'cover': images.get('coverarthq') or images.get('coverart') or ''}


def full_song(heard):
    """The heard song as a Drift track (YouTube Music first): an exact title+artist match, else the top result."""
    item = {'__typename': 'Track', 'name': heard['title'],
            'artists': {'items': [{'profile': {'name': name.strip()}}
                                  for name in heard['artist'].replace(' & ', ', ').replace(' feat. ', ', ').split(', ')
                                  if name.strip()]}}
    track = links._match(item, prepare=True) if item['artists']['items'] else None
    if track:
        return track
    found = online.search(f"{heard['artist']} {heard['title']}", 'ytmusic', 3)
    return found[0] if found else None


def sample_track(heard):
    """Something to show while the full song is being found."""
    return Track(uid='heard:' + heard['artist'] + '/' + heard['title'], title=heard['title'], artist=heard['artist'],
                 album=heard.get('album') or '', thumb=heard.get('cover') or None, source='web')
