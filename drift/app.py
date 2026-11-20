"""Start Drift: one window only (a second start brings the first to the front); the music core starts with it."""
import ctypes
import json
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication

APP_ID = 'Drift.MusicPlayer'
PIPE = 'drift-music-player-' + os.environ.get('USERNAME', 'user')
if os.environ.get('DRIFT_DATA'):        # a test copy with its own data never meets the real Drift
    import zlib
    PIPE += '-%08x' % zlib.crc32(os.environ['DRIFT_DATA'].encode())
# the old player kept its folder in a config.json beside the source
OLD_CONFIGS = [Path(__file__).resolve().parent.parent / 'config.json']


def app_icon():
    """The Drift mark: a sound wave drifting out of a dot, on the room's colour."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        px = QPixmap(size, size)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor('#16161b'))
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.24, size * 0.24)
        from .ui import theme
        mark = theme.pixmap('radio', '#7c9cff', int(size * 0.72), 2.4, ratio=1.0)
        p.drawPixmap(int(size * 0.14), int(size * 0.14), mark)
        p.end()
        icon.addPixmap(px)
    return icon


def first_folder(store):
    """Keep the old player's songs: its folder becomes the library the first time Drift starts."""
    if store.get('folder_set'):
        return
    for cfg in OLD_CONFIGS:
        try:
            old = json.loads(cfg.read_text(encoding='utf-8')).get('music_folder')
        except (OSError, ValueError):
            continue
        if old and Path(old).is_dir():
            store.set('music_folder', old)
            break
    store.set('folder_set', True)


def keep_going(kind, value, tb):
    """An error in one click or one picture never closes Drift (PyQt would abort): it is written down instead."""
    import traceback
    try:
        from .store import DATA
        with open(DATA / 'errors.log', 'a', encoding='utf-8') as f:
            f.write(''.join(traceback.format_exception(kind, value, tb)) + chr(10))
    except OSError:
        pass
    sys.__excepthook__(kind, value, tb)


def main(argv=None):
    argv = argv if argv is not None else sys.argv
    sys.excepthook = keep_going
    if sys.platform == 'win32':
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    app = QApplication(argv)
    app.setApplicationName('Drift')
    app.setQuitOnLastWindowClosed(False)
    # one Drift at a time: a second start shows the first
    probe = QLocalSocket()
    probe.connectToServer(PIPE)
    if probe.waitForConnected(300):
        probe.write(b'show')
        probe.flush()
        probe.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(PIPE)
    server = QLocalServer()
    server.listen(PIPE)
    app.setWindowIcon(app_icon())
    from .ui import theme
    app.setFont(theme.font(10))

    from .store import Store
    from .engine import Engine
    from .ui import window as windows
    from .ui.window import Window
    store = Store()
    first_folder(store)
    engine = Engine(store)
    win = Window(store, engine)

    def incoming():
        sock = server.nextPendingConnection()
        if sock is not None:
            sock.waitForReadyRead(300)
            cmd = bytes(sock.readAll()).decode(errors='ignore')
            w = windows.live()          # a sketch change replaces the window
            if cmd == 'quit':
                w.quit()
            else:
                w.bring_up()

    server.newConnection.connect(incoming)
    win.place()
    win.show()
    engine.restore_session()
    engine.start()
    QTimer.singleShot(300, lambda: windows.live().rescan(quiet=True))
    rc = app.exec()
    win = windows.live()
    # Leave in order: the window and its parts go while Qt is still here (Python tearing them down after the app
    # was gone crashed every exit with 0xC0000409, waking Windows' error reporting).
    # Let go of the tray and of "one Drift at a time" first: a reopen while slow work finishes starts a fresh window.
    win.hide()
    server.close()
    engine.core.leave()
    from . import tasks
    tasks.shutdown()
    win.deleteLater()
    engine.deleteLater()
    from PyQt6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    store.db.close()
    for stream in (sys.stdout, sys.stderr):    # the installed app has no console: both are None there
        try:
            stream and stream.flush()
        except (OSError, ValueError):
            pass
    os._exit(rc)


if __name__ == '__main__':
    sys.exit(main())
