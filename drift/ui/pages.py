"""The pages besides Now (Now is the landscape, scape.py): Explore (Discover, your mixes, what you played lately, picks
for you, moods; and search everywhere), song lists, Queue and Lyrics. Pages leave room at the top for the index and
at the bottom for the horizon (both float over them)."""
import random
import time
from threading import Event

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter
from PyQt6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QScrollArea, QSizePolicy,
                             QVBoxLayout, QWidget)

from .. import hear, links, online, taste, tasks
from ..store import matching, norm
from . import theme
from .discover import DiscoverView
from .lyricsfeed import shared as lyrics_feed
from .stage import CoverView
from .widgets import Chip, MixTile, Quiet, draw_quiet, eyebrow, heading, label, pill, search_box

TOP_ROOM, BOTTOM_ROOM = 62, 124

MOODS = ['Chill', 'Focus', 'Workout', 'Party', 'Lo-fi beats', 'Love songs', 'Rap français', 'Afrobeats', 'Raï',
         'Throwback 2000s', 'Jazz', 'Classical', 'Deep house', 'Acoustic']


def flow(widgets, spacing=8):
    """A wrapping row of chips."""
    box = QWidget()
    lay = _Flow(box, spacing)
    for w in widgets:
        lay.addWidget(w)
    return box


from PyQt6.QtWidgets import QLayout  # noqa: E402
from PyQt6.QtCore import QPoint, QRect, QSize  # noqa: E402


class _Flow(QLayout):
    def __init__(self, parent, spacing):
        super().__init__(parent)
        self.items, self.sp = [], spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self.items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, rect, test):
        x, y, line = rect.x(), rect.y(), 0
        for it in self.items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() and line:
                x, y, line = rect.x(), y + line + self.sp, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.sp
            line = max(line, hint.height())
        return y + line - rect.y()


class Header(QWidget):
    """Every page opens alike: spaced capitals in the song's accent saying what this is and how much (the line Now
    sets over the song's name), the title in the serif, a quiet note when there is one; a field may sit on the right."""

    def __init__(self, title, kind='', right=None):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.kind = kind
        self.eyebrow = eyebrow(kind)
        self.title = label(title, 26, 700, display=True)
        self.note = label('', 10, 400, theme.MUTED)
        self.note.setWordWrap(True)
        self.note.hide()
        for w in (self.eyebrow, self.title, self.note):
            col.addWidget(w)
        row.addLayout(col, 1)
        if right is not None:
            row.addWidget(right, 0, Qt.AlignmentFlag.AlignBottom)

    def say(self, eyebrow_=None, note=None):
        if eyebrow_ is not None:
            self.eyebrow.setText(eyebrow_.upper())
        if note is not None:
            self.note.setText(note)
            self.note.setVisible(bool(note))


def songs_line(tracks):
    """'40 songs · 2 h 40 min'"""
    n = len(tracks)
    total = sum(t.duration or 0 for t in tracks) // 60000
    span = f' · {total // 60} h {total % 60} min' if total >= 60 else f' · {total} min' if total else ''
    return f'{n} song{"s" if n != 1 else ""}{span}'


def daypart():
    """'Tuesday evening'"""
    h = time.localtime().tm_hour
    part = 'morning' if 5 <= h < 12 else 'afternoon' if 12 <= h < 18 else 'evening' if h < 23 else 'night'
    return f'{time.strftime("%A")} {part}' if h >= 5 else f'{time.strftime("%A")} night'


class Page(QScrollArea):
    """A scrolling page that opens with a Header."""

    def __init__(self, title='', kind=''):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(40, TOP_ROOM, 40, BOTTOM_ROOM)
        self.body.setSpacing(14)
        self.header = Header(title, kind)
        self.heading = self.header.title
        self.body.addWidget(self.header)
        self.setWidget(body)


def clear(layout):
    while layout.count():
        it = layout.takeAt(0)
        if it.widget():
            it.widget().deleteLater()
        elif it.layout():
            clear(it.layout())


def wire_list(view, win):
    view.play.connect(win.play_from)
    view.like.connect(win.engine.like)
    view.keep.connect(win.keep_toggle)
    view.menu.connect(win.track_menu)
    if hasattr(view, 'flew'):            # a crate: the record flies to the player
        view.flew.connect(win.fly)
    return view


