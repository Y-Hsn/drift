"""The link to drift-core, the tiny native player that makes the sound (and keeps playing when this window is closed).
Line-delimited JSON over 127.0.0.1; the core writes its port and token to %LOCALAPPDATA%/Drift/core."""
import json
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtNetwork import QAbstractSocket, QTcpSocket

from .store import DATA

ROOT = Path(__file__).resolve().parent.parent


def core_exe():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).with_name('drift-core.exe')
    return ROOT / 'core' / 'target' / 'release' / 'drift-core.exe'


def self_cmd():
    """How the core starts this app again (the window, or a short helper with --net)."""
    if getattr(sys, 'frozen', False):
        return [sys.executable]
    exe = Path(sys.executable)
    quiet = exe.with_name('pythonw.exe')
    return [str(quiet if quiet.is_file() else exe), '-m', 'drift']


def read_port():
    try:
        port, token, pid = (DATA / 'core').read_text().split()
        return int(port), token, int(pid)
    except (OSError, ValueError):
        return None


def launch():
    exe = core_exe()
    if not exe.is_file():
        return False
    flags = 0x0000_0008 | 0x0000_0200 | 0x0000_4000   # detached, own group, below-normal priority
    for extra in (0x0100_0000, 0):                     # break away from a job when allowed (it must outlive us)
        try:
            subprocess.Popen([str(exe)], cwd=str(ROOT), creationflags=flags | extra, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            continue
    return False


class Core(QObject):
    message = pyqtSignal(dict)  # one event from the core (never name it `event`: that is Qt's own)
    ready = pyqtSignal()        # connected (and said hello)
    lost = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sock = QTcpSocket(self)
        self.sock.connected.connect(self._hello)
        self.sock.readyRead.connect(self._read)
        self.sock.disconnected.connect(self._gone)
        self.sock.errorOccurred.connect(self._failed)
        self.buf = b''
        self.waiting = []
        self.token = None
        self.tries = 0
        self.launched = False
        self.is_ready = False

    def start(self):
        info = read_port()
        if not info:
            self._spawn()
            return
        port, self.token, _pid = info
        self.sock.abort()
        self.sock.connectToHost('127.0.0.1', port)

    def _spawn(self):
        if not self.launched:
            self.launched = launch()
        self.tries += 1
        if self.tries > 60:
            self.message.emit({'ev': 'error', 'msg': 'The music core would not start.'})
            return
        QTimer.singleShot(100, self.start)

    def _failed(self, _err):
        if not self.is_ready:
            try:
                (DATA / 'core').unlink()    # a core that is gone left its port behind
            except OSError:
                pass
            self._spawn()

    def _hello(self):
        self.tries = 0
        cmd = self_cmd()
        self._write({'token': self.token, 'cmd': 'hello', 'role': 'ui', 'helper': cmd, 'ui': cmd})
        self.is_ready = True
        for line in self.waiting:
            self._write(line)
        self.waiting.clear()
        self.ready.emit()

    def _gone(self):
        was = self.is_ready
        self.is_ready = False
        if was:
            self.launched = False
            self.lost.emit()
            QTimer.singleShot(300, self.start)    # the core quit or crashed: bring it back

    def _write(self, obj):
        self.sock.write((json.dumps(obj, separators=(',', ':')) + '\n').encode())

    def send(self, cmd, **args):
        args['cmd'] = cmd
        if self.is_ready and self.sock.state() == QAbstractSocket.SocketState.ConnectedState:
            self._write(args)
        else:
            self.waiting = [w for w in self.waiting if w.get('cmd') != cmd or cmd in ('load', 'reply')] + [args]

    def flush(self, ms=500):
        if self.sock.state() == QAbstractSocket.SocketState.ConnectedState:
            self.sock.flush()
            self.sock.waitForBytesWritten(ms)

    def leave(self):
        """The window is going: hang up at once, so the tray opens a new window instead of calling this one."""
        self.is_ready = False
        self.flush()
        self.sock.disconnectFromHost()
        if self.sock.state() != QAbstractSocket.SocketState.UnconnectedState:
            self.sock.waitForDisconnected(300)

    def _read(self):
        self.buf += bytes(self.sock.readAll())
        *lines, self.buf = self.buf.split(b'\n')
        for line in lines:
            try:
                self.message.emit(json.loads(line))
            except ValueError:
                continue

