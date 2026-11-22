"""The real phone link end to end, on this PC (no tunnel): a scratch data folder with a few saved songs, a liked song
and a playlist; drift-core on it (its door); the phone app in headless Edge pairs from a Settings-style invite, fills
its shelf from the core, plays, sends its plays back, and they land in drift.db. Then unpairing ends its access.
python tests/phone_door_e2e.py SONG_DIR OUTDIR        (cargo build --release in core/ first)"""
import asyncio
import functools
import http.server
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SONGS, OUT = Path(sys.argv[1]), Path(sys.argv[2])
DATA = Path(tempfile.mkdtemp(prefix='drift-door-'))
APP_PORT, DOOR_PORT = 8793, 8794
os.environ['DRIFT_DATA'] = str(DATA)
sys.path.insert(0, str(ROOT))
from drift.store import Store, Track  # noqa: E402

from playwright.async_api import async_playwright  # noqa: E402

T0 = time.time()


def log(*a):
    print(f'{time.time() - T0:6.1f}s', *a, flush=True)


def library():
    music = DATA / 'music'
    music.mkdir()
    s = Store()
    tracks = []
    for i, f in enumerate(sorted(SONGS.glob('*.m4a'))[:8]):
        dst = music / f'Artist {i % 3} - {f.stem}.m4a'
        shutil.copyfile(f, dst)
        tracks.append(s.upsert(Track(f'file:{f.stem}', f.stem.replace('song', 'Song '), f'Artist {i % 3}',
                                     path=str(dst), duration=30000, liked=int(i < 2))))
    big = music / 'Artist 9 - Long one.m4a'           # > 4 MB: arrives in pieces
    big.write_bytes(next(SONGS.glob('*.m4a')).read_bytes() + os.urandom(9 * 1024 * 1024))
    tracks.append(s.upsert(Track('file:long', 'Long one', 'Artist 9', path=str(big), duration=30000, liked=1)))
    pid = s.new_playlist('Morning')
    s.add_to_playlist(pid, tracks[2:5])
    return tracks


class App(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()


class Core:
    def __init__(self):
        env = dict(os.environ, DRIFT_PHONE_APP=f'http://127.0.0.1:{APP_PORT}/', DRIFT_NO_NTFY='1')
        if os.environ.get('LINK') != 'real':          # LINK=real: through a real Cloudflare quick tunnel
            env['DRIFT_LINK_URL'] = f'http://127.0.0.1:{DOOR_PORT}'
        if os.environ.get('CLOUDFLARED'):              # a copy already fetched (saves the 55 MB download)
            (DATA / 'phone').mkdir(exist_ok=True)
            shutil.copyfile(os.environ['CLOUDFLARED'], DATA / 'phone' / 'cloudflared.exe')
        (DATA / 'phone').mkdir(exist_ok=True)
        (DATA / 'phone' / 'door.json').write_text(json.dumps({'pc': '', 'name': '', 'topic': '', 'link': '', 'port': DOOR_PORT}))
        self.p = subprocess.Popen([str(ROOT / 'core' / 'target' / 'release' / 'drift-core.exe')], cwd=str(ROOT), env=env,
                                  creationflags=0x0800_0000 | 0x0000_4000)
        for _ in range(100):
            try:
                port, self.token, _pid = (DATA / 'core').read_text().split()
                break
            except (OSError, ValueError):
                time.sleep(0.1)
        self.s = socket.create_connection(('127.0.0.1', int(port)), timeout=5)
        self.f = self.s.makefile('r', encoding='utf-8')
        self.send(cmd='hello', role='ui', helper=[sys.executable, '-m', 'drift'], ui=[])
        self.send(cmd='mute', on=True)

    def send(self, **v):
        self.s.sendall((json.dumps({'token': self.token, **v}) + '\n').encode())

    def wait(self, ev, timeout=30, ok=lambda m: True):
        self.s.settimeout(timeout)
        end = time.time() + timeout
        while time.time() < end:
            m = json.loads(self.f.readline() or 'null') or {}
            if m.get('ev') == ev and ok(m):
                return m
        raise TimeoutError(ev)

    def quit(self):
        try:
            self.send(cmd='quit')
        except OSError:
            pass
        try:
            self.p.wait(5)
        except subprocess.TimeoutExpired:
            self.p.kill()


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tracks = library()
    web = http.server.ThreadingHTTPServer(('127.0.0.1', APP_PORT), functools.partial(App, directory=str(ROOT / 'phone')))
    threading.Thread(target=web.serve_forever, daemon=True).start()
    core = Core()
    problems = []
    try:
        core.send(cmd='phone_invite')
        inv = core.wait('phone_invite', 300, ok=lambda m: 'url' in m or 'error' in m)
        assert 'url' in inv, inv
        log('invite:', inv['url'][:90])
        if os.environ.get('LINK') == 'real':
            time.sleep(8)                              # a new quick tunnel takes a few seconds to answer
        async with async_playwright() as p:
            b = await p.chromium.launch(channel='msedge', args=['--autoplay-policy=no-user-gesture-required'])
            ctx = await b.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
            pg = await ctx.new_page()
            pg.on('console', lambda m: m.type == 'error' and problems.append('console: ' + m.text))
            pg.on('pageerror', lambda e: problems.append('page error: ' + str(e)))
            await pg.goto(inv['url'])
            await pg.wait_for_selector('#main:not([hidden])', timeout=20000)
            phones = core.wait('phones', ok=lambda m: m['list'])
            log('paired:', [(x['device'], x['name']) for x in phones['list']])
            t = time.time()
            await pg.wait_for_function("document.getElementById('sync-line').textContent.includes('new songs')", timeout=120000)
            line = await pg.text_content('#sync-line')
            log(f'shelf filled in {time.time() - t:.1f} s:', line)
            assert line.startswith('9 new songs'), line
            await pg.click('#play')
            await pg.wait_for_timeout(2500)
            title = await pg.text_content('#now-title')
            assert title != 'Nothing playing', 'nothing played'
            log('playing:', title)
            await pg.screenshot(path=str(OUT / 'door-now.png'))
            await pg.wait_for_timeout(31000)                 # 30 s = a play
            await pg.click('#next')
            await pg.click('.tabs button[data-page="shelf"]')
            await pg.click('#shelf-chips [data-shelf="playlists"]')
            await pg.wait_for_timeout(500)
            await pg.screenshot(path=str(OUT / 'door-playlists.png'))
            pls = await pg.eval_on_selector_all('#shelf li b', 'els => els.map(e => e.textContent)')
            assert 'Morning' in pls, pls
            await pg.click('#open-settings')
            await pg.click('#sync-now')                      # sends the play and the skip
            await pg.wait_for_timeout(4000)
            await b.close()
        db = sqlite3.connect(str(DATA / 'drift.db'))
        for _ in range(60):
            plays = db.execute('select sum(plays), sum(skips) from tracks').fetchone()
            if plays[0]:
                break
            time.sleep(0.5)
        log('drift.db plays, skips:', plays)
        assert plays[0] >= 1, 'the phone play did not reach drift.db'
        dev = phones['list'][0]['device']
        core.send(cmd='phone_unpair', device=dev)
        left = core.wait('phones')
        assert not left['list'], left
        log('unpaired; phone folder gone:', not (DATA / 'phone' / dev).exists())
    finally:
        core.quit()
        web.shutdown()
    if problems:
        print('PROBLEMS:\n' + '\n'.join(problems))
        sys.exit(1)
    print('door e2e OK')


asyncio.run(main())