# ---- explore ---------------------------------------------------------------------------------------------------
class LinkCard(QWidget):
    """A pasted playlist or album: its name and description, and what to do with it (listen, keep it as a Drift
    playlist, or keep it and download every song)."""

    def __init__(self):
        super().__init__()
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 6, 0, 4)
        col.setSpacing(4)
        self.eyebrow = eyebrow('')
        self.title = label('', 20, 700, display=True)
        self.title.setWordWrap(True)
        self.note = label('', 10, 400, theme.MUTED)
        self.note.setWordWrap(True)
        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 0)
        row.setSpacing(10)
        self.listen = pill('Listen')
        self.keep = pill('Save as playlist', ghost=True)
        self.download = pill('Save and download', ghost=True)
        for b in (self.listen, self.keep, self.download):
            row.addWidget(b)
        row.addStretch()
        for w in (self.eyebrow, self.title, self.note):
            col.addWidget(w)
        col.addLayout(row)

    def show_heard(self, about):
        """A song named by ear: its name and artist; listen to it or keep it for offline."""
        self.show_about({**about, 'total': 0})
        self.eyebrow.setText('HEARD ON THIS PC')
        self.keep.hide()
        self.download.setText('Save for offline')

    def show_about(self, about):
        kind = 'album' if about.get('kind') == 'album' else 'playlist'
        total = about.get('total') or 0
        self.eyebrow.setText(' · '.join(filter(None, [f"{about.get('source') or ''} {kind}".strip(),
                                                      f'{total} songs' if total else ''])).upper())
        self.title.setText(about.get('name') or kind.title())
        self.note.setText(about.get('note') or '')
        self.note.setVisible(bool(about.get('note')))
        self.keep.show()
        self.listen.setText('Listen')
        self.keep.setText('Save as playlist')
        self.download.setText('Save and download')
        for b in (self.listen, self.keep, self.download):
            b.setEnabled(True)
        self.show()


