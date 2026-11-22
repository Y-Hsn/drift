"""Settings, a page of the room like every other (no pop-up): how Drift looks (the sketches), how it sounds, how it
plays, where your music lives, and the keys as a sheet of keycaps. Each choice is a row: what it does in a word, why
in a quiet line, a switch on the right."""
import os

import time

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer
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
        (('Pause',), 'player card, anywhere'), (('Ctrl', 'Alt', 'S'), 'name the song this PC plays, anywhere'))


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
        self.quiet = quiet = label(note, 9.5, 400, theme.MUTED)
        quiet.setWordWrap(True)
        words.addWidget(quiet)
        lay.addLayout(words, 1)
        lay.addWidget(switch, 0, Qt.AlignmentFlag.AlignVCenter)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(QColor(255, 255, 255, 14))
        p.drawLine(QRectF(self.rect()).bottomLeft(), QRectF(self.rect()).bottomRight())


class QrCode(QWidget):
    """A QR code drawn as squares (dark on light paper), sharp at any screen scale."""

    def __init__(self, size=232):
        super().__init__()
        self.cells = []
        self.setFixedSize(QSize(size, size))

    def set(self, text):
        import qrcode
        q = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
        q.add_data(text)
        q.make(fit=True)
        self.cells = q.get_matrix()
        self.update()

    def paintEvent(self, e):
        if not self.cells:
            return
        p = QPainter(self)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(246, 241, 232))
        p.drawRoundedRect(r, 12, 12)
        quiet = 14
        cell = (r.width() - 2 * quiet) / len(self.cells)
        p.setBrush(QColor(16, 18, 30))
        for y, row in enumerate(self.cells):
            for x, on in enumerate(row):
                if on:
                    p.drawRect(QRectF(quiet + x * cell, quiet + y * cell, cell + 0.4, cell + 0.4))


def ago(ms):
    if not ms:
        return 'not synced yet'
    s = max(0, time.time() - ms / 1000)
    if s < 90:
        return 'synced just now'
    if s < 3600:
        return f'synced {int(s // 60)} min ago'
    if s < 86400:
        return f'synced {int(s // 3600)} h ago'
    return f'synced {int(s // 86400)} days ago'


