"""Off-screen look at the window with a song 'playing' (no sound, no internet): the stage, the shoreline, the river,
the whisper line. python tests/preview.py OUTDIR [width height]"""
import os
import sys
import tempfile

out = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
W, H = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (1280, 900)
os.environ.setdefault('DRIFT_DATA', tempfile.mkdtemp())
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QPA_FONTDIR', 'C:/Windows/Fonts')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
import drift.app as A  # noqa: E402
if os.environ.get('SKETCH'):        # SKETCH=vinyl: look at another sketch
    import drift.ui.sketches as _sk
    _first = _sk.active

    def _once(store):          # the first window only: a swap later picks for itself
        _sk.active = _first
        return _sk.find(os.environ['SKETCH']) or _sk.BUILTIN[0]
    _sk.active = _once
from drift.store import Track  # noqa: E402

orig = QApplication.exec


def run():
    win = next(w for w in QApplication.topLevelWidgets() if w.__class__.__name__ == 'Window')
    win.resize(W, H)
    e = win.engine
    songs = [Track(uid=f'yt:demo{i}', title=t, artist=a, duration=d, source='youtube')
             for i, (t, a, d) in enumerate([('Harbour Lights', 'The Low Tides', 243000), ('Copper Sky', 'Mira Vale', 258000),
                                            ('Slow Ferry', 'North Window', 128000), ('Glasshouse', 'Elm & Ash', 634000),
                                            ('Long Weekend', 'Sora Field', 237000), ('Paper Moons', 'Juniper Lane', 301000),
                                            ('Undertow', 'The Quiet Hours', 289000), ('Late Bloom', 'Ada Reyes', 276000)])]
    from drift.ui import lyricsfeed
    lines = [(0, ''), (20000, 'Lights along the water'), (24000, 'Drifting where the evening goes'),
             (30000, 'The harbour hums below'), (36000, 'And every boat is going home')]

    def fake_cover(uid, a, b, dot):
        """A made-up cover (two-tone gradient and a disc) so colours can be judged without the internet."""
        from PyQt6.QtGui import QImage, QPainter, QLinearGradient, QColor
        from drift.ui.covers import shared
        img = QImage(320, 320, QImage.Format.Format_RGB32)
        q = QPainter(img)
        g = QLinearGradient(0, 0, 320, 320)
        g.setColorAt(0, QColor(a))
        g.setColorAt(1, QColor(b))
        q.fillRect(img.rect(), g)
        q.setBrush(QColor(dot))
        q.setPen(QColor(dot))
        q.drawEllipse(70, 120, 150, 150)
        q.end()
        shared().images[uid] = img

    def play():
        palette = os.environ.get('COVER', 'warm')
        if palette == 'warm':
            fake_cover(songs[0].uid, '#ff7a3d', '#5a1e6e', '#ffd166')
        elif palette == 'cold':
            fake_cover(songs[0].uid, '#0f4c5c', '#1b998b', '#e0f2e9')
        e.core.message.disconnect()      # the scratch core plays nothing: keep the pretend position
        e.current = songs[0]
        e.queue = songs[1:]
        e.drift = True
        e.playing = True
        e.duration = songs[0].duration
        lyricsfeed.shared().cache[songs[0].uid] = lines
        import random
        r = random.Random(3)
        env = [0.3] * 14 + [0.6] * 26 + [0.95] * 20 + [0.55] * 24 + [1.0] * 22 + [0.35] * 14 + [1.0] * 26 + [0.2] * 14
        e.wave = (songs[0].uid, [int(255 * min(1.0, v * (0.85 + 0.15 * r.random()))) for v in env])
        e.track_changed.emit(songs[0])
        e.queue_changed.emit()
        e.wave_ready.emit(*e.wave)
        e.position = 98000
        e.position_changed.emit(98000, songs[0].duration)
        e.playing_changed.emit(True)
        win.go('home')

    def lib():
        win.store.upsert(songs[0])
        for t in songs[1:]:
            win.store.upsert(t)
        win.go('library')

    from drift.ui import window as WIN

    def offline():
        e._set_online(False)
        win.go('explore')

    def swap():
        nxt = os.environ.get('SWAP')
        if nxt:
            WIN.live().swap(nxt)

    steps = [('now', play), ('explore', lambda: win.go('explore')), ('queue', lambda: win.go('queue')), ('library', lib),
             ('lyrics', lambda: win.go('lyrics')), ('calm', lambda: win.go('calm')), ('now2', lambda: win.go('home')),
             ('settings', lambda: win.go('settings')),
             ('settings-end', lambda: win.settings_page.verticalScrollBar().setValue(99999)), ('liked', lambda: win.go('liked')),
             ('offline', offline), ('swapped', swap)]

    def step(i=0):
        if i < len(steps):
            name, fn = steps[i]
            fn()
            QTimer.singleShot(1200, lambda: (WIN.live().grab().save(os.path.join(out, f'{i}-{name}.png')), step(i + 1)))
        else:
            WIN.live().quit()
    QTimer.singleShot(1200, step)
    return orig()


QApplication.exec = staticmethod(run)
print('exit', A.main(['drift']))
