"""Settings, a page of the room like every other (no pop-up): how Drift looks (the sketches), how it sounds, how it
plays, where your music lives, and the keys as a sheet of keycaps. Each choice is a row: what it does in a word, why
in a quiet line, a switch on the right."""
import os

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from .. import online, tasks
from . import theme
from .pages import Page, flow
from .widgets import KeyCap, Toggle, heading, label, pill

SOUND = (('sound_level', True, 'Same loudness', 'Matches song levels with room for the loudest peaks.'),
         ('sound_tone', True, 'Studio finish', 'Gently opens the upper stereo image and keeps the bass and center steady.'))
PLAYING = (('drift', None, 'Drift on', 'When the queue ends, keep playing songs you will like. Offline, your saved '
                                       'songs, by taste.'),
           ('whisper', True, 'The line being sung', 'Shown on Now, under the sky.'),
           ('save_played', None, 'Keep what I play', 'Save every song played to the end to this PC. Off: only songs '
                                                     'you save with the arrow.'),
           ('close_to_tray', True, 'Play on when closed', 'Closing the window keeps the music going from the tray, '
                                                          'using almost no memory.'))
KEYS = ((('Space',), 'play / pause'), (('Ctrl', '← →'), 'previous / next'), (('← →',), '5 s back / on'),
        (('Ctrl', '↑ ↓'), 'volume'), (('M',), 'mute'), (('Ctrl', 'L'), 'like'), (('J',), 'the best part'),
        (('F',), 'calm view'), (('Ctrl', 'F'), 'search'), (('Ctrl', 'Y'), 'lyrics'), (('Ctrl', 'Q'), 'queue'),
        (('Ctrl', '1-4'), 'pages'), (('Ctrl', 'T'), 'next sketch'), (('Esc',), 'back'),
        (('Pause',), 'player card, anywhere'))


class Row(QWidget):
    """One choice: its name, a quiet line, the switch; a hairline under it."""

    def __init__(self, title, note, switch):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 10, 4, 12)
        lay.setSpacing(24)
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(label(title, 10.5, 600))
        quiet = label(note, 9.5, 400, theme.MUTED)
        quiet.setWordWrap(True)
        words.addWidget(quiet)
        lay.addLayout(words, 1)
        lay.addWidget(switch, 0, Qt.AlignmentFlag.AlignVCenter)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(QColor(255, 255, 255, 14))
        p.drawLine(QRectF(self.rect()).bottomLeft(), QRectF(self.rect()).bottomRight())


class SettingsPage(Page):
    def __init__(self, win):
        super().__init__('Settings', 'Drift · how it looks, sounds and plays')
        self.win = win
        b = self.body
        b.setSpacing(10)
        self.section('Look', 'Sunset, Vinyl and Paper Theatre come with Drift. Draw or import a sketch to give '
                             'the player and song lists a room of their own.')
        self.wall = QVBoxLayout()
        b.addLayout(self.wall)
        self.section('Sound')
        for key, default, title, note in SOUND:
            self.choice(key, default, title, note, lambda _on: win.engine.send_sound())
        self.section('Playing')
        for key, default, title, note in PLAYING:
            self.choice(key, default, title, note, self._drift if key == 'drift' else None)
        self.section('Your music', 'Your own songs and the ones Drift saves live in one folder.')
        self.folder = label('', 10, 400)
        self.folder.setWordWrap(True)
        b.addWidget(self.folder)
        self.size = label('', 9.5, 400, theme.MUTED)
        b.addWidget(self.size)
        row = QHBoxLayout()
        row.setSpacing(10)
        for text, fn, ghost in (('Change folder…', self._choose, False), ('Open it', win.open_folder, True),
                                ('Look for new songs', win.rescan, True)):
            p = pill(text, ghost)
            p.clicked.connect(lambda _=False, f=fn: f())
            row.addWidget(p)
        row.addStretch()
        b.addLayout(row)
        self.section('Keys', 'Media keys work everywhere, even with the window closed.')
        b.addWidget(flow([KeyCap(k, m) for k, m in KEYS], 10))
        b.addStretch()

    def section(self, title, note=''):
        self.body.addSpacing(18)
        self.body.addWidget(heading(title, 15))
        if note:
            n = label(note, 9.5, 400, theme.MUTED)
            n.setWordWrap(True)
            self.body.addWidget(n)

    def choice(self, key, default, title, note, then=None):
        store = self.win.store
        switch = Toggle(bool(store.get(key, default) if default is not None else store.get(key)))
        switch.toggled.connect(lambda on: (store.set(key, on), then and then(on)))
        self.body.addWidget(Row(title, note, switch))
        if key == 'drift':
            self.drift_switch = switch

    def _drift(self, on):
        self.win.engine.set_drift(on)
        self.win.bar.drift.setChecked(on)

    def _choose(self):
        store = self.win.store
        path = QFileDialog.getExistingDirectory(self.win, 'Music folder', store.get('music_folder'))
        if path:
            store.set('music_folder', os.path.normpath(path))
            self.win.rescan()
            self.refresh()

    def refresh(self):
        """Fresh each time it opens: the sketch cards (which one is in use), the folder and what it holds."""
        from .gallery import gallery
        from .pages import clear
        clear(self.wall)
        self.wall.addWidget(gallery(self.win))
        self.drift_switch.blockSignals(True)
        self.drift_switch.setChecked(self.win.engine.drift)
        self.drift_switch.t = 1.0 if self.win.engine.drift else 0.0
        self.drift_switch.blockSignals(False)
        folder = self.win.store.get('music_folder')
        self.folder.setText(folder)
        self.size.setText('Counting…')
        tasks.run(online.folder_size, folder, done=self._sized)

    def _sized(self, n):
        try:
            self.size.setText(f'Saved music takes {n / 2**30:.2f} GB.' if n >= 2**28 else
                              f'Saved music takes {n / 2**20:.0f} MB.')
        except RuntimeError:
            pass        # the page was rebuilt meanwhile