class Phones(QWidget):
    """Settings > Phones: pair a phone with a QR code (no account, no password), see paired phones, unpair one."""
    WAIT = {'fetching': 'Getting the phone link ready. The first time takes about a minute…',
            'starting': 'Opening the link to your phone…', 'off': 'Opening the link to your phone…',
            'up': 'Opening the link to your phone…',
            'failed': 'Could not open the link. Check the internet and try again.'}
    STEPS = ('Open the link it shows. On an iPhone: tap Share, then Add to Home Screen, then open Drift from the home '
             'screen. On Android: open it and tap Get the Drift app.\n\nThe code works once. Your phone gets your '
             'playlists, liked songs and radio picks, and plays them offline.')

    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        row = QHBoxLayout()
        self.pair = pill('Pair a phone')
        self.pair.clicked.connect(self._ask)
        row.addWidget(self.pair)
        row.addStretch()
        lay.addLayout(row)
        self.card = QWidget()
        card = QHBoxLayout(self.card)
        card.setContentsMargins(0, 6, 0, 6)
        card.setSpacing(22)
        self.qr = QrCode()
        card.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(6)
        self.say = label('', 10.5, 600)
        self.say.setWordWrap(True)
        words.addWidget(self.say)
        self.steps = label('', 9.5, 400, theme.MUTED)
        self.steps.setWordWrap(True)
        words.addWidget(self.steps)
        words.addStretch()
        card.addLayout(words, 1)
        self.card.hide()
        lay.addWidget(self.card)
        self.list = QVBoxLayout()
        self.list.setSpacing(0)
        lay.addLayout(self.list)
        self.expires = 0
        self.clock = QTimer(self)
        self.clock.timeout.connect(self._tick)
        win.engine.phones_listed.connect(self._listed)
        win.engine.phone_invite.connect(self._invite)

    def refresh(self):
        self.win.engine.core.send('phones')

    def _ask(self):
        self.card.show()
        self.qr.hide()
        self.say.setText(self.WAIT['starting'])
        self.steps.setText('')
        self.pair.setEnabled(False)
        self.win.engine.core.send('phone_invite')

    def _invite(self, ev):
        try:
            if 'url' in ev:
                self.qr.set(ev['url'])
                self.qr.show()
                self.expires = int(ev.get('expires') or 0)
                self.steps.setText(self.STEPS)
                self.pair.setEnabled(True)
                self.pair.setText('New code')
                self.clock.start(1000)
                self._tick()
            elif 'error' in ev:
                self.say.setText(ev['error'])
                self.pair.setEnabled(True)
            else:
                self.say.setText(self.WAIT.get(ev.get('state'), self.WAIT['starting']))
        except RuntimeError:
            pass                                     # the page was rebuilt (sketch change)

    def _tick(self):
        left = int(self.expires / 1000 - time.time())
        try:
            if left <= 0:
                self.clock.stop()
                self.qr.hide()
                self.say.setText('That code has run out.')
                self.steps.setText('Make a new one when your phone is ready.')
            else:
                self.say.setText(f"Scan this with your phone's camera · {left // 60}:{left % 60:02d}")
        except RuntimeError:
            self.clock.stop()

    def _listed(self, ev):
        from .pages import clear
        try:
            clear(self.list)
        except RuntimeError:
            return
        phones = ev.get('list') or []
        if self.qr.isVisible() and any(p.get('created', 0) / 1000 > time.time() - 600 for p in phones):
            self.clock.stop()                        # a phone just paired with the code on screen
            self.card.hide()
            self.pair.setText('Pair a phone')
        for ph in phones:
            unpair = pill('Unpair', True)
            unpair.clicked.connect(lambda _=False, d=ph['device']: self.win.engine.core.send('phone_unpair', device=d))
            size = ph.get('size') or 0
            note = ago(ph.get('seen')) + (f' · shelf {size / 2**30:.0f} GB' if size else '')
            self.list.addWidget(Row(ph.get('name') or 'Phone', note, unpair))


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
        self.speakers = pill('Change', True)
        self.speakers.clicked.connect(lambda: win.engine.core.send('devices'))
        self.speakers_row = Row('Plays through', self._speaker_words(), self.speakers)
        b.addWidget(self.speakers_row)
        win.engine.devices_listed.connect(self._speaker_menu)
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
        self.section('Phones', 'Your music on your phone, offline too: scan a code, no account or password. '
                               'The phone syncs with this PC whenever both are online.')
        self.phones = Phones(win)
        b.addWidget(self.phones)
        from .. import __version__
        self.section('Drift', f'Version {__version__}. Drift looks for a new version now and then, and asks '
                              'before it updates: one click, a quick restart, and the song carries on.')
        row = QHBoxLayout()
        look = pill('Look for updates', True)
        look.clicked.connect(lambda: win.check_update(manual=True))
        row.addWidget(look)
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

    def _speaker_words(self):
        name = self.win.store.get('output_device') or ''
        return name or 'Whatever Windows uses (it follows when you plug in headphones).'

    def _speaker_menu(self, names, chosen):
        """The core's answer to 'devices': a menu under the button, the one in use ticked."""
        from PyQt6.QtWidgets import QMenu
        try:
            if not self.speakers.isVisible():
                return
        except RuntimeError:
            return                                  # this page was rebuilt (sketch change)
        m = QMenu(self)
        for label_text, name in [('Windows default', '')] + [(n, n) for n in names]:
            act = m.addAction(label_text)
            act.setCheckable(True)
            act.setChecked(name == chosen)
            act.triggered.connect(lambda _=False, n=name: self._pick_speakers(n))
        m.exec(self.speakers.mapToGlobal(self.speakers.rect().bottomLeft()))

    def _pick_speakers(self, name):
        self.win.engine.set_device(name)
        self.speakers_row.quiet.setText(self._speaker_words())

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
        self.phones.refresh()
        folder =self.win.store.get('music_folder')
        self.folder.setText(folder)
        self.size.setText('Counting…')
        tasks.run(online.folder_size, folder, done=self._sized)

    def _sized(self, n):
        try:
            self.size.setText(f'Saved music takes {n / 2**30:.2f} GB.' if n >= 2**28 else
                              f'Saved music takes {n / 2**20:.0f} MB.')
        except RuntimeError:
            pass        # the page was rebuilt meanwhile
