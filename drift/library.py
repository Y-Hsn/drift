"""Local files: scan the music folder (only new or changed files are read), tags, and embedded cover art."""
import os
import re
from pathlib import Path

from .store import Track, split_artist

EXTENSIONS = {'.mp3', '.flac', '.wav', '.ogg', '.oga', '.m4a', '.aac', '.opus', '.wma', '.webm', '.mp4'}
_YT_ID = re.compile(r'\[([A-Za-z0-9_-]{11})\]\.[^.]+$')


def recycle(path):
    """Move a file to the Recycle Bin (it can be put back from there). Raises OSError when Windows refuses,
    e.g. while the file is open."""
    import ctypes
    from ctypes import wintypes

    class _Op(ctypes.Structure):
        _fields_ = [('hwnd', wintypes.HWND), ('wFunc', wintypes.UINT), ('pFrom', wintypes.LPCWSTR),
                    ('pTo', wintypes.LPCWSTR), ('fFlags', ctypes.c_ushort), ('fAnyOperationsAborted', wintypes.BOOL),
                    ('hNameMappings', ctypes.c_void_p), ('lpszProgressTitle', wintypes.LPCWSTR)]

    path = os.path.abspath(path)
    if not os.path.exists(path):
        return
    # FO_DELETE with FOF_ALLOWUNDO (= Recycle Bin) | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    op = _Op(None, 3, path + '\0', None, 0x40 | 0x10 | 0x4 | 0x400, False, None, None)
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if code or op.fAnyOperationsAborted or os.path.exists(path):
        raise OSError(f'Windows could not move {os.path.basename(path)} to the Recycle Bin (code {code}).')


def uid_for(path):
    """Songs Drift saved carry their YouTube id in the name, so the saved file and the online song are one."""
    m = _YT_ID.search(str(path))
    return f'yt:{m.group(1)}' if m else 'file:' + os.path.normcase(str(path))


def read_tags(path):
    """(title, artist, album, duration ms) from the file's tags; the name fills what the tags lack."""
    title = artist = album = ''
    duration = 0
    try:
        from mutagen import File
        audio = File(path, easy=True)
        if audio is not None:
            if audio.info is not None:
                duration = int((audio.info.length or 0) * 1000)
            get = lambda k: str((audio.get(k) or [''])[0]).strip()  # noqa: E731
            title, artist, album = get('title'), get('artist'), get('album')
    except Exception:
        pass  # a broken tag never hides a playable file
    if not title:
        title, guessed = split_artist(Path(path).stem)
        artist = artist or guessed
    elif not artist:
        title, artist = split_artist(title)
    return title, artist, album, duration


def scan(folder, store, stop=lambda: False):
    """Walk `folder`; returns the number of songs found. Unchanged files (same mtime) are not opened again."""
    folder = Path(folder)
    if not folder.is_dir():
        return 0
    known = {t.path: t for t in store.query("path is not null and path != ''")}
    seen = set()
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if stop():
                return len(seen)
            if os.path.splitext(name)[1].lower() not in EXTENSIONS or name.endswith('.part'):
                continue
            path = os.path.join(root, name)
            seen.add(path)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            old = known.get(path)
            if old and abs(old.mtime - mtime) < 1:
                continue
            title, artist, album, duration = read_tags(path)
            uid = uid_for(path)
            source = 'youtube' if uid.startswith('yt:') else 'file'
            store.upsert(Track(uid, title, artist, album or Path(root).name if root != str(folder) else album, duration,
                               path=path, source=source, mtime=mtime,
                               page=f'https://www.youtube.com/watch?v={uid[3:]}' if source == 'youtube' else None))
    store.forget_missing(seen)
    return len(seen)


def cover_bytes(path):
    """Embedded picture of a local file, else cover.jpg/folder.jpg beside it; None when there is none."""
    try:
        from mutagen import File
        audio = File(path)
        if audio is not None:
            if getattr(audio, 'pictures', None):
                return audio.pictures[0].data
            tags = getattr(audio, 'tags', None)
            if tags:
                if 'covr' in tags and tags['covr']:
                    return bytes(tags['covr'][0])
                for key in tags.keys():
                    if str(key).startswith('APIC'):
                        return tags[key].data
                for key in ('metadata_block_picture',):
                    if key in tags:
                        import base64
                        from mutagen.flac import Picture
                        return Picture(base64.b64decode(tags[key][0])).data
    except Exception:
        pass
    for name in ('cover.jpg', 'cover.png', 'folder.jpg', 'front.jpg'):
        p = Path(path).parent / name
        if p.is_file():
            try:
                return p.read_bytes()
            except OSError:
                pass
    return None


def write_tags(path, track, cover=None):
    """Title, artist and cover into a file Drift saved (m4a/mp3/opus); best effort."""
    try:
        from mutagen import File
        ext = Path(path).suffix.lower()
        if ext in ('.m4a', '.mp4'):
            from mutagen.mp4 import MP4, MP4Cover
            a = MP4(path)
            a['\xa9nam'], a['\xa9ART'] = [track.title], [track.artist]
            if cover:
                a['covr'] = [MP4Cover(cover, MP4Cover.FORMAT_JPEG if cover[:2] == b'\xff\xd8' else MP4Cover.FORMAT_PNG)]
            a.save()
        elif ext == '.mp3':
            from mutagen.id3 import APIC, ID3, TIT2, TPE1, ID3NoHeaderError
            try:
                a = ID3(path)
            except ID3NoHeaderError:
                a = ID3()
            a.add(TIT2(encoding=3, text=track.title))
            a.add(TPE1(encoding=3, text=track.artist))
            if cover:
                a.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover))
            a.save(path)
        else:
            a = File(path, easy=True)
            if a is not None:
                a['title'], a['artist'] = track.title, track.artist
                a.save()
    except Exception:
        pass
