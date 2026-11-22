"""A stand-in for the PC's door (docs/phone-sync.md), to test the phone app without Drift:
python tests/phone_door_mock.py SONG_DIR [PORT]   -> door on 127.0.0.1:PORT (default 8790), app on PORT+1.
Prints an invite link. Checks signatures exactly like the real door must. Test data only; never touches drift.db."""
import base64
import hashlib
import hmac
import http.server
import json
import secrets
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SONGS = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8790
APP = Path(__file__).resolve().parent.parent / 'phone'
ORIGIN = f'http://127.0.0.1:{PORT + 1}'

invites, tickets, devices, seen = {}, {}, {}, set()
events = []
LINK = secrets.token_bytes(32)


def b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip('=')


def unb64(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def catalogue():
    out = []
    for i, f in enumerate(sorted(SONGS.glob('*.m4a'))):
        data = f.read_bytes()
        out.append({'id': f.stem, 'title': f.stem.replace('song', 'Song '), 'artist': ['Lake', 'Pines', 'Dunes'][i % 3],
                    'album': 'Test shelf', 'secs': 30, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                    'type': 'audio/mp4', 'cover': False, 'score': 1 - i / 20, 'why': 'Drift radio'})
    return out


class Door(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def cors(self):
        self.send_header('Access-Control-Allow-Origin', ORIGIN)
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Drift-Device, X-Drift-Time, X-Drift-Sign')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST')
        self.send_header('Access-Control-Max-Age', '600')

    def answer(self, code, body=b'', kind='application/json'):
        body = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        self.send_response(code)
        self.cors()
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def body(self):
        return self.rfile.read(int(self.headers.get('Content-Length') or 0))

    def signed(self, raw):
        dev = devices.get(self.headers.get('X-Drift-Device') or '')
        t, sig = self.headers.get('X-Drift-Time') or '', self.headers.get('X-Drift-Sign') or ''
        if not dev or not t.isdigit() or abs(time.time() * 1000 - int(t)) > 120_000 or sig in seen:
            return False
        msg = '|'.join([self.command, self.path, t, hashlib.sha256(raw).hexdigest()]).encode()
        ok = hmac.compare_digest(hmac.new(dev['key'], msg, 'sha256').hexdigest(), sig)
        if ok:
            seen.add(sig)
        return ok

    def do_POST(self):
        raw = self.body()
        path = urlparse(self.path).path
        if path == '/pair':
            req = json.loads(raw)
            if invites.pop(req.get('invite'), 0) < time.time():
                return self.answer(403, b'This code has expired. Show a new one on your PC.', 'text/plain')
            t = secrets.token_urlsafe(18)
            tickets[t] = time.time() + 86400
            return self.answer(200, {'ticket': t, 'pc': 'mock-pc', 'pcName': 'Test PC'})
        if path == '/pair/finish':
            req = json.loads(raw)
            if tickets.pop(req.get('ticket'), 0) < time.time():
                return self.answer(403, b'Pairing ticket expired', 'text/plain')
            dev = 'd' + secrets.token_hex(6)
            devices[dev] = {'key': unb64(req['key']), 'name': req.get('name', 'Phone')}
            return self.answer(200, {'device': dev, 'link': b64(LINK), 'pcName': 'Test PC'})
        if not self.signed(raw):
            return self.answer(401, b'not paired', 'text/plain')
        if path == '/events':
            req = json.loads(raw)
            events.extend(req['events'])
            print('events:', [(e['seq'], e['kind']) for e in req['events']], flush=True)
            return self.answer(200, {'ack': max(e['seq'] for e in req['events'])})
        self.answer(404, b'')

    def do_GET(self):
        u = urlparse(self.path)
        if not self.signed(b''):
            return self.answer(401, b'not paired', 'text/plain')
        if u.path == '/hello':
            return self.answer(200, {'pc': 'mock-pc', 'name': 'Test PC', 'version': 'mock'})
        if u.path == '/shelf':
            cat = catalogue()
            rev = hashlib.sha256(json.dumps(cat).encode()).hexdigest()[:12]
            if parse_qs(u.query).get('have', [''])[0] == rev:
                return self.answer(200, {'same': True, 'rev': rev})
            pls = [{'id': 'p1', 'name': 'Morning', 'items': [{'item': f'i{k}', 'song': s['id']} for k, s in enumerate(cat[:4])]}]
            return self.answer(200, {'rev': rev, 'songs': cat, 'playlists': pls,
                                     'taste': {'artists': {'lake': 0.4, 'pines': 0.1}, 'songs': {}}})
        if u.path.startswith('/song/'):
            f = SONGS / (u.path[6:] + '.m4a')
            return self.answer(200, f.read_bytes(), 'audio/mp4') if f.is_file() else self.answer(404)
        self.answer(404, b'')


class App(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(APP), **k)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()


def new_invite():
    i = secrets.token_urlsafe(12)
    invites[i] = time.time() + 300
    return f'{ORIGIN}/#i={i}&u={b64(f"http://127.0.0.1:{PORT}".encode())}&t='


if __name__ == '__main__':
    threading.Thread(target=http.server.ThreadingHTTPServer(('127.0.0.1', PORT + 1), App).serve_forever, daemon=True).start()
    print('invite:', new_invite(), flush=True)
    http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Door).serve_forever()
