"""The window. Its look is a sketch (sketches/): the sketch's stage builds the Now page (a landscape, a turntable...),
paints the ground of every page, the index along the top and the player strip along the bottom (both float over the
page, which fades under them), and decides how song lists look. Pages dissolve into each other. Song menus, shortcuts
and settings (the tray icon is the core's). Changing the sketch builds a new window around the music (swap)."""
import ctypes
import os
import subprocess
import sys
import time

from PyQt6.QtCore import QByteArray, QEasingCurve, QPoint, QRect, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QGuiApplication, QKeySequence, QPainter, QShortcut
from PyQt6.QtWidgets import (QButtonGroup, QGraphicsOpacityEffect, QHBoxLayout,
                             QLabel, QMainWindow, QMenu, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from .. import library, tasks
from . import frame, sketches, theme
from .covers import shared as covers
from .lists import TrackList
from .lyricsfeed import shared as lyrics_feed
from .pages import ExplorePage, ListPage, LyricsPage
from .playlists import PlaylistsPage, ask_name
from .settings import SettingsPage
from .widgets import Dock, IconButton, label

TOP = 48         # the index along the top
STRIP = 108      # the horizon along the bottom
LIVE = None      # the window on screen (a sketch change replaces it)
UPDATE_CHECKING = False
UPDATE_MANUAL = False


def live():
    return LIVE


def release(engine):
    """Let go of every window connection to the shared parts (engine, colours, covers, lyrics), so a new window can
    take over and the old one goes without a trace. The engine's own link (queue -> core) is put back."""
    for name in ('track_changed', 'playing_changed', 'position_changed', 'queue_changed', 'loading', 'message',
                 'online_changed', 'track_updated', 'library_changed', 'downloads_changed', 'show_requested',
                 'quit_requested', 'wave_ready'):
        try:
            getattr(engine, name).disconnect()
        except TypeError:
            pass
    engine.queue_changed.connect(engine._sync_queue)
    for sig in (theme.scene.changed, theme.accent.changed, covers().ready, lyrics_feed().ready):
        try:
            sig.disconnect()
        except TypeError:
            pass


class Room(QWidget):
    """The ground of every page: the song's sky after dusk (its colours, deep and quiet), a far glow where its sun
    went down, fine grain. It glides to each new song's colours."""

    def __init__(self, placed, look):
        super().__init__()
        self.setObjectName('Main')
        self.placed, self.look = placed, look
        theme.scene.changed.connect(self.update)

    def resizeEvent(self, e):
        self.placed()
        super().resizeEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.look.paint_room(p, self, e.rect())


class Top(frame.TitleBar):
    """The index along the top; over a page it fades the page out under it (over Now it is just words on the sky)."""
    solid = True
    look = None

    def paintEvent(self, e):
        if self.look is not None:
            p = QPainter(self)
            self.look.paint_top(p, self, self.solid)


class Tabs(QWidget):
    """Text tabs along the top; a short accent line slides under the one that is open (200 ms)."""

    def __init__(self, look, parent=None):
        super().__init__(parent)
        self.look = look
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons = {}
        self.mark = QRectF()
        self.anim = QVariantAnimation(self, duration=200, easingCurve=QEasingCurve(QEasingCurve.Type.OutCubic))
        self.anim.valueChanged.connect(self._slide)
        theme.accent.changed.connect(lambda _c: self.update())

    def add(self, key, text, fn):
        b = QPushButton(text.lower())
        b.setObjectName('Tab')
        b.setFont(theme.caps(10.5, 400, 0.6, condensed=False))
        b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)   # never squeezed into a clipped word
        b.setCheckable(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(fn)
        self.group.addButton(b)
        self.row.addWidget(b)
        self.buttons[key] = b
        return b

    def set_compact(self, compact):
        for b in self.buttons.values():
            b.setFont(theme.caps(9.1, 400, .3, condensed=False) if compact
                      else theme.caps(10.5, 400, .6, condensed=False))
            b.setStyleSheet('padding: 6px 4px;' if compact else '')

    def select(self, key):
        b = self.buttons.get(key)
        if b is None:
            return
        b.setChecked(True)
        QTimer.singleShot(0, lambda: self._aim(b))

    def _aim(self, b):
        if not self.isVisible() or not b.isVisible() or b.width() <= 0 or b.x() == 0 and b is not self.row.itemAt(0).widget():
            self.mark = QRectF()      # not laid out yet: no mark (it once sat in a wrong corner); aim again shortly
            self.update()
            QTimer.singleShot(60, lambda: b.isChecked() and self.isVisible() and self._aim(b))
            return
        g = b.geometry()
        target = QRectF(g.center().x() - 2.5, g.bottom() - 6, 5, 5)
        if self.mark.isNull() or not self.isVisible():
            self.mark = target
            self.update()
            return
        self.anim.stop()
        self.anim.setStartValue(self.mark)
        self.anim.setEndValue(target)
        self.anim.start()

    def _slide(self, r):
        self.mark = QRectF(r)
        self.update()

    def resizeEvent(self, e):
        b = self.group.checkedButton()
        if b is not None:
            QTimer.singleShot(0, lambda: self._aim(b))
        super().resizeEvent(e)

    def paintEvent(self, e):
        if self.mark.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.look.paint_mark(p, self.mark, theme.accent.color)      # under the open page


class FadeStack(QStackedWidget):
    """Pages dissolve into each other (170 ms): a picture of the old page fades out over the new one."""

    def __init__(self):
        super().__init__()
        self.veil = QLabel(self)
        self.veil.hide()
        self.fx = QGraphicsOpacityEffect(self.veil)
        self.veil.setGraphicsEffect(self.fx)
        self.anim = QVariantAnimation(self, duration=170, easingCurve=QEasingCurve(QEasingCurve.Type.OutCubic))
        self.anim.valueChanged.connect(lambda v: self.fx.setOpacity(float(v)))
        self.anim.finished.connect(self._done)

    def show_page(self, w):
        old = self.currentWidget()
        if old is w:
            return
        if old is not None and self.isVisible() and self.width() > 0:
            self.veil.setPixmap(old.grab())
            self.veil.setGeometry(self.rect())
            self.fx.setOpacity(1.0)
            self.veil.show()
            self.veil.raise_()
            self.anim.stop()
            self.anim.setStartValue(1.0)
            self.anim.setEndValue(0.0)
            self.setCurrentWidget(w)
            self.veil.raise_()
            self.anim.start()
        else:
            self.setCurrentWidget(w)

    def _done(self):
        self.veil.hide()
        self.veil.clear()      # the picture is let go at once


class Window(QMainWindow):
    def __init__(self, store, engine):
        super().__init__()
        global LIVE
        LIVE = self
        self.store, self.engine = store, engine
        self.sketch = sketches.active(store)
        self.look = self.sketch.make()
        sketches.apply(self.sketch, self.look)
        self._watch_sketch()
        self.setWindowTitle('Drift')
        self.setMinimumSize(560, 560)
        if sys.platform == 'win32' and QGuiApplication.platformName() == 'windows':
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint)   # the top strip is the title bar (frame.py)
        self.grips = None
        self.quitting = False
        self.page = None
        root = Room(self._place, self.look)
        self.root = root
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.stack = FadeStack()
        outer.addWidget(self.stack, 1)
        self.top = self._top()
        self.top.setParent(root)
        self.bar = Dock(engine, root, self.look)
        theme.scene.changed.connect(lambda: (self.bar.update(), self.top.update()))
        w = self.look.word
        self.home = self.look.now_page(self)
        self.explore = ExplorePage(self)
        self.library = ListPage(self, w('library', 'Your Library'), 'On this PC', (
            'Nothing saved yet', 'Press the arrow next to the heart on any song to keep it here: it then plays '
            'offline. Or choose a folder of your own music in Settings.'),
            (('Open folder', self.open_folder), ('Rescan', self.rescan)))
        self.liked = ListPage(self, w('liked', 'Liked songs'), 'Loved', (
            'No likes yet', 'Tap the heart on any song: likes teach Drift your taste. A like does not save the song; '
            'the arrow next to the heart does.'))
        self.queue_page = ListPage(self, w('queue', 'Queue'), 'Up next', (
            'Nothing queued', 'With Drift on, songs you will like keep coming.'), (('Clear', engine.clear_queue),))
        self.playlist_page = ListPage(self, 'Playlist', 'Playlist', (
            'An empty page', 'Right-click any song, Add to playlist.'),
            (('All playlists', lambda: self.go('playlists')), ('Rename', self.rename_playlist),
             ('Delete', self.delete_playlist)))
        self.playlists_page = PlaylistsPage(self.store, w('playlists', 'Playlists'))
        self.playlists_page.open.connect(lambda pid: self.go('playlist', pid))
        self.playlists_page.new.connect(lambda: (lambda p: p and self.go('playlist', p))(self.new_playlist()))
        self.lyrics = LyricsPage(self)
        self.settings_page = SettingsPage(self)
        self.pages = {'home': self.home, 'explore': self.explore, 'library': self.library, 'liked': self.liked,
                      'queue': self.queue_page, 'playlists': self.playlists_page, 'playlist': self.playlist_page,
                      'lyrics': self.lyrics, 'settings': self.settings_page, 'calm': self.home}
        self.back = 'home'           # where Esc (or the gear again) leads from Settings, Lyrics, Queue
        for k, p in self.pages.items():
            if k != 'calm':
                self.stack.addWidget(p)
        self.playlist_id = None
        self.toast = QLabel(self)
        self.toast.setStyleSheet(f'background: rgba(10,10,14,0.9); color: {theme.TEXT}; border: 1px solid '
                                 f'{theme.EDGE}; border-radius: 17px; padding: 9px 20px;')
        self.toast.setFont(theme.font(10, 500))
        self.toast.hide()
        self.toast_timer = QTimer(self, singleShot=True, interval=3500, timeout=self.toast.hide)
        self.update_pill = QPushButton(self)
        self.update_pill.setStyleSheet(f'QPushButton {{ background: rgba(10,10,14,0.92); color: {theme.TEXT}; '
                                       f'border: 1px solid {theme.EDGE}; border-radius: 17px; padding: 9px 20px; }} '
                                       f'QPushButton:hover {{ border-color: {theme.TEXT}; }}')
        self.update_pill.setFont(theme.font(10, 500))
        self.update_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_pill.clicked.connect(self.update_now)
        self.update_pill.hide()
        self.bar.installEventFilter(self)      # the player bar shows/hides/moves with the page: notes follow it
        self.update_timer = QTimer(self, interval=3600_000, timeout=self.check_update)   # due() keeps it to 6 h
        self.update_timer.start()
        self.trim_timer = QTimer(self, singleShot=True, interval=20_000, timeout=self.trim)
        self._wire()
        self._shortcuts()
        theme.accent.changed.connect(self._accent)
        self._accent(theme.accent.color)
        if self.windowFlags() & Qt.WindowType.FramelessWindowHint:
            self.grips = frame.Grips(self)
        self.go('home')
        self._check_update(cached_only=True)

    # ---- layout -------------------------------------------------------------------------------------------------
    def _top(self):
        """The top strip is the title bar: drag it, double-click it to maximise; Drift's own window buttons."""
        top = Top()
        top.look = self.look
        top.setFixedHeight(TOP)
        row = QHBoxLayout(top)
        row.setContentsMargins(22, 0, 0, 0)
        row.setSpacing(6)
        self.logo = QLabel('drift')
        self.logo.setFont(theme.serif(15, 500, italic=True))
        self.logo.setStyleSheet(f'color: {theme.TEXT}; background: transparent;')
        self.logo.setContentsMargins(0, 0, 20, 0)
        row.addWidget(self.logo)
        self.tabs = Tabs(self.look)
        words = self.look.tabs
        for key in ('home', 'explore', 'library', 'liked', 'queue'):
            self.tabs.add(key, words.get(key, key), lambda _=False, k=key: self.go(k))
        self.lists_tab = self.tabs.add('playlists', words.get('playlists', 'Lists'),
                                       lambda _=False: self.go('playlists'))
        row.addWidget(self.tabs)
        row.addStretch()
        self.saving = label('', 9, 400, theme.MUTED)
        row.addWidget(self.saving)
        row.addSpacing(10)
        self.net = QLabel()
        self.net.setObjectName('Net')            # in the accent (theme.stylesheet)
        self.net.setFont(theme.caps(8.4, 700, 2.0))
        self.net.setToolTip('No internet: Drift plays and searches the songs saved on this PC. '
                            'Everything else comes back with the internet.')
        row.addWidget(self.net)
        gear = IconButton('settings', 17, 34, 'Settings')
        gear.clicked.connect(self.settings)
        row.addWidget(gear)
        row.addSpacing(6)
        self.captions = frame.captions(self)
        row.addWidget(self.captions, 0, Qt.AlignmentFlag.AlignTop)
        self.lists_box = None
        return top


    def _accent(self, color):
        QGuiApplication.instance().setStyleSheet(theme.stylesheet(color))

    def _place(self):
        """The index and the player float over the page: over Now the player sits in the lake, elsewhere it is the
        horizon along the bottom; calm view hides both."""
        if not hasattr(self, 'bar') or not hasattr(self, 'home'):
            return
        w, h = self.root.width(), self.root.height()
        now = self.page in ('home', 'calm')
        calm = self.page == 'calm'
        self.top.solid = not now
        self.top.setGeometry(0, 0, w, TOP)
        self.top.setVisible(not calm)
        self.top.update()
        self.set_top_ink(now and getattr(self.home, 'light_top', False))
        if now:
            self.bar.set_mode(self.look.now_dock)
            self.bar.setGeometry(self.home.dock_rect())
            self.bar.setVisible(not calm and self.engine.current is not None)
        else:
            self.bar.set_mode('strip')
            self.bar.setGeometry(0, h - STRIP, w, STRIP)
            self.bar.setVisible(True)
        self.top.raise_()
        self.bar.raise_()
        if self.grips:
            self.grips.place()

    def set_top_ink(self, dark):
        """Drift's own words along the top in dark ink, over a light room (a canvas sketch says when its top is
        light); light ink everywhere else."""
        if getattr(self, '_dark_top', False) == dark:
            return
        self._dark_top = dark
        self.logo.setStyleSheet(f'color: {theme.INK if dark else theme.TEXT}; background: transparent;')
        self.tabs.setStyleSheet(
            f'QPushButton#Tab {{ color: {theme.INK_MUTED}; }} QPushButton#Tab:hover {{ color: {theme.INK}; }} '
            f'QPushButton#Tab:checked {{ color: {theme.INK}; }}' if dark else '')
        from .widgets import IconButton
        for b in self.top.findChildren(IconButton) + self.top.findChildren(frame.Caption):
            b.dark_ink = dark
            b.update()

    def resizeEvent(self, e):
        compact = self.width() < 700
        if compact != getattr(self, '_compact_index', None):
            self._compact_index = compact
            self.top.layout().setContentsMargins(8 if compact else 22, 0, 0, 0)
            self.top.layout().setSpacing(4 if compact else 6)
            self.logo.setVisible(not compact)
            self.saving.setVisible(not compact)
            self.tabs.set_compact(compact)
            self._network_label(self.engine.online)
        self.grips.place() if self.grips else None
        self._place_toast()
        super().resizeEvent(e)

    # ---- wiring ---------------------------------------------------------------------------------------------------
    def _wire(self):
        e = self.engine
        e.message.connect(self.say)
        e.queue_changed.connect(lambda: self.page == 'queue' and self.show_queue())
        e.track_changed.connect(self._now)
        e.track_updated.connect(self._updated)
        e.library_changed.connect(self._library_changed)
        e.online_changed.connect(self._online)
        e.downloads_changed.connect(self._downloads)
        e.show_requested.connect(self.bring_up)
        e.hearing.connect(self._hearing)
        e.heard.connect(lambda ev: self.explore.heard(ev))
        e.quit_requested.connect(lambda: self.quit(keep_music=True))
        self.bar.queue.clicked.connect(lambda: self.go('queue') if self.page != 'queue' else self.go_back())
        self.bar.lyrics.clicked.connect(lambda: self.go('lyrics') if self.page != 'lyrics' else self.go_back())
        self.bar.cover.mousePressEvent = lambda ev: self.go('lyrics')
        self._online(e.online)
        self.refresh_playlists()

    def _hearing(self, on, why):
        if on and self.page != 'explore':
            self.go('explore')     # no pop-up while listening: the window comes up once the song is named
        self.explore.hearing(on, why)

    def _now(self, t):
        if self.page in ('home', 'calm'):
            self._place()
        for p in (self.library, self.liked, self.queue_page, self.playlist_page, self.explore):
            lst = getattr(p, 'list', None) or getattr(p, 'results', None)
            if lst is not None:
                lst.model_.set_current(t.uid if t else None)
        self.setWindowTitle(f'{t.title} · {t.subtitle} — Drift' if t else 'Drift')

    def _updated(self, t):
        for p in self.pages.values():
            for lst in p.findChildren(TrackList):
                lst.model_.replace(t) if any(x.uid == t.uid for x in lst.tracks()) else None
        covers().forget(t.uid) if t.local else None
        if self.page == 'liked':
            self.liked.set_tracks(self.store.liked())

    def _library_changed(self):
        if self.page == 'library':
            self.library.set_tracks(self.store.local_tracks())

    def _online(self, on):
        self._network_label(on)
        if self.page == 'explore':
            self.explore.refresh()

    def _network_label(self, online):
        offline = 'OFFLINE' if self.width() < 700 else 'OFFLINE · SAVED SONGS'
        self.net.setText('' if online else offline)

    def _downloads(self, waiting, frac):
        self.saving.setText(f'Saving {waiting} song{"s" if waiting != 1 else ""} · {int(frac * 100)}%' if waiting else '')

    # ---- pages ----------------------------------------------------------------------------------------------------
    def go(self, key, playlist=None):
        if key in ('settings', 'lyrics', 'queue') and self.page not in ('settings', 'lyrics', 'queue', None):
            self.back = 'home' if self.page == 'calm' else self.page
        self.page = key
        self.home.set_calm(key == 'calm')
        tab = 'playlists' if key == 'playlist' else key
        if tab in self.tabs.buttons:
            self.tabs.select(tab)
        else:
            b = self.tabs.group.checkedButton()
            if b:
                self.tabs.group.setExclusive(False)
                b.setChecked(False)
                self.tabs.group.setExclusive(True)
                self.tabs.mark = QRectF()
                self.tabs.update()
        self.bar.queue.setChecked(key == 'queue')
        self.bar.lyrics.setChecked(key == 'lyrics')
        if key == 'home':
            self.home.refresh()
        elif key == 'explore':
            self.explore.refresh()
            QTimer.singleShot(0, self.explore.focus)
        elif key == 'library':
            self.library.set_tracks(self.store.local_tracks())
        elif key == 'liked':
            self.liked.set_tracks(self.store.liked())
        elif key == 'queue':
            self.show_queue()
        elif key == 'playlist':
            self.playlist_id = playlist
            name = dict(self.store.playlists()).get(playlist, 'Playlist')
            self.playlist_page.heading.setText(name)
            self.playlist_page.set_tracks(self.store.playlist_tracks(playlist), self.store.playlist_note(playlist))
        elif key == 'playlists':
            self.playlists_page.refresh()
        elif key == 'settings':
            self.settings_page.refresh()
        self.stack.show_page(self.pages[key])
        self._place()
        cur = self.engine.current
        self._now(cur) if cur else None

    def show_queue(self):
        q = self.engine.queue
        note = f'{len(q)} up next' + (' · Drift keeps adding songs you will like' if self.engine.drift else '')
        self.queue_page.set_tracks(q, note)

    # ---- actions --------------------------------------------------------------------------------------------------
    def play_from(self, track, tracks):
        if self.page == 'queue':
            i = next((k for k, t in enumerate(self.engine.queue) if t.uid == track.uid), -1)
            if i >= 0:
                del self.engine.queue[:i]
                self.engine.next()
                return
        self.engine.play(track, tracks)

    def jump_to(self, track):
        """Play a song from the queue now; the ones before it are skipped."""
        i = next((k for k, t in enumerate(self.engine.queue) if t.uid == track.uid), -1)
        if i >= 0:
            del self.engine.queue[:i]
            self.engine.next()
        else:
            self.engine.play(track)

    def track_menu(self, track, tracks, pos):
        m = QMenu(self)
        e = self.engine
        m.addAction('Play now', lambda: self.play_from(track, tracks))
        m.addAction('Play next', lambda: e.play_next([track]))
        m.addAction('Add to queue', lambda: e.enqueue([track]))
        m.addAction('Start radio from this song', lambda: e.start_radio(seed=track))
        m.addSeparator()
        m.addAction('Remove from liked' if track.liked else 'Like', lambda: e.like(track))
        sub = m.addMenu('Add to playlist')
        for pid, name in self.store.playlists():
            sub.addAction(name, lambda p=pid: self._add_to(p, track))
        if self.store.playlists():
            sub.addSeparator()
        sub.addAction('New playlist…', lambda: self._add_to(self.new_playlist(), track))
        if track.local and track.page:
            m.addAction('Remove from this PC', lambda: e.unsave(track))
        elif track.local:
            m.addAction('Move the file to the Recycle Bin', lambda: e.unsave(track))
        elif track.page:
            a = m.addAction('Saving to this PC…' if e.saving(track) else 'Save to this PC', lambda: e.save(track))
            a.setEnabled(not e.saving(track))
        m.addSeparator()
        if self.page == 'queue' or any(t.uid == track.uid for t in self.engine.queue):
            m.addAction('Remove from queue', lambda: self._unqueue(track))
        if self.page == 'playlist' and self.playlist_id:
            m.addAction('Remove from this playlist', lambda: (self.store.remove_from_playlist(self.playlist_id, track.uid),
                                                              self.go('playlist', self.playlist_id)))
        if track.local:
            m.addAction('Show in folder', lambda: subprocess.Popen(['explorer', '/select,', os.path.normpath(track.path)]))
        if track.page:
            m.addAction('Copy link', lambda: QGuiApplication.clipboard().setText(track.page))
        m.exec(pos)

    def _unqueue(self, track):
        i = next((k for k, t in enumerate(self.engine.queue) if t.uid == track.uid), -1)
        self.engine.remove_from_queue(i)

    def _add_to(self, pid, track):
        if pid:
            self.store.add_to_playlist(pid, [track])
            self.say(f'Added to {dict(self.store.playlists()).get(pid, "playlist")}.')

    def new_playlist(self):
        name = ask_name(self, 'New playlist')
        if not name:
            return None
        pid = self.store.new_playlist(name)
        self.refresh_playlists()
        return pid

    def rename_playlist(self):
        if not self.playlist_id:
            return
        old = dict(self.store.playlists()).get(self.playlist_id, '')
        name = ask_name(self, 'Rename playlist', old, 'Rename')
        if name:
            self.store.rename_playlist(self.playlist_id, name)
            self.refresh_playlists()
            self.go('playlist', self.playlist_id)

    def delete_playlist(self):
        if self.playlist_id:
            name = dict(self.store.playlists()).get(self.playlist_id, 'the playlist')
            self.store.drop_playlist(self.playlist_id)   # songs stay in the library; only the list goes
            self.playlist_id = None
            self.refresh_playlists()
            self.say(f'Deleted {name}. Its songs were not touched.')
            self.go('playlists')

    def refresh_playlists(self):
        """The Playlists page shows the cards fresh when it is open."""
        if self.page == 'playlists':
            self.playlists_page.refresh()

    def keep_toggle(self, track, pos):
        """The save button of a song: save it to this PC, or (already here) offer to remove it."""
        e = self.engine
        if track.local:
            m = QMenu(self)
            m.addAction('Remove from this PC (to the Recycle Bin)', lambda: e.unsave(track))
            m.addAction('Show in folder', lambda: subprocess.Popen(['explorer', '/select,', os.path.normpath(track.path)]))
            m.exec(pos)
        elif e.saving(track):
            self.say('Already saving it.')
        else:
            e.save(track)

    def open_folder(self):
        folder = self.store.get('music_folder')
        os.makedirs(folder, exist_ok=True)
        os.startfile(folder)

    def rescan(self, quiet=False):
        folder = self.store.get('music_folder')
        if not quiet:
            self.say('Looking for songs…')

        def done(n):
            self._library_changed()
            if not quiet:
                self.say(f'{n} songs in your library.')
            if self.page == 'home':
                self.home.refresh()

        tasks.run(library.scan, folder, self.store, done=done, failed=lambda e: self.say(f'Scan failed: {e}'))

    def say(self, text):
        self.toast.setText(text)
        self.toast.adjustSize()
        self._place_toast()
        self.toast.show()
        self.toast.raise_()
        self.toast_timer.start()

    def _place_toast(self):
        if hasattr(self, 'toast'):
            # the player bar lives inside the page root: its top in window coordinates
            bottom = self.bar.mapTo(self, QPoint(0, 0)).y() if self.bar.isVisible() else self.height()
            self.toast.move((self.width() - self.toast.width()) // 2, bottom - self.toast.height() - 18)
            if self.toast.isVisible():
                bottom = self.toast.y() - 8
            self.update_pill.move((self.width() - self.update_pill.width()) // 2,
                                  bottom - self.update_pill.height() - 18)

    def eventFilter(self, obj, e):
        if obj is getattr(self, 'bar', None) and e.type() in (e.Type.Show, e.Type.Hide, e.Type.Move, e.Type.Resize):
            self._place_toast()
        return super().eventFilter(obj, e)

    # ---- updates (drift/update.py) ----------------------------------------------------------------------------------
    def check_update(self, manual=False):
        self._check_update(manual=manual)

    def _check_update(self, manual=False, cached_only=False):
        """Check the local stage first on startup, then check GitHub when due or asked."""
        from .. import update
        global UPDATE_CHECKING, UPDATE_MANUAL
        if update.found:
            return self._update_ready(update.found)
        if UPDATE_CHECKING:
            if manual:
                UPDATE_MANUAL = True
                self.say('Looking for updates…')
            return
        if not update.can_update():
            return manual and self.say('Updates come through the Drift.exe from the releases page.')
        if not cached_only and not manual and (not self.engine.online or not update.due(self.store)):
            return

        def finished(info=None, error=False):
            global UPDATE_CHECKING, UPDATE_MANUAL
            requested = UPDATE_MANUAL
            UPDATE_CHECKING = UPDATE_MANUAL = False
            window = live()
            if window.quitting:
                return
            if info:
                update.found = info
                window._update_ready(info)
            elif cached_only:
                if requested:
                    window.check_update(manual=True)
                else:
                    QTimer.singleShot(8000, window.check_update)
            elif requested:
                window.say('Could not get the update. Try again.' if error else
                           f'Drift {update.__version__} is the newest.')

        if not cached_only:
            self.store.set('update_checked', time.time())
        UPDATE_CHECKING, UPDATE_MANUAL = True, manual
        tasks.run(update.staged if cached_only else update.prepare, done=finished,
                  failed=lambda _: finished(error=True))

    def _update_ready(self, info):
        self.update_pill.setEnabled(True)
        self.update_pill.setText(f'Drift {info["version"]} is ready · Restart to update')
        self.update_pill.adjustSize()
        self.update_pill.show()
        self.update_pill.raise_()
        self._place_toast()

    def update_now(self):
        from .. import update
        from ..core import read_port
        info = update.found
        if not info or self.quitting or not self.update_pill.isEnabled():
            return
        self.update_pill.setEnabled(False)
        self.update_pill.setText('Restarting…')
        self.update_pill.adjustSize()
        self._place_toast()
        core = read_port()
        self.store.set('resume_after_update', bool(self.engine.playing))
        try:
            update.apply(info['path'], info['version'], [os.getpid(), core[2] if core else 0])
        except (OSError, ValueError):
            self.store.set('resume_after_update', False)
            self.say('Could not restart to update.')
            if info['path'].is_dir():
                self._update_ready(info)
            else:
                update.found = None
                self.update_pill.hide()
            return
        self.quit(keep_music=False)

    # ---- keys -----------------------------------------------------------------------------------------------------
    def _shortcuts(self):
        e = self.engine

        def typing():
            from PyQt6.QtWidgets import QApplication, QLineEdit
            return isinstance(QApplication.focusWidget(), QLineEdit)

        keys = {
            'Space': lambda: typing() or e.toggle(),
            'Ctrl+Right': lambda: e.next(), 'Ctrl+Left': e.previous,
            'Ctrl+Up': lambda: (e.set_volume(e.volume + 0.05), self.bar.volume.set_value(e.volume)),
            'Ctrl+Down': lambda: (e.set_volume(e.volume - 0.05), self.bar.volume.set_value(e.volume)),
            'Right': lambda: typing() or e.seek(e.position + 5000),
            'Left': lambda: typing() or e.seek(max(0, e.position - 5000)),
            'M': lambda: typing() or self.bar._mute(),
            'J': lambda: typing() or self.bar.jump_best() or self.say('No part of this song stands out yet.'),
            'Ctrl+L': lambda: e.current and e.like(e.current),
            'Ctrl+F': lambda: self.go('explore'), 'Ctrl+K': lambda: self.go('explore'),
            'Ctrl+Q': lambda: self.go('queue'), 'Ctrl+Y': lambda: self.go('lyrics'),
            'Ctrl+S': lambda: self.bar.shuffle.toggle(), 'Ctrl+R': self.bar._repeat,
            'Ctrl+1': lambda: self.go('home'), 'Ctrl+2': lambda: self.go('explore'),
            'Ctrl+3': lambda: self.go('library'), 'Ctrl+4': lambda: self.go('liked'),
            'Escape': lambda: (self.go('home') if self.page == 'calm'
                               else self.page in ('lyrics', 'queue', 'settings') and self.go_back()),
            'F': lambda: typing() or self.go('home' if self.page == 'calm' else 'calm'),
            'Ctrl+T': self.next_sketch,
        }
        for k, fn in keys.items():
            s = QShortcut(QKeySequence(k), self)
            s.setContext(Qt.ShortcutContext.WindowShortcut)
            s.activated.connect(fn)

    def bring_up(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        """The window quits for real (no memory left behind); the music core keeps playing if it was."""
        e.accept()
        if not self.quitting:
            self.quit(keep_music=self.store.get('close_to_tray', True))   # paused too: the tray stays to reopen

    def quit(self, keep_music=False):
        self.quitting = True
        self.engine.save_session()
        self.store.set('geometry', bytes(self.saveGeometry().toBase64()).decode())
        if not keep_music:
            self.engine.core.send('quit')
        self.engine.core.flush()
        QGuiApplication.instance().quit()

    def place(self):
        """Last place and size; the first time, the right half of the screen."""
        geo = self.store.get('geometry')
        if geo and self.restoreGeometry(QByteArray.fromBase64(geo.encode())):
            return
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.setGeometry(area.x() + area.width() // 2, area.y(), area.width() // 2, area.height())

    def showEvent(self, e):
        if not getattr(self, '_framed', False) and QGuiApplication.platformName() == 'windows':
            self._framed = True
            frame.native_frame(self)
        QTimer.singleShot(0, lambda: self.tabs.select(self.page) if self.page in self.tabs.buttons else None)
        self.trim_timer.stop()
        super().showEvent(e)

    def hideEvent(self, e):
        self.trim_timer.start()
        super().hideEvent(e)

    def changeEvent(self, e):
        if e.type() == e.Type.WindowStateChange and hasattr(self, 'captions'):
            self.captions.buttons[1].update()     # maximise <-> restore icon
            self.grips.place() if self.grips else None
        if e.type() in (e.Type.WindowStateChange, e.Type.ActivationChange):
            if self.isMinimized() or not self.isActiveWindow():
                self.trim_timer.start()
            else:
                self.trim_timer.stop()
        super().changeEvent(e)

    def trim(self):
        """Out of sight for a while: drop cached covers and hand unused memory back to Windows (music keeps playing)."""
        if self.isActiveWindow() and not self.isMinimized():
            return
        import gc
        if not self.isVisible() or self.isMinimized():   # out of sight: covers can be drawn again later
            c = covers()
            c.pix.clear()
            c.images.clear()
            theme._cache.clear()
            self.home.let_go()               # the Now page's pictures are drawn again when it shows
        gc.collect()
        if sys.platform == 'win32':
            k = ctypes.windll.kernel32
            k.GetCurrentProcess.restype = ctypes.c_void_p
            k.SetProcessWorkingSetSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t]
            k.SetProcessWorkingSetSize(k.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1))

    # ---- settings -------------------------------------------------------------------------------------------------
    def settings(self):
        """The gear: Settings is a page of the room; the gear again (or Esc) goes back."""
        self.go('settings') if self.page != 'settings' else self.go_back()

    def go_back(self):
        """From Settings, Lyrics or Queue back to the page you came from."""
        self.go(self.back, self.playlist_id if self.back == 'playlist' else None)

    # ---- sketches -------------------------------------------------------------------------------------------------
    def next_sketch(self):
        all_ = sketches.available()
        i = next((k for k, s in enumerate(all_) if s.id == self.sketch.id), -1)
        self.swap(all_[(i + 1) % len(all_)].id)

    def _watch_sketch(self):
        """While your own sketch is in use, Drift watches its folder: every save redraws the room (an artist sees
        the change at once). Drift's own sketches are not watched."""
        self.sketch_watch = None
        if self.sketch.builtin or self.sketch.folder is None:
            return
        from PyQt6.QtCore import QFileSystemWatcher
        w = QFileSystemWatcher(self)
        w.addPath(str(self.sketch.folder))
        files = [str(f) for f in self.sketch.folder.iterdir() if f.is_file()]
        if files:
            w.addPaths(files)
        later = QTimer(self, singleShot=True, interval=450)
        later.timeout.connect(self._sketch_changed)
        w.directoryChanged.connect(lambda _p: later.start())
        w.fileChanged.connect(lambda _p: later.start())
        self.sketch_watch = (w, later, self._sketch_stamp())

    def _sketch_stamp(self):
        try:
            return tuple(sorted((f.name, f.stat().st_mtime_ns, f.stat().st_size)
                                for f in self.sketch.folder.iterdir() if f.is_file()))
        except OSError:
            return None

    def _sketch_changed(self):
        if self.quitting or self.sketch_watch is None or self._sketch_stamp() == self.sketch_watch[2]:
            return
        if sketches.find(self.sketch.id) is None:        # the folder is gone or its sketch.json does not read
            self.say(f'{self.sketch.name}: sketch.json could not be read, keeping the last good look.')
            self.sketch_watch = (*self.sketch_watch[:2], self._sketch_stamp())
            return
        self.swap(self.sketch.id, force=True, quiet=True)

    def swap(self, sketch_id, force=False, quiet=False):
        """Walk into another room: a new window is built in the new sketch at the same place, the old one dissolves
        into it and is gone. The music never notices (the core plays on)."""
        if self.quitting or (sketch_id == self.sketch.id and not force):
            return
        self.store.set('sketch', sketch_id)
        picture = self.grab()
        page = self.page if self.page in self.pages else 'home'
        page = 'playlists' if page == 'playlist' else page
        release(self.engine)
        self.quitting = True
        new = Window(self.store, self.engine)
        new.setGeometry(self.geometry())
        if self.isMaximized():
            new.showMaximized()
        else:
            new.show()
        new.go(page)
        new.catch_up()
        new.curtain(picture)
        self.hide()
        self.deleteLater()
        if not quiet:
            new.say(f'{new.sketch.name}  ·  Ctrl+T tries the next sketch')
        return new

    def catch_up(self):
        """A new window shows what already plays (the engine knows; it does not tell twice)."""
        e = self.engine
        cur = e.current
        self.bar.show_track(cur)
        self.bar.play.set_playing(e.playing)
        if cur is not None:
            self.bar._wave(*e.wave)
            self.bar._position(e.position, e.duration)
        self.bar.shuffle.setChecked(e.shuffle)
        self.bar.repeat.setChecked(e.repeat != 'off')
        self.bar.repeat.set_icon('repeat1' if e.repeat == 'one' else 'repeat')
        self.bar.volume.set_value(e.volume)
        self.bar._volume_icon(e.volume)
        self._online(e.online)
        self.home.refresh()
        self._now(cur) if cur else None
        self._place()

    def curtain(self, picture):
        """The old window's picture over the new one, fading out (520 ms)."""
        veil = QLabel(self)
        veil.setPixmap(picture)
        veil.setGeometry(self.rect())
        fx = QGraphicsOpacityEffect(veil)
        veil.setGraphicsEffect(fx)
        veil.show()
        veil.raise_()
        anim = QVariantAnimation(veil, duration=520, easingCurve=QEasingCurve(QEasingCurve.Type.InOutSine))
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.valueChanged.connect(lambda v: fx.setOpacity(float(v)))
        anim.finished.connect(veil.deleteLater)
        anim.start()

    def fly(self, track, rect):
        """A record taken from the crate flies to the player strip."""
        if self.page in ('home', 'calm') or not self.bar.isVisible() or not hasattr(self.look, 'record'):
            return
        from .crate import Flight
        target = QRect(self.bar.cover.mapTo(self.root, QPoint(0, 0)), self.bar.cover.size())
        start = QRect(self.root.mapFromGlobal(rect.topLeft()), rect.size())
        Flight(self.root, self.look.record(track, rect.width()), start, target)
