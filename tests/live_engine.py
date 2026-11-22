"""Live check (needs internet and sound, plays muted): search -> play through drift-core -> radio fills the queue ->
next -> the window goes away and the core keeps playing -> next song found by the --net helper. python tests/live_engine.py"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

os.environ.setdefault('DRIFT_DATA', tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from drift import core, online  # noqa: E402
from drift.engine import Engine  # noqa: E402
from drift.store import Store  # noqa: E402

T0 = time.time()


def log(*a):
    print(f'{time.time() - T0:6.1f}s', *a, flush=True)


app = QApplication(sys.argv)
e = Engine(Store())
e.start()
e.core.ready.connect(lambda: e.toggle_mute())
e.wave_ready.connect(lambda uid, w: log('shape', uid, len(w), 'bars' if w else '(measuring…)'))
steps = []


def later(ms, fn):
    QTimer.singleShot(ms, fn)


def begin():
    hit = online.search('ElGrandeToto Mghayer', 'ytmusic', 1)[0]
    log('found', hit.title, hit.artist)
    steps.append(time.time())
    e.play(hit)
    wait_playing('first song', after_first)


def wait_playing(what, then, tries=[0], uid=None):
    if e.playing and 0 < e.position < 8000 and (uid is None or (e.current and e.current.uid == uid)):
        log(what, 'playing at', e.position, 'ms', '| queue', len(e.queue))
        tries[0] = 0
        then()
    elif tries[0] > 150:
        log('FAIL', what, 'never played')
        app.exit(1)
    else:
        tries[0] += 1
        later(100, lambda: wait_playing(what, then, uid=uid))


def after_first():
    def go():
        nxt = e.queue[0]
        log('queue after radio', len(e.queue), [t.title for t in e.queue[:3]],
            '| next on disk already:', online.core_cache(nxt.uid).is_file())
        steps.append(time.time())
        e.next()
        wait_playing('after next', lambda: (log(f'next song started in {time.time() - steps[-1]:.1f}s'), alone()),
                     uid=nxt.uid)
    later(12000, go)


TARGET = []


def alone():
    TARGET.append(e.queue[1])          # 'next' twice while alone lands here
    log('window goes away; the core keeps the music')
    e.core.flush()
    e.core.sock.abort()
    app.quit()


later(500, begin)
code = app.exec()
if code:
    sys.exit(code)

port, token, pid = core.read_port()
s = socket.create_connection(('127.0.0.1', port))
f = s.makefile('rw', encoding='utf-8')


def send(**v):
    f.write(json.dumps(v) + '\n')
    f.flush()


send(token=token, cmd='state', role='remote')
time.sleep(2)
mem = subprocess.run(['powershell', '-NoProfile', '-Command',
                      f'$p=Get-Process -Id {pid}; "{{0:N1}} MB working set, {{1:N1}} MB private" -f ($p.WorkingSet64/1MB),($p.PrivateMemorySize64/1MB)'],
                     capture_output=True, text=True).stdout.strip()
log('core alone:', mem)
send(cmd='next')
time.sleep(4)
send(cmd='next')   # this one has no stream link yet: the core starts a --net helper for it
log('next twice while alone (the helper finds the second stream)...')
# watch the core's cache: the helper-resolved song lands there (named by its id, as the core names it) when it plays


def cached_name(uid):
    h = 0xcbf29ce484222325
    for b in uid.encode():
        h = ((h ^ b) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return f'{h:016x}.m4a'


want = core.DATA / 'stream' / cached_name(TARGET[0].uid)
t1 = time.time()
ok = False
while time.time() - t1 < 30:
    time.sleep(0.5)
    if want.is_file():
        ok = True
        break
log('helper next:', 'OK' if ok else 'FAIL', f'{time.time() - t1:.1f}s', TARGET[0].title)
send(cmd='quit')
s.close()
sys.exit(0 if ok else 1)
