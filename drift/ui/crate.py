"""The crate: songs as records in their sleeves (the Vinyl sketch's song lists). Same signals as TrackList, so every
page works the same; only the look and one gesture change: a click takes the record out of its sleeve and puts it
on the deck (it flies to the player). The record on the deck leaves an empty sleeve behind. Hover lifts a record a
little out of its sleeve; the heart and the save sticker sit on the sleeve's corner.

Like TrackList it is one model and one painter: only the sleeves in sight are drawn, so ten thousand records cost
the same as ten."""
import math

from PyQt6.QtCore import QEasingCurve, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QListView, QStyle, QStyledItemDelegate, QWidget

from . import theme
from .covers import shared as covers
from .lists import TrackList, TrackRole, mmss

CELL_W, CELL_H = 178, 250
SLEEVE = 146
LIFT = 46           # room above the sleeve for the record to rise into
PEEK, RISE = 17, 28  # how far a record shows out of its sleeve: at rest, and more under the mouse


class CrateDelegate(QStyledItemDelegate):
    def __init__(self, view, stage, numbered=True):
        super().__init__(view)
        self.view, self.stage, self.numbered = view, stage, numbered
        self.f_title = theme.font(10, 600)
        self.f_sub = theme.caps(8.2, 500, 1.6)

    def sizeHint(self, option, index):
        g = self.view.gridSize()
        return QSize(max(CELL_W, g.width()) - 1, CELL_H)

    def sleeve(self, rect):
        return QRect(rect.left() + (rect.width() - SLEEVE) // 2, rect.top() + LIFT + 4, SLEEVE, SLEEVE)

    def heart_rect(self, rect):
        s = self.sleeve(rect)
        return QRect(s.right() - 32, s.top() + 8, 26, 26)

    def keep_rect(self, rect):
        s = self.sleeve(rect)
        return QRect(s.right() - 62, s.top() + 8, 26, 26)

    def paint(self, p, option, index):
        t = index.data(TrackRole)
        rect = option.rect
        model = index.model()
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        current = model.current_uid == t.uid
        acc = theme.accent.color
        rise = self.view.rise.get(index.row(), 0.0)
        s = QRectF(self.sleeve(rect))
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # the record, rising out of its sleeve (none: it is on the deck)
        if not current:
            d = SLEEVE * 0.94
            disc = self.stage.record(t, int(d), label=True)
            y = s.top() + (SLEEVE - d) / 2 - (PEEK + rise * RISE)
            p.drawPixmap(QPointF(s.left() + (SLEEVE - d) / 2, y), disc)
        # the ledge the row stands on (cells touch, so the ledges make one shelf)
        ledge = QRectF(rect.left(), s.bottom() + 1, rect.width() + 2, 4)
        p.fillRect(ledge, QColor(92, 62, 40, 120))
        p.fillRect(QRectF(ledge.left(), ledge.top(), ledge.width(), 1), QColor(255, 226, 190, 40))
        p.fillRect(QRectF(ledge.left(), ledge.bottom(), ledge.width(), 5), QColor(0, 0, 0, 60))
        # the sleeve: cardboard, the cover printed on it, a ring worn into it by the record inside
        p.setPen(Qt.PenStyle.NoPen)
        for k, a in ((6, 26), (3, 50)):
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(s.adjusted(-k * 0.3, k * 0.5, k * 0.3, k), 3 + k, 3 + k)
        p.drawPixmap(s.toAlignedRect(), covers().get(t, SLEEVE, 3))
        if not current:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 18), 1.2))
            p.drawEllipse(s.center(), SLEEVE * 0.43, SLEEVE * 0.43)
        edge = QRectF(s.left(), s.top(), s.width(), 9)
        from PyQt6.QtGui import QLinearGradient
        g = QLinearGradient(edge.topLeft(), edge.bottomLeft())
        g.setColorAt(0.0, QColor(0, 0, 0, 90))
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(edge, g)                                   # the sleeve's open mouth
        p.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(s.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        if hover or selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 14))
            p.drawRoundedRect(s, 3, 3)
        if current:
            tag = QRectF(s.left() + 8, s.bottom() - 30, 0, 22)
            p.setFont(theme.caps(7.6, 700, 1.6))
            tag.setWidth(QFontMetrics(p.font()).horizontalAdvance('ON THE DECK') + 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(acc)
            p.drawRoundedRect(tag, 11, 11)
            p.setPen(theme.ink_on(acc))
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, 'ON THE DECK')
        # stickers: the heart, saved on this PC
        for r, on, name, show in ((self.heart_rect(rect), bool(t.liked), 'heartfill' if t.liked else 'heart', True),
                                  (self.keep_rect(rect), False, 'saved' if t.local else 'download', bool(t.page) or t.local)):
            if not show or not (on or hover):     # saved shows under the mouse only (a crate is mostly saved songs)
                continue
            rf = QRectF(r)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(acc if on else QColor(12, 12, 14, 200))
            p.drawEllipse(rf)
            p.drawPixmap(r.center().x() - 8, r.center().y() - 8,
                         theme.pixmap(name, theme.ink_on(acc) if on else QColor('#f1ebe2'), 16, 1.9))
        if self.numbered and hover:                          # its catalogue number
            p.setFont(theme.caps(7.6, 600, 1.4))
            p.setPen(QColor(255, 255, 255, 150))
            num = QRectF(s.left() + 8, s.top() + 8, 60, 16)
            p.setBrush(QColor(0, 0, 0, 120))
            p.setPen(Qt.PenStyle.NoPen)
            label = f'NO. {index.row() + 1:02d}'
            num.setWidth(QFontMetrics(p.font()).horizontalAdvance(label) + 12)
            p.drawRoundedRect(num, 8, 8)
            p.setPen(QColor('#f1ebe2'))
            p.drawText(num, Qt.AlignmentFlag.AlignCenter, label)
        # its words, under the sleeve
        x, w = int(s.left()), int(s.width())
        p.setFont(self.f_title)
        p.setPen(acc if current else QColor(theme.TEXT))
        p.drawText(QRect(x, int(s.bottom()) + 14, w, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(self.f_title).elidedText(t.title, Qt.TextElideMode.ElideRight, w))
        p.setFont(self.f_sub)
        p.setPen(QColor(theme.MUTED))
        sub = t.subtitle.upper() + (f'  ·  {mmss(t.duration)}' if t.duration else '')
        p.drawText(QRect(x, int(s.bottom()) + 34, w, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(self.f_sub).elidedText(sub, Qt.TextElideMode.ElideRight, w))
        p.restore()


class CrateList(TrackList):
    """Records in a grid. flew(track, QRect global) asks the window to fly the record to the player."""
    flew = pyqtSignal(object, QRect)

    def __init__(self, stage, parent=None, numbered=True, grow=True):
        super().__init__(parent, numbered=numbered, grow=grow)
        self.stage = stage
        self.rise = {}             # row -> 0..1 how far its record is out of its sleeve
        self.hover_row = -1
        self.delegate = CrateDelegate(self, stage, numbered)
        self.setItemDelegate(self.delegate)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setGridSize(QSize(CELL_W, CELL_H))
        self.setSpacing(0)
        self.setUniformItemSizes(True)
        self.setDragEnabled(False)
        self.doubleClicked.disconnect()          # one click puts a record on
        self.anims = {}

    def set_tracks(self, tracks):
        self.model_.set_tracks(tracks)
        self.rise.clear()
        self.hover_row = -1
        self._fit()

    def _cols(self):
        return max(1, (self.viewport().width() - 2) // CELL_W)

    def _fit(self):
        if self.grow:
            n = len(self.model_.tracks)
            rows = max(1, math.ceil(n / self._cols()))
            self.setFixedHeight(rows * CELL_H + 6)

    def resizeEvent(self, e):
        cell = max(CELL_W, (self.viewport().width() - 2) // self._cols() - 1)      # the row fills the width
        if self.gridSize().width() != cell:
            self.setGridSize(QSize(cell, CELL_H))
        super().resizeEvent(e)
        if self.grow:
            self._fit()

    def _animate(self, row, to):
        a = self.anims.get(row)
        if a is None:
            a = QVariantAnimation(self, duration=200, easingCurve=QEasingCurve(QEasingCurve.Type.OutCubic))
            a.valueChanged.connect(lambda v, r=row: self._rise(r, float(v)))
            a.finished.connect(lambda r=row: self._settled(r))
            self.anims[row] = a
        a.stop()
        a.setStartValue(self.rise.get(row, 0.0))
        a.setEndValue(to)
        a.start()

    def _rise(self, row, v):
        self.rise[row] = v
        ix = self.model_.index(row)
        self.viewport().update(self.visualRect(ix).adjusted(0, -4, 0, 4))

    def _settled(self, row):
        if self.rise.get(row, 0.0) <= 0.001 and row != self.hover_row:
            self.rise.pop(row, None)
            a = self.anims.pop(row, None)
            if a is not None:
                a.deleteLater()

    def mouseMoveEvent(self, e):
        ix = self.indexAt(e.position().toPoint())
        row = ix.row() if ix.isValid() and self.delegate.sleeve(self.visualRect(ix)).adjusted(0, -LIFT, 0, 60).contains(
            e.position().toPoint()) else -1
        if row != self.hover_row:
            if self.hover_row >= 0:
                self._animate(self.hover_row, 0.0)
            self.hover_row = row
            if row >= 0:
                self._animate(row, 1.0)
        self.setCursor(Qt.CursorShape.PointingHandCursor if row >= 0 else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self.hover_row >= 0:
            self._animate(self.hover_row, 0.0)
            self.hover_row = -1
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        ix = self.indexAt(pos)
        if ix.isValid() and e.button() == Qt.MouseButton.LeftButton:
            rect = self.visualRect(ix)
            t = ix.data(TrackRole)
            if self.delegate.heart_rect(rect).contains(pos):
                self.like.emit(t)
                return
            if self.delegate.keep_rect(rect).contains(pos) and (t.page or t.local):
                self.keep.emit(t, self.viewport().mapToGlobal(pos))
                return
            s = self.delegate.sleeve(rect)
            if s.adjusted(0, -LIFT, 0, 60).contains(pos):
                self.setCurrentIndex(ix)
                if self.model_.current_uid != t.uid:
                    d = int(SLEEVE * 0.94)
                    top = s.top() - int(PEEK + self.rise.get(ix.row(), 0.0) * RISE) + (SLEEVE - d) // 2
                    disc = QRect(s.left() + (SLEEVE - d) // 2, top, d, d)
                    self.flew.emit(t, QRect(self.viewport().mapToGlobal(disc.topLeft()), disc.size()))
                self.play.emit(t, self.model_.tracks)
                return
        QListView.mousePressEvent(self, e)


class Flight(QWidget):
    """A record flying from its sleeve to the player: an arc, a turn and a half, shrinking as it lands. It lives
    650 ms over the window and is gone."""

    def __init__(self, parent, pixmap, start, end):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(parent.rect())
        self.px, self.a, self.b = pixmap, QRectF(start), QRectF(end)
        self.t = 0.0
        self.anim = QVariantAnimation(self, duration=650, easingCurve=QEasingCurve(QEasingCurve.Type.InOutCubic))
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.valueChanged.connect(self._step)
        self.anim.finished.connect(self.deleteLater)
        self.show()
        self.raise_()
        self.anim.start()

    def _rect(self, t):
        a, b = self.a, self.b
        w = a.width() + (b.width() - a.width()) * t
        cx = a.center().x() + (b.center().x() - a.center().x()) * t
        cy = a.center().y() + (b.center().y() - a.center().y()) * t - math.sin(math.pi * t) * min(260.0, abs(
            b.center().y() - a.center().y()) * 0.45 + 60)
        return QRectF(cx - w / 2, cy - w / 2, w, w)

    def _step(self, v):
        old = self._rect(self.t).toAlignedRect().adjusted(-24, -24, 24, 24)
        self.t = float(v)
        self.update(old.united(self._rect(self.t).toAlignedRect().adjusted(-24, -24, 24, 24)))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = self._rect(self.t)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, int(70 * math.sin(math.pi * self.t) + 30)))
        lift = 6 + 22 * math.sin(math.pi * self.t)
        p.drawEllipse(r.center() + QPointF(lift * 0.4, lift), r.width() / 2, r.width() / 2)
        p.translate(r.center())
        p.rotate(540 * self.t)
        p.drawPixmap(QRectF(-r.width() / 2, -r.width() / 2, r.width(), r.width()), self.px, QRectF(self.px.rect()))
