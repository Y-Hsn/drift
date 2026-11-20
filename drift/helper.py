"""Short-lived helper the core starts while the window is closed (`Drift.exe --net <op> ...`): find a song's stream
link, bring more radio songs, or save a song for offline. No window, no Qt; it answers the core and exits."""
import json
import socket
import sys

from . import online, taste
from .store import Store, Track


def item_of(t, store=None, resolve=False, radio=False):
    item = {'id': t.uid, 'src': t.path if t.local else None, 'headers': {}, 'page': t.page, 'title': t.title,
            'artist': t.artist, 'duration': t.duration or 0, 'save': bool(store.get('save_played', False)) if store else False,
            'thumb': t.thumb or None, 'radio': radio}
    if resolve and not t.local and t.page:
        try:
            item['src'], item['headers'] = online.stream(t)
        except Exception:
            pass
    return item


def track_of(item):
    return Track(uid=item.get('id') or '', title=item.get('title') or '', artist=item.get('artist') or '',
                 duration=int(item.get('duration') or 0), page=item.get('page'),
                 source='file' if not item.get('page') else 'youtube', extra={'radio': bool(item.get('radio'))})


def resolve(item):
    t = track_of(item)
    src, headers = online.stream(t)
    return {'src': src, 'headers': headers, 'duration': t.duration}


def refill(item, upcoming=()):
    store = Store()
    current = (store.track(item['id']) or track_of(item)) if item.get('id') else None
    if current:
        current.extra['radio'] = bool(item.get('radio'))
    radio = taste.Radio.from_store(store, current, [track_of(t) for t in upcoming])
    saved = store.local_tracks()
    catalog = taste.RadioCatalog(lambda t, n: online.related(t, n, match=taste.recording))
    picks = radio.plan(catalog, saved) if not online.blocked() and online.is_online() else []
    repeat_current = False
    if not picks:
        picks = radio.offline(saved)
        repeat_current = bool(current and len(picks) == 1 and picks[0].uid == current.uid)
    picks = [store.upsert(t) for t in picks]
    return {'items': [item_of(t, store, resolve=i == 0, radio=True) for i, t in enumerate(picks)],
            'repeat_current': repeat_current}


def save(item):
    store = Store()
    t = store.track(item.get('id') or '') or track_of(item)
    if t.local or not t.page:
        return {}
    t.path = online.download(t, store.get('music_folder'))
    store.upsert(t)
    store.set_field(t.uid, path=t.path)
    return {}


OPS = {'resolve': resolve, 'refill': refill, 'save': save}


def main(argv):
    args = dict(zip(argv[::2], argv[1::2]))
    op, item = args.get('--net'), json.loads(args.get('--item') or '{}')
    try:
        answer = refill(item, json.loads(args.get('--upcoming') or '[]')) if op == 'refill' else OPS[op](item)
    except Exception as e:
        answer = {'error': str(e)[:200] or 'failed'}
    req = int(args.get('--req') or 0)
    if not req:
        return 0
    msg = {'token': args.get('--token'), 'cmd': 'reply', 'req': req, 'id': item.get('id'), **answer}
    try:
        with socket.create_connection(('127.0.0.1', int(args.get('--port') or 0)), timeout=5) as s:
            s.sendall((json.dumps(msg) + '\n').encode())
    except OSError:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