class ExplorePage(Page):
    moods_cache = None

    def __init__(self, win):
        super().__init__(win.look.word('explore', 'Explore'))
        self.win = win
        self.picks = []
        self.picks_t = 0
        self.query = ''
        self._stamp = 0
        self._link_cancel = None
        self.search = search_box('Search music or paste a Spotify, YouTube or SoundCloud link')
        self.search.returnPressed.connect(lambda: self.run(self.search.text()))
        self.search.textChanged.connect(self._search_edited)
        self.body.addWidget(self.search)
        self.sources = QButtonGroup(self)
        self.sources.setExclusive(True)
        chips = []
        current = win.store.get('source', 'ytmusic')
        for key, name in online.SOURCES:
            c = Chip(name, True)
            c.setProperty('key', key)
            c.setChecked(key == current)
            self.sources.addButton(c)
            chips.append(c)
        self.sources.buttonClicked.connect(self._source)
        self.chips = QWidget()
        srow = QHBoxLayout(self.chips)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.setSpacing(8)
        for c in chips:
            srow.addWidget(c)
        srow.addStretch()
        self.say_btn = pill('Say it', ghost=True)
        self.say_btn.setToolTip('Say or sing a few words you remember: Windows voice typing writes them here')
        self.say_btn.clicked.connect(self.say_words)
        self.hear_btn = pill('Hear this PC', ghost=True)
        self.hear_btn.setToolTip('Names the song this PC is playing (a video, a film...). Ctrl+Alt+S does it anywhere')
        self.hear_btn.clicked.connect(self.listen)
        srow.addWidget(self.say_btn)
        srow.addWidget(self.hear_btn)
        self._hear = None          # this listen: named yet, tries waiting for an answer
        self._voice = QTimer(self)  # voice typing: search once the words stop coming
        self._voice.setSingleShot(True)
        self._voice.setInterval(1800)
        self._voice_on = False
        self._voice.timeout.connect(lambda: self.search.text().strip() and self.run(self.search.text()))
        self.search.textChanged.connect(lambda _t: self._voice_on and self._voice.start())
        self.body.addWidget(self.chips)
        self.status = label('', 10, 400, theme.MUTED)
        self.status.setWordWrap(True)
        self.card = LinkCard()
        self.card.hide()
        self.card.listen.clicked.connect(self._link_listen)
        self.card.keep.clicked.connect(self._link_keep)
        self.card.download.clicked.connect(self._link_download)
        self.body.addWidget(self.card)
        self.body.addWidget(self.status)
        self._link = None        # the pasted list being read: about, songs found, what the user chose
        self.nothing = Quiet()
        self.nothing.hide()
        self.body.addWidget(self.nothing)
        self.actions = QWidget()
        al = QHBoxLayout(self.actions)
        al.setContentsMargins(0, 0, 0, 0)
        play_all = pill('Play all')
        play_all.clicked.connect(lambda: self.results.tracks() and self.win.engine.play_list(self.results.tracks(), False))
        radio = pill('Start radio', ghost=True)
        radio.clicked.connect(lambda: self.results.tracks() and self.win.engine.start_radio(seed=self.results.tracks()[0]))
        al.addWidget(play_all)
        al.addWidget(radio)
        self.resume = pill('Return to previous queue', ghost=True)
        self.resume.clicked.connect(self._resume_queue)
        al.addWidget(self.resume)
        al.addStretch()
        self.actions.hide()
        self.body.addWidget(self.actions)
        self.results = wire_list(win.look.track_list(), win)
        self.results.hide()
        self.body.addWidget(self.results)
        self.ideas = QWidget()
        il = QVBoxLayout(self.ideas)
        il.setContentsMargins(0, 8, 0, 0)
        il.setSpacing(12)
        self.ideas_layout = il
        self.body.addWidget(self.ideas)
        self.body.addStretch()

    def _stop_link(self):
        """A new search stops reading the pasted link, unless it is being saved: that one finishes on its own."""
        if self._link_cancel and not (self._link and self._link['pid']):
            self._link_cancel.set()

    def _search_edited(self, text):
        if text.strip() != self.query:
            self._stamp = 0
            self._stop_link()
        if not text.strip():
            self._reset()

    def _reset(self):
        self.query = ''
        self._stamp = 0
        self.results.hide()
        self.actions.hide()
        self.nothing.hide()
        self.card.hide()
        self.status.setText('')
        self.ideas.show()

    def _found(self, items, line, none=('Nothing found', 'Try other words or another source.')):
        """Show a search's songs (or the quiet horizon when there are none)."""
        self.results.set_tracks(items)
        self.results.setVisible(bool(items))
        self.actions.setVisible(bool(items))
        self.resume.setVisible(self.win.engine.can_resume_link)
        self.nothing.setVisible(not items)
        self.nothing.say(*none)
        self.status.setText(line if items else '')

    def _resume_queue(self):
        self.win.engine.resume_before_link()
        self.resume.hide()

    def _source(self, button):
        self.win.store.set('source', button.property('key'))
        if self.query and not online.is_url(self.query):
            self.run(self.query)

    def source(self):
        b = self.sources.checkedButton()
        return b.property('key') if b else 'ytmusic'

    def refresh(self):
        clear(self.ideas_layout)
        on = self.win.engine.online
        self.header.say(f'{daypart()} · search everywhere' if on else 'Offline · searching this PC')
        self.chips.setVisible(on)
        self.search.setPlaceholderText('Search music or paste a Spotify, YouTube or SoundCloud link' if on
                                       else 'Search the songs saved on this PC')
        if not on:
            self._offline()
            return
        known = self.win.store.query('1')
        artists = [a for a, _ in taste.top_artists(known, 10)]
        current = self.win.engine.current
        seeds = artists or ([current.artist] if current is not None and current.artist else [])
        if seeds:
            view = DiscoverView(seeds, {norm(t.artist) for t in known if t.artist})
            view.play.connect(self.win.play_from)
            view.menu.connect(self.win.track_menu)
            self.ideas_layout.addWidget(view)
        self._feed(known)
        if artists:
            self.ideas_layout.addWidget(heading('From your taste'))
            chips = []
            for a in artists:
                c = Chip(a)
                c.clicked.connect(lambda _=False, a=a: self.run(a))
                chips.append(c)
            self.ideas_layout.addWidget(flow(chips))
        holder = QVBoxLayout()
        holder.setSpacing(12)
        box = QWidget()
        box.setLayout(holder)
        holder.setContentsMargins(0, 0, 0, 0)
        self.ideas_layout.addWidget(box)
        if ExplorePage.moods_cache:
            self._moods(holder, ExplorePage.moods_cache)
        elif self.win.engine.online:
            def got(m):
                ExplorePage.moods_cache = m
                try:
                    self._moods(holder, m)
                except RuntimeError:
                    pass
            tasks.run(online.moods, done=got, failed=lambda e: self._moods(holder, [('Moods and genres', [(m, None) for m in MOODS])]))
        else:
            self._moods(holder, [('Moods and genres', [(m, None) for m in MOODS])])

    def _offline(self):
        """No internet: Explore turns to what is on this PC (mixes of your saved artists, what you played lately)."""
        saved = self.win.store.local_tracks()
        if not saved:
            self.ideas_layout.addWidget(Quiet('Offline, and nothing saved yet',
                                              'Songs you save with the arrow next to the heart play without the '
                                              'internet. They will wait for you here.'))
            return
        self._feed(saved, picks=False)

    def _feed(self, known, picks=True):
        """Your mixes (radio from your top artists), what you played lately, and picks for you."""
        store, engine = self.win.store, self.win.engine
        lay = self.ideas_layout
        artists = taste.top_artists(known, 8)
        if artists:
            lay.addWidget(heading('Your mixes' if self.win.engine.online else 'Mixes from this PC'))
            row = QHBoxLayout()
            row.setSpacing(16)
            by_artist = {}
            for t in known:
                by_artist.setdefault(t.artist, t)
            width = max(400, self.viewport().width() - 80)
            fit = max(2, min(len(artists), width // 184))
            for name, _w in artists[:fit]:
                tile = MixTile(f'{name}\nMix', name, name, by_artist.get(name))
                tile.clicked.connect(lambda n=name: engine.start_radio(artist=n))
                row.addWidget(tile)
            row.addStretch()
            w = QWidget()
            w.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(w)
        recent = store.recent(6 if picks else 30)
        if not picks:
            recent = [t for t in recent if t.local][:6]
        if recent:
            lay.addWidget(heading('Jump back in'))
            lst = wire_list(self.win.look.track_list(numbered=False), self.win)
            lst.set_tracks(recent)
            lay.addWidget(lst)
        if not picks:
            return
        head = QHBoxLayout()
        head.addWidget(heading('Picked for you'))
        head.addStretch()
        more = pill('Refresh', ghost=True)
        more.clicked.connect(lambda: self.load_picks(force=True))
        head.addWidget(more)
        hw = QWidget()
        hw.setLayout(head)
        head.setContentsMargins(0, 10, 0, 0)
        lay.addWidget(hw)
        self.picks_view = wire_list(self.win.look.track_list(numbered=False), self.win)
        self.picks_note = label('', 10, 400, theme.MUTED)
        lay.addWidget(self.picks_note)
        lay.addWidget(self.picks_view)
        if not known:
            self.picks_note.setText('Search for songs you love: Drift learns your taste from what you play.')
        else:
            self.load_picks()

    def load_picks(self, force=False):
        if self.picks and not force and time.time() - self.picks_t < 1800:
            self.picks_view.set_tracks(self.picks)
            return
        if not self.win.engine.online:
            self.picks_note.setText('Offline. Picks come back with the internet; your saved songs still play.')
            return
        known = self.win.store.query('1')
        seed = taste.pick_seed([t for t in known if t.liked or t.plays]) or (random.choice(known) if known else None)
        if not seed:
            return
        self.picks_note.setText(f'Because you like {seed.artist or seed.title}…')
        recent = {t.uid for t in self.win.store.recent(30)}
        bad = taste.disliked(known)

        def got(items):
            self.picks = [self.win.store.refresh(t) for t in items if taste.accept(t, recent, bad)][:10]
            self.picks_t = time.time()
            try:
                self.picks_view.set_tracks(self.picks)
            except RuntimeError:
                pass   # the page was rebuilt meanwhile

        tasks.run(online.related, seed, 20, done=got, failed=lambda e: self.picks_note.setText('Could not load picks.'))

    def _moods(self, holder, sections):
        for title, items in sections:
            holder.addWidget(heading(title))
            chips = []
            for name, params in items:
                c = Chip(name)
                c.clicked.connect(lambda _=False, n=name, p=params: self.run_mood(n, p) if p else self.run(n + ' songs'))
                chips.append(c)
            holder.addWidget(flow(chips))

    def run_mood(self, name, params):
        self.query = ''
        self.search.clear()
        self.nothing.hide()
        self.status.setText(f'Loading {name}…')
        self.ideas.hide()
        stamp = self._stamp = time.time()

        def got(items):
            if self._stamp == stamp:
                self._found([self.win.store.refresh(t) for t in items], f'{name} · {songs_line(items)}',
                            (f'{name} is quiet today', 'Try another mood.'))

        def bad(e):
            if self._stamp == stamp:
                self.status.setText(f'Could not load {name}. Try another one.')
                self.ideas.show()

        tasks.run(online.mood_tracks, params, done=got, failed=bad)

    def focus(self):
        self.search.setFocus()
        self.search.selectAll()

    # ---- find a song by ear: the words you remember, or what this PC is playing -------------------------------------
    def say_words(self):
        """Windows voice typing (Win+H) writes what you say or sing into the search; it runs when you stop."""
        import ctypes
        self.search.clear()
        self.search.setFocus()
        self._voice_on = True
        self.status.setText('Say or sing the words you remember…')
        press = ctypes.windll.user32.keybd_event
        for key, up in ((0x5B, 0), (0x48, 0), (0x48, 2), (0x5B, 2)):     # Win down, H down, H up, Win up
            press(key, 0, up, 0)

    def listen(self):
        if self._hear and self._hear['on']:
            self.win.engine.listen_stop()
            self.hearing(False, '')
            return
        if not self.win.engine.online:
            self.status.setText('Naming a song needs the internet.')
            return
        self.win.engine.listen()

    def hearing(self, on, why):
        if on:
            self._hear = {'on': True, 'named': False, 'waiting': 0, 'last': False}
            self._stop_link()
            self._stamp = 0
            for w in (self.ideas, self.card, self.results, self.actions, self.nothing):
                w.hide()
            self.status.setText('Listening to this PC… (a few seconds)')
        elif self._hear:
            self._hear['on'] = False
            if why and not self._hear['named']:
                self.status.setText('Could not listen: ' + why)
        self.hear_btn.setText('Stop listening' if on else 'Hear this PC')

    def heard(self, ev):
        h = self._hear
        if not h or h['named']:
            return
        last = bool(ev.get('last'))

        def answer(found):
            h['waiting'] -= 1
            if found and not h['named']:
                h['named'] = True
                self.win.engine.listen_stop()
                self._show_heard(found)
            elif h['last'] and not h['waiting'] and not h['named']:
                self._found([], '', ('Could not name it', 'Try again while the song plays clearly, without talking over it.'))

        def failed(e):
            h['waiting'] -= 1
            if not h['named']:
                h['named'] = True      # stop asking
                self.win.engine.listen_stop()
                self.status.setText(str(e) if isinstance(e, hear.Busy) else 'Could not reach the song search. Check the internet.')

        h['last'] = h['last'] or last
        if ev.get('quiet'):
            if last and not h['waiting']:
                self._found([], '', ('Nothing is playing', 'Play the video or film, then try again.'))
            return
        h['waiting'] += 1
        tasks.run(hear.recognize, ev.get('uri') or '', int(ev.get('ms') or 0), int(ev.get('at') or 0) or None,
                  done=answer, failed=failed)

    def _show_heard(self, h):
        about = {'kind': 'song', 'name': h['title'], 'source': 'Heard on this PC',
                 'note': ' · '.join(filter(None, [h['artist'], h.get('album'), h.get('year')]))}
        link = self._link = {'about': about, 'found': [], 'cancel': Event(), 'listen': False, 'playing': False,
                             'pid': None, 'download': False, 'done': False, 'single': True}
        self._link_cancel = link['cancel']
        stamp = self._stamp = time.time()
        self.ideas.hide()
        self.nothing.hide()
        self.card.show_heard(about)
        self.status.setText('Finding the full song…')
        self.win.bring_up()

        def got(track):
            link['done'] = True
            if self._stamp != stamp:
                return
            if not track:
                self.status.setText('Named it, but the full song is not online. Try the search.')
                return
            track = self.win.store.refresh(track)
            link['found'].append(track)
            self._link_took(link, [track])
            self._found([track], 'Heard on this PC')
            self.actions.hide()

        def bad(_e):
            link['done'] = True
            if self._stamp == stamp:
                self.status.setText('Named it, but could not reach the full song. Try the search.')

        tasks.run(hear.full_song, h, done=got, failed=bad)

    def run(self, query):
        self._voice_on = False
        self._voice.stop()
        query = query.strip()
        if not query:
            return
        self._stop_link()
        self.query = query
        self.search.setText(query)
        self.ideas.hide()
        self.nothing.hide()
        self.card.hide()
        if not self.win.engine.online:      # offline: the songs on this PC answer
            hits = matching(self.win.store.local_tracks(), query)
            self._found(hits, f'On this PC · {songs_line(hits)}',
                        ('Not on this PC', 'Offline, Drift searches only the songs saved here. '
                                           'Everything else comes back with the internet.'))
            return
        self.status.setText('Searching…')
        stamp = time.time()
        self._stamp = stamp

        if online.is_url(query):
            self._play_link(query, stamp)
            return

        def got(items):
            if self._stamp == stamp:
                self._found([self.win.store.refresh(t) for t in items], songs_line(items))

        def bad(e):
            if self._stamp == stamp:
                self.status.setText('Search failed: ' + str(e).splitlines()[0][:160] if str(e) else 'Search failed.')

        tasks.run(online.search, query, self.source(), 24, done=got, failed=bad)

    def _play_link(self, url, stamp):
        self.results.hide()
        self.actions.hide()
        self.status.setText('Finding a playable recording…')
        cancel = self._link_cancel = Event()
        link = self._link = {'about': None, 'found': [], 'cancel': cancel, 'listen': False, 'playing': False,
                             'pid': None, 'download': False, 'done': False}
        found = link['found']
        engine = self.win.engine

        def matched(item):
            if isinstance(item, dict):          # a playlist or album: show it and let the user choose
                link['about'] = item['about']
                if self._stamp == stamp:
                    self.card.show_about(item['about'])
                    self.status.setText('Finding the songs…')
                return
            track = self.win.store.refresh(item)
            if link['about'] is None:           # one song: it plays straight away
                if not found:
                    engine.play_link(track, cancel)
                elif engine.active_link is cancel:
                    engine.enqueue([track])
                else:
                    cancel.set()
                    return
            else:
                self._link_took(link, [track])
            found.append(track)
            if self._stamp == stamp:
                total = (link['about'] or {}).get('total')
                if link['about'] is not None:
                    self._found(found, f'{len(found)} of {total} found · Finding the rest…' if total else
                                songs_line(found) + ' · Finding the rest…')
                    self.actions.hide()
                else:
                    self.status.setText(f'{songs_line(found)} · Checking link…')

        def done(missing):
            link['done'] = True
            engine.finish_link(cancel)
            if link['pid'] and link['about']:
                self.win.say(f"{link['about']['name']}: {songs_line(found)} saved"
                             + (' and downloading.' if link['download'] else '.'))
            if self._stamp != stamp:
                return
            if found:
                note = f' · {missing} unavailable or unmatched' if missing else ''
                self._found(found, songs_line(found) + note)
                if link['about'] is not None:
                    self.actions.hide()
            else:
                self.card.hide()
                self._found([], '', ('No playable match', 'Try a direct YouTube or SoundCloud link.'))

        def failed(error):
            link['done'] = True
            engine.finish_link(cancel)
            if self._stamp == stamp:
                if found:
                    self._found(found, songs_line(found) + ' · Link loading stopped')
                    if link['about'] is not None:
                        self.actions.hide()
                else:
                    self.card.hide()
                    self.status.setText('Could not play this link. ' + str(error).split('\n')[0][:140])

        tasks.run(links.tracks_from_link, url, cancel, done=done, failed=failed, progress=matched)

    # what the user chose for a pasted list; songs found later follow the same choice
    def _link_took(self, link, tracks):
        engine, store = self.win.engine, self.win.store
        if link['pid']:
            store.add_to_playlist(link['pid'], tracks)
        if link['download']:
            for t in tracks:
                engine.save(t, quiet=True)
        if link['listen']:
            if not link['playing']:
                engine.play_link(tracks[0], link['cancel'])
                link['playing'] = True
                tracks = tracks[1:]
                if link['done']:
                    engine.finish_link(link['cancel'])
            if tracks and engine.active_link is link['cancel']:
                engine.enqueue(tracks)

    def _link_listen(self):
        link = self._link
        if not link or link['listen']:
            return
        link['listen'] = True
        self.card.listen.setText('Listening')
        self.card.listen.setEnabled(False)
        if link['found']:
            self._link_took({**link, 'pid': None, 'download': False}, list(link['found']))
            link['playing'] = True

    def _link_keep(self):
        link = self._link
        if not link or not link['about'] or link.get('single'):
            return
        if link['pid']:
            self.win.go('playlist', link['pid'])
            return
        store = self.win.store
        link['pid'] = store.new_playlist(store.free_playlist_name(link['about']['name']))
        store.set_playlist_note(link['pid'], link['about'].get('note') or '')
        if link['found']:
            store.add_to_playlist(link['pid'], link['found'])
        self.win.refresh_playlists()
        self.card.keep.setText('Open playlist')
        self.win.say(f"Saved as a playlist: {link['about']['name']}."
                     + ('' if link['done'] else ' The rest is added as it is found.'))

    def _link_download(self):
        link = self._link
        if not link or not link['about'] or link['download']:
            return
        if not link['pid'] and not link.get('single'):
            self._link_keep()
        link['download'] = True
        for t in link['found']:
            self.win.engine.save(t, quiet=True)
        self.card.download.setText('Downloading')
        self.card.download.setEnabled(False)
        self.win.say(f"Downloading {link['about']['name']} for offline.")


# ---- a list of songs (library, liked, a playlist, the queue) ------------------------------------------------------
class ListPage(QWidget):
    """A list of songs: library, liked, a playlist, the queue. `kind` heads it ('on this PC'), `empty` = (line, note)
    for the quiet horizon it shows while there are none."""

    def __init__(self, win, title, kind, empty, buttons=()):
        super().__init__()
        self.win = win
        self.all = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, TOP_ROOM, 30, BOTTOM_ROOM - 16)
        lay.setSpacing(10)
        self.filter = search_box('Filter', 36, 16)
        self.filter.setFixedWidth(220)
        self.filter.textChanged.connect(self._filter)
        self.header = Header(title, kind, self.filter)
        self.heading = self.header.title
        lay.addWidget(self.header)
        self.tools = QWidget()
        row = QHBoxLayout(self.tools)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.play = pill('Play')
        self.play.clicked.connect(lambda: self.list.tracks() and win.engine.play_list(self.list.tracks(), False))
        self.shuf = pill('Shuffle', ghost=True)
        self.shuf.clicked.connect(lambda: self.list.tracks() and win.engine.play_list(self.list.tracks(), True))
        row.addWidget(self.play)
        row.addWidget(self.shuf)
        for text, fn in buttons:
            b = pill(text, ghost=True)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addWidget(self.tools)
        self.empty = Quiet(*empty)
        lay.addWidget(self.empty, 1)    # takes the room while the list is hidden, so nothing spreads out
        self.list = wire_list(win.look.track_list(grow=False), win)
        lay.addWidget(self.list, 1)

    def set_tracks(self, tracks, note=''):
        self.all = list(tracks)
        self.header.say(f'{self.header.kind} · {songs_line(self.all)}' if self.all else self.header.kind, note)
        self._filter(self.filter.text())

    def _filter(self, text):
        shown = matching(self.all, text)
        self.list.set_tracks(shown)
        self.empty.setVisible(not self.all)
        self.list.setVisible(bool(self.all))
        self.play.setVisible(bool(self.all))
        self.shuf.setVisible(bool(self.all))
        for b in (self.play, self.shuf):
            b.setEnabled(bool(shown))


# ---- lyrics -------------------------------------------------------------------------------------------------------
class LyricsView(QWidget):
    """Synced lyrics: the line being sung sits in the middle, bright; the rest fade with distance. Click a line to jump."""
    seek = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.lines = []
        self.synced = False
        self.at = -1
        self.offset = 0.0      # smooth scroll position (in lines)
        self.note = ('', '')     # (line, quiet note) shown while there are no lines
        self.anim = QTimer(self, interval=16, timeout=self._step)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.f = theme.serif(19, 500)
        self.scroll = 0

    def set_lines(self, lines, note=('', '')):
        self.lines = lines
        self.synced = bool(lines) and lines[0][0] is not None
        self.at, self.offset, self.scroll = -1, 0.0, 0
        self.note = note
        self.update()

    def position(self, ms):
        if not self.synced:
            return
        i = -1
        for k, (t, _line) in enumerate(self.lines):
            if t <= ms + 250:
                i = k
            else:
                break
        if i != self.at:
            self.at = i
            if not self.anim.isActive():
                self.anim.start()

    def _step(self):
        target = max(0, self.at)
        self.offset += (target - self.offset) * 0.18
        if abs(target - self.offset) < 0.01:
            self.offset = target
            self.anim.stop()
        self.update()

    def wheelEvent(self, e):
        if not self.synced:
            self.scroll = max(0, self.scroll - e.angleDelta().y() // 2)
            self.update()

    def mouseReleaseEvent(self, e):
        if not self.synced:
            return
        lh = 46
        k = round(self.offset + (e.position().y() - self.height() / 2 + lh / 2) / lh - 0.5)
        if 0 <= k < len(self.lines):
            self.seek.emit(self.lines[k][0])

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(self.f)
        fm = QFontMetrics(self.f)
        w = self.width() - 20
        if not self.lines:
            title, note = self.note
            draw_quiet(p, QRectF(self.rect()).adjusted(0, self.height() / 2 - 120, 0, 0), title, note)
            return
        lh = 46
        if self.synced:
            mid = self.height() / 2
            acc = theme.accent.color
            first = max(0, int(self.offset - mid / lh) - 1)
            last = min(len(self.lines), int(self.offset + mid / lh) + 2)
            for k in range(first, last):
                y = mid + (k - self.offset) * lh
                text = self.lines[k][1] or '♪'
                d = abs(k - self.offset)
                if k == self.at:
                    p.setPen(QColor('#ffffff'))
                elif k < self.at:
                    p.setPen(QColor(acc.red(), acc.green(), acc.blue(), int(max(40, 170 - d * 25))))
                else:
                    p.setPen(QColor(255, 255, 255, int(max(30, 120 - d * 18))))
                p.drawText(QRectF(10, y - lh / 2, w, lh), Qt.AlignmentFlag.AlignVCenter,
                           fm.elidedText(text, Qt.TextElideMode.ElideRight, w))
        else:
            y = 20 - self.scroll
            p.setPen(QColor(255, 255, 255, 210))
            for _t, text in self.lines:
                if -lh < y < self.height() + lh:
                    p.drawText(QRectF(10, y, w, lh), Qt.AlignmentFlag.AlignVCenter, text)
                y += 40


class LyricsPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.uid = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(40, TOP_ROOM, 40, BOTTOM_ROOM)
        lay.setSpacing(40)
        left = QVBoxLayout()
        left.addStretch()
        self.cover = CoverView(300, 150)
        left.addWidget(self.cover)
        self.kind = eyebrow('')
        self.title = label('', 18, 700, display=True)
        self.title.setWordWrap(True)
        self.title.setMaximumWidth(300)
        self.artist = label('', 11, 400, theme.MUTED)
        left.addSpacing(16)
        left.addWidget(self.kind)
        left.addWidget(self.title)
        left.addWidget(self.artist)
        left.addStretch()
        self.left = QWidget()
        self.left.setLayout(left)
        left.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.left)
        self.view = LyricsView()
        self.view.seek.connect(win.engine.seek)
        lay.addWidget(self.view, 1)
        win.engine.position_changed.connect(lambda ms, _d: self.isVisible() and self.view.position(ms))
        win.engine.track_changed.connect(lambda t: self.isVisible() and self.show_track(t))
        lyrics_feed().ready.connect(self._lyrics)

    def resizeEvent(self, e):
        self.left.setVisible(self.width() > 760 and self.uid is not None)
        super().resizeEvent(e)

    def showEvent(self, e):
        self.show_track(self.win.engine.current)
        super().showEvent(e)

    def show_track(self, t):
        if t is None:
            self.uid = None
            self.left.hide()          # the quiet horizon sits in the middle of the page
            self.view.set_lines([], ('Nothing playing', 'Play a song and its words appear here, line by line.'))
            return
        if t.uid == self.uid:
            return
        self.uid = t.uid
        self.left.setVisible(self.width() > 760)
        self.title.setText(t.title)
        self.artist.setText(t.subtitle)
        self.cover.show_track(t)
        feed = lyrics_feed()
        if not self.win.engine.online and not feed.known(t):
            self.kind.setText('OFFLINE')
            self.view.set_lines([], ('The words are out of reach', 'Lyrics come with the internet, and stay with '
                                    'every song you save.'))
            return
        lines = feed.lines(t)
        if lines is None:
            self.kind.setText('LISTENING FOR THE WORDS')
            self.view.set_lines([], ('Listening for the words…', ''))
        else:
            self._lyrics(t.uid)

    def _lyrics(self, uid):
        if uid == self.uid:
            lines = lyrics_feed().cache.get(uid) or []
            self.kind.setText(('SUNG IN TIME' if lines[0][0] is not None else 'THE WORDS') if lines else 'NO WORDS')
            self.view.set_lines(lines, ('An instrumental, maybe', 'No lyrics were found for this song.'))
            self.view.position(self.win.engine.position)
