"""Open Drift off-screen with a scratch data folder, visit every page, save screenshots, quit. python tests/smoke.py OUTDIR"""
import os, sys, tempfile
out = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
os.environ.setdefault('DRIFT_DATA', tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
import drift.app as A

orig = QApplication.exec
def run():
    win = next(w for w in QApplication.topLevelWidgets() if w.__class__.__name__ == 'Window')
    win.resize(int(os.environ.get('W', 1100)), 760)
    steps = ['home', 'explore', 'library', 'liked', 'queue', 'lyrics', 'home']
    def step(i=0):
        if i < len(steps):
            win.go(steps[i])
            QTimer.singleShot(900, lambda: (win.grab().save(os.path.join(out, f'{i}-{steps[i]}.png')), step(i + 1)))
        else:
            win.quit()
    QTimer.singleShot(1500, step)
    return orig()
QApplication.exec = staticmethod(run)
print('exit', A.main(['drift']))
