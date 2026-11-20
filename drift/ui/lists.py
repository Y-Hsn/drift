"""Song lists: one model + one painter, so ten thousand songs cost the same as ten (only visible rows are drawn)."""
from PyQt6.QtCore import QAbstractListModel, QModelIndex, QPoint, QPointF, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter
from PyQt6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from . import theme
from .covers import shared as covers

ROW = 60
TrackRole = Qt.ItemDataRole.UserRole + 1


def mmss(ms):
    s = max(0, int(ms or 0) // 1000)
    return f'{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}' if s >= 3600 else f'{s // 60}:{s % 60:02d}'


class TrackModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.tracks = []
        self.current_uid = None

    def set_tracks(self, tracks):
        self.beginResetModel()
        self.tracks = list(tracks)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.tracks)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        t = self.tracks[index.row()]
        if role == TrackRole:
            return t
        if role == Qt.ItemDataRole.DisplayRole:
            return t.title
        if role == Qt.ItemDataRole.ToolTipRole:
            return f'{t.title} — {t.subtitle}'
        return None

    def update_uid(self, uid):
        for i, t in enumerate(self.tracks):
            if t.uid == uid:
                ix = self.index(i)
                self.dataChanged.emit(ix, ix)

    def replace(self, track):
        for i, t in enumerate(self.tracks):
            if t.uid == track.uid:
                self.tracks[i] = track
                ix = self.index(i)
                self.dataChanged.emit(ix, ix)

    def set_current(self, uid):
        old, self.current_uid = self.current_uid, uid
        for u in (old, uid):
            if u:
                self.update_uid(u)


