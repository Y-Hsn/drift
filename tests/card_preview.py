"""The player card as a picture, without anything popping up: starts a scratch music core (own data folder, silent,
paused), gives it a song and a made-up cover, asks it to draw the card into PNGs, quits it.
python tests/card_preview.py OUTDIR [song file]"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
out = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp())
song = sys.argv[2] if len(sys.argv) > 2 else next(iter(sorted((ROOT / '.music').glob('*.m4a'))), None)
data = Path(tempfile.mkdtemp())
env = dict(os.environ, DRIFT_DATA=str(data))
core = subprocess.Popen([str(ROOT / 'core' / 'target' / 'release' / 'drift-core.exe')], env=env)


def fnv(s):
    h = 0x811c9dc5
    for b in s.encode():
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def fake_cover(path):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    img = QImage(320, 320, QImage.Format.Format_RGB32)
    q = QPainter(img)
    g = QLinearGradient(0, 0, 320, 320)
    g.setColorAt(0, QColor('#ff7a3d'))
    g.setColorAt(1, QColor('#5a1e6e'))
    q.fillRect(img.rect(), g)
    q.setBrush(QColor('#ffd166'))
    q.drawEllipse(70, 120, 150, 150)
    q.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path))


try:
    for _ in range(50):
        if (data / 'core').is_file():
            break
        time.sleep(0.1)
    port, token, _pid = (data / 'core').read_text().split()
    s = socket.create_connection(('127.0.0.1', int(port)))
    f = s.makefile('rw', encoding='utf-8')

    def send(**v):
        v['token'] = token
        f.write(json.dumps(v) + '\n')
        f.flush()

    def wait(ev, secs=8, full=False):
        end = time.time() + secs
        s.settimeout(1)
        while time.time() < end:
            try:
                line = f.readline()
            except (socket.timeout, OSError):
                continue
            v = json.loads(line) if line else {}
            if v.get('ev') == ev and (not full or v.get('wave')):
                return v
        return None

    send(cmd='hello', role='ui', helper=[], ui=[])
    send(cmd='volume', v=0.0)
    send(cmd='card_png', path=str(out / 'card-empty.png'))
    print('empty', wait('card_png'))
    if song:
        item = {'id': 'test:card', 'src': str(song), 'title': 'Harbour Lights', 'artist': 'The Low Tides', 'duration': 0}
        send(cmd='load', item=item, paused=True, start=95000)
        print('wave', bool(wait('wave', 20, full=True)))
        send(cmd='card_png', path=str(out / 'card-nocover.png'))
        print('nocover', wait('card_png'))
        fake_cover(data / 'art' / f'{fnv("test:card"):08x}.png')
        send(cmd='card_png', path=str(out / 'card.png'))
        print('cover', wait('card_png'))
    send(cmd='quit')
finally:
    time.sleep(0.5)
    core.kill() if core.poll() is None else None
print('out', out)
