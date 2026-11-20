"""Run slow work (network, tags, files) off the window's thread; the answer comes back on the window's thread."""
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

_pool = QThreadPool.globalInstance()
_pool.setMaxThreadCount(4)
_live = set()        # keeps each bridge alive until its answer is delivered


class _Bridge(QObject):
    done = pyqtSignal(object)
    failed = pyqtSignal(object)
    progress = pyqtSignal(object)


class _Job(QRunnable):
    def __init__(self, fn, args, bridge):
        super().__init__()
        self.fn, self.args, self.bridge = fn, args, bridge
        self.setAutoDelete(True)

    def run(self):
        try:
            value = self.fn(*self.args)
        except BaseException as exc:  # noqa: BLE001  every failure goes back to the caller
            self.bridge.failed.emit(exc)
        else:
            self.bridge.done.emit(value)


def run(fn, *args, done=None, failed=None, progress=None):
    """fn(*args) on a worker; done(value) / failed(exception) later on the window's thread."""
    bridge = _Bridge()
    if progress:
        bridge.progress.connect(progress)
        args += (bridge.progress.emit,)
    _live.add(bridge)

    def finish(cb, value):
        _live.discard(bridge)
        bridge.deleteLater()
        if cb:
            cb(value)

    bridge.done.connect(lambda v: finish(done, v))
    bridge.failed.connect(lambda e: finish(failed, e))
    _pool.start(_Job(fn, args, bridge))


def shutdown(ms=1500):
    """Leaving: drop work not started yet and give running work a moment to finish."""
    _pool.clear()
    _pool.waitForDone(ms)