class TrackDelegate(QStyledItemDelegate):
    def __init__(self, view, numbered=True):
        super().__init__(view)
        self.view = view
        self.numbered = numbered
        self.f_title = theme.font(10.5, 600)
        self.f_sub = theme.font(9.3)
        self.f_num = theme.font(9.5)

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), ROW)

    def heart_rect(self, rect):
        return QRect(rect.right() - 98, rect.top() + (ROW - 28) // 2, 28, 28)

    def keep_rect(self, rect):
        """Left of the heart: save to this PC (an arrow on hover) or, once saved, a tick (click to remove)."""
        return QRect(rect.right() - 134, rect.top() + (ROW - 28) // 2, 28, 28)

    def paint(self, p, option, index):
        t = index.data(TrackRole)
        r = option.rect.adjusted(6, 1, -10, -1)
        model = index.model()
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        current = model.current_uid == t.uid
        acc = theme.accent.color
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if current:                      # the song that plays is sunlit: its light falls in from the left
            glow = QLinearGradient(QPointF(r.left(), 0), QPointF(r.left() + r.width() * 0.7, 0))
            glow.setColorAt(0, QColor(acc.red(), acc.green(), acc.blue(), 34))
            glow.setColorAt(1, QColor(acc.red(), acc.green(), acc.blue(), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawRoundedRect(r, 12, 12)
        if hover or selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 14) if hover else QColor(255, 255, 255, 9))
            p.drawRoundedRect(r, 12, 12)
        x = r.left() + 8
        if self.numbered:
            box = QRect(x, r.top(), 26, r.height())
            if hover:
                p.drawPixmap(box.center().x() - 8, box.center().y() - 8, theme.pixmap('play', theme.TEXT, 16))
            elif current:
                p.drawPixmap(box.center().x() - 9, box.center().y() - 9, theme.pixmap('volume', acc, 18, 2))
            else:
                p.setFont(self.f_num)
                p.setPen(QColor(theme.DIM))
                p.drawText(box, Qt.AlignmentFlag.AlignCenter, str(index.row() + 1))
            x += 34
        cover = covers().get(t, 42, 6)
        p.drawPixmap(x, r.top() + (r.height() - 42) // 2, cover)
        x += 54
        right = r.right() - 8
        # duration, heart, saved
        p.setFont(self.f_sub)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRect(right - 52, r.top(), 52, r.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   mmss(t.duration) if t.duration else '')
        hr = self.heart_rect(option.rect)
        if t.liked or hover:
            p.drawPixmap(hr.center().x() - 9, hr.center().y() - 9,
                         theme.pixmap('heartfill' if t.liked else 'heart', acc if t.liked else theme.MUTED, 18, 1.8))
        kr = self.keep_rect(option.rect)
        if t.local:
            p.drawPixmap(kr.center().x() - 9, kr.center().y() - 9, theme.pixmap('saved', acc, 18, 1.8))
        elif hover and t.page:
            p.drawPixmap(kr.center().x() - 9, kr.center().y() - 9, theme.pixmap('download', theme.MUTED, 18, 1.8))
        text_right = right - 140
        width = r.width()
        if width > 760 and t.album:
            col = QRect(x + int((text_right - x) * 0.62), r.top(), int((text_right - x) * 0.38), r.height())
            p.setPen(QColor(theme.MUTED))
            p.drawText(col, Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(self.f_sub).elidedText(t.album, Qt.TextElideMode.ElideRight, col.width() - 10))
            text_right = col.left() - 12
        tw = max(40, text_right - x)
        p.setFont(self.f_title)
        p.setPen(acc if current else QColor(theme.TEXT))
        p.drawText(QRect(x, r.top() + 9, tw, 22), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(self.f_title).elidedText(t.title, Qt.TextElideMode.ElideRight, tw))
        p.setFont(self.f_sub)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRect(x, r.top() + 30, tw, 18), Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(self.f_sub).elidedText(t.subtitle, Qt.TextElideMode.ElideRight, tw))
        p.restore()


class TrackList(QListView):
    """play(track, tracks), like(track), keep(track, QPoint global) = the save/remove button, menu(track, tracks,
    QPoint global)."""
    play = pyqtSignal(object, object)
    like = pyqtSignal(object)
    keep = pyqtSignal(object, QPoint)
    menu = pyqtSignal(object, object, QPoint)

    def __init__(self, parent=None, numbered=True, grow=True):
        super().__init__(parent)
        self.model_ = TrackModel(self)
        self.setModel(self.model_)
        self.delegate = TrackDelegate(self, numbered)
        self.setItemDelegate(self.delegate)
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.doubleClicked.connect(lambda ix: self.play.emit(ix.data(TrackRole), self.model_.tracks))
        self.grow = grow      # inside a scrolling page: as tall as its rows, the page scrolls
        if grow:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        covers().ready.connect(self.model_.update_uid)

    def set_tracks(self, tracks):
        self.model_.set_tracks(tracks)
        if self.grow:
            self.setFixedHeight(max(1, len(self.model_.tracks)) * ROW + 4)

    def tracks(self):
        return self.model_.tracks

    def mousePressEvent(self, e):
        ix = self.indexAt(e.position().toPoint())
        if ix.isValid() and e.button() == Qt.MouseButton.LeftButton:
            pos = e.position().toPoint()
            rect = self.visualRect(ix)
            if self.delegate.heart_rect(rect).contains(pos):
                self.like.emit(ix.data(TrackRole))
                return
            t = ix.data(TrackRole)
            if self.delegate.keep_rect(rect).contains(pos) and (t.page or t.local):
                self.keep.emit(t, self.viewport().mapToGlobal(pos))
                return
            if self.delegate.numbered and pos.x() < rect.left() + 48:
                self.play.emit(ix.data(TrackRole), self.model_.tracks)
                return
        super().mousePressEvent(e)

    def _menu(self, pos):
        ix = self.indexAt(pos)
        if ix.isValid():
            self.menu.emit(ix.data(TrackRole), self.model_.tracks, self.viewport().mapToGlobal(pos))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.play.emit(self.currentIndex().data(TrackRole), self.model_.tracks)
            return
        super().keyPressEvent(e)
