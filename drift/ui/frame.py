"""Drift's own title bar. The window has no Windows caption: the top strip is the title bar (drag it, double-click to
maximise), with Drift's own minimise / maximise / close. Moving and resizing go through Qt's startSystemMove /
startSystemResize, so Windows still snaps the window to halves and corners. Windows still draws the shadow and the
rounded corners (DWM). No nativeEvent: in PyQt 6.9 any Python nativeEvent override crashes the window at show."""
import ctypes
import sys

from PyQt6.QtCore import QPoint, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from . import theme

EDGE = 6       # resize grip, logical px


class _Margins(ctypes.Structure):
    _fields_ = [('l', ctypes.c_int), ('r', ctypes.c_int), ('t', ctypes.c_int), ('b', ctypes.c_int)]


def native_frame(win):
    """Shadow + rounded corners from Windows for the frameless window; taskbar click minimises it again."""
    if sys.platform != 'win32':
        return
    try:
        hwnd = int(win.winId())
        u = ctypes.windll.user32
        u.GetWindowLongW.restype = ctypes.c_long
        style = u.GetWindowLongW(hwnd, -16)
        u.SetWindowLongW(hwnd, -16, style | 0x00020000 | 0x00010000)     # MINIMIZEBOX, MAXIMIZEBOX (nothing drawn)
        dwm = ctypes.windll.dwmapi
        m = _Margins(0, 0, 1, 0)
        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))
        on = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))          # dark
        round_ = ctypes.c_int(2)
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(round_), ctypes.sizeof(round_))  # rounded (Windows 11)
    except Exception:
        pass


class TitleBar(QWidget):
    """Empty space in the bar (and its labels) moves the window; double-click maximises or restores."""

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.window().windowHandle() is not None:
            self.window().windowHandle().startSystemMove()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            w = self.window()
            w.showNormal() if w.isMaximized() else w.showMaximized()
            e.accept()


class Grip(QWidget):
    """A thin invisible strip along one edge or corner that resizes the window."""
    SHAPES = {
        Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor, Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
        Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor, Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
    }

    def __init__(self, win, edges):
        super().__init__(win)
        self.edges = edges
        diag = len([x for x in (Qt.Edge.LeftEdge, Qt.Edge.RightEdge, Qt.Edge.TopEdge, Qt.Edge.BottomEdge) if edges & x])
        if diag == 2:
            tl = edges in (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.Edge.RightEdge | Qt.Edge.BottomEdge)
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if tl else Qt.CursorShape.SizeBDiagCursor)
        else:
            self.setCursor(self.SHAPES[Qt.Edge(edges)])

    def mousePressEvent(self, e):
        h = self.window().windowHandle()
        if e.button() == Qt.MouseButton.LeftButton and h is not None:
            h.startSystemResize(self.edges)
            e.accept()


class Grips:
    """Eight grips around the window, kept on the edges; hidden while maximised."""

    def __init__(self, win):
        L, R, T, B = Qt.Edge.LeftEdge, Qt.Edge.RightEdge, Qt.Edge.TopEdge, Qt.Edge.BottomEdge
        self.win = win
        self.grips = {k: Grip(win, k) for k in (L, R, T, B, T | L, T | R, B | L, B | R)}

    def place(self):
        w, h, e = self.win.width(), self.win.height(), EDGE
        L, R, T, B = Qt.Edge.LeftEdge, Qt.Edge.RightEdge, Qt.Edge.TopEdge, Qt.Edge.BottomEdge
        rects = {L: (0, e, e, h - 2 * e), R: (w - e, e, e, h - 2 * e), T: (e, 0, w - 2 * e, e), B: (e, h - e, w - 2 * e, e),
                 T | L: (0, 0, e, e), T | R: (w - e, 0, e, e), B | L: (0, h - e, e, e), B | R: (w - e, h - e, e, e)}
        show = not (self.win.isMaximized() or self.win.isFullScreen())
        for k, g in self.grips.items():
            g.setGeometry(*rects[k])
            g.setVisible(show)
            g.raise_()


class Caption(QPushButton):
    """A window button: minimise, maximise/restore or close; thin line icons, close turns red under the mouse."""

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(46, 40)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip({'min': 'Minimise', 'max': 'Maximise', 'close': 'Close'}[kind])
        self.dark_ink = False      # over a light room: dark lines

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hover = self.underMouse()
        if hover:
            p.fillRect(self.rect(), QColor('#c42b1c') if self.kind == 'close' else
                       QColor(0, 0, 0, 22) if self.dark_ink else QColor(255, 255, 255, 20))
        if self.dark_ink:
            p.setPen(QPen(QColor('#ffffff') if hover and self.kind == 'close' else
                          QColor(theme.INK) if hover else QColor(theme.INK_MUTED), 1.1))
        else:
            p.setPen(QPen(QColor('#ffffff') if hover else QColor(theme.MUTED), 1.1))
        cx, cy = self.width() / 2, self.height() / 2
        if self.kind == 'min':
            p.drawLine(QPoint(int(cx - 5), int(cy)), QPoint(int(cx + 5), int(cy)))
        elif self.kind == 'max':
            if self.window().isMaximized():
                p.drawRoundedRect(QRectF(cx - 5, cy - 3, 8, 8), 1.5, 1.5)
                p.drawLine(QPoint(int(cx - 3), int(cy - 5)), QPoint(int(cx + 5), int(cy - 5)))
                p.drawLine(QPoint(int(cx + 5), int(cy - 5)), QPoint(int(cx + 5), int(cy + 3)))
            else:
                p.drawRoundedRect(QRectF(cx - 5, cy - 5, 10, 10), 1.5, 1.5)
        else:
            p.drawLine(QPoint(int(cx - 5), int(cy - 5)), QPoint(int(cx + 5), int(cy + 5)))
            p.drawLine(QPoint(int(cx + 5), int(cy - 5)), QPoint(int(cx - 5), int(cy + 5)))


def captions(win):
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    mn, mx, cl = Caption('min'), Caption('max'), Caption('close')
    mn.clicked.connect(win.showMinimized)
    mx.clicked.connect(lambda: win.showNormal() if win.isMaximized() else win.showMaximized())
    cl.clicked.connect(win.close)
    for b in (mn, mx, cl):
        row.addWidget(b)
    box.buttons = (mn, mx, cl)
    return box
