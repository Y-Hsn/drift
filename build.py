"""Build Drift.exe and install it.  python build.py [--no-install]

1. draws the icon (assets/drift.ico), 2. builds the music core (core/, Rust) and the window (PyInstaller one-folder,
windowed, no terminal) -> dist/Drift, 3. closes a running Drift and its core, copies it to %LOCALAPPDATA%/Programs/Drift, refreshes the Start menu + Desktop shortcuts,
   starts it again. Each run replaces the installed copy: this is how Drift gets updated."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INSTALL = Path(os.environ['LOCALAPPDATA']) / 'Programs' / 'Drift'
EXE = INSTALL / 'Drift.exe'
EXCLUDE = ['tkinter', 'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets',
           'PyQt6.QtQuick', 'PyQt6.QtPdf', 'PyQt6.Qt3DCore', 'PyQt6.QtBluetooth', 'PyQt6.QtPositioning',
           'PyQt6.QtSql', 'PyQt6.QtTest', 'PyQt6.QtOpenGL', 'PyQt6.QtDesigner', 'PyQt6.QtHelp', 'numpy', 'PIL',
           'matplotlib', 'scipy', 'pandas', 'IPython', 'Cryptodome', 'websockets', 'brotli',
           'PyQt6.QtMultimedia', 'PyQt6.QtMultimediaWidgets', 'PyQt6.QtSpatialAudio',   # the core makes the sound
           'pygame', 'cryptography', 'bcrypt', 'OpenSSL', 'secretstorage', 'curl_cffi']   # optional extras of requests/yt-dlp
SKETCH_FILES = ('README.md', 'guide.html', 'starter.js')
KEEP_QT = ('QtCore', 'QtGui', 'QtWidgets', 'QtNetwork', 'QtSvg', 'QtQml')   # QtQml: only QJSEngine, for sketch.js
ENTRY = """import sys
if '--net' in sys.argv:            # a short helper for the music core: no window
    from drift.helper import main
    sys.exit(main(sys.argv[1:]))
from drift.app import main
sys.exit(main())
"""


def icon():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from PyQt6.QtWidgets import QApplication
    sys.path.insert(0, str(ROOT))
    app = QApplication.instance() or QApplication([])  # noqa: F841
    from drift.app import app_icon
    ico = app_icon()
    out = ROOT / 'assets'
    out.mkdir(exist_ok=True)
    # an .ico holding PNG frames (Windows Vista+): 16..256
    frames = []
    for size in (16, 24, 32, 48, 64, 128, 256):
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        ico.pixmap(size, size).save(buf, 'PNG')
        frames.append((size, bytes(data)))
    import struct
    head = struct.pack('<HHH', 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries, blobs = b'', b''
    for size, png in frames:
        entries += struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blobs += png
    (out / 'drift.ico').write_bytes(head + entries + blobs)
    return out / 'drift.ico'


def build_core():
    home = Path(os.environ.get('CARGO_HOME') or Path.home() / '.cargo')
    cargo = home / 'bin' / 'cargo.exe'
    # source paths end up in panic messages: keep the builder's user folder out of the shipped .exe
    # (the encoded form, split on 0x1f, so a folder name with a space survives)
    env = {**os.environ, 'CARGO_ENCODED_RUSTFLAGS': f'--remap-path-prefix={home}=cargo\x1f--remap-path-prefix={ROOT}=drift'}
    env.pop('RUSTFLAGS', None)
    subprocess.run([str(cargo), 'build', '--release'], check=True, cwd=ROOT / 'core', env=env, creationflags=0x00004000)
    return ROOT / 'core' / 'target' / 'release' / 'drift-core.exe'


def build(ico):
    core = build_core()
    entry = ROOT / 'Drift.pyw'
    entry.write_text(ENTRY, encoding='utf-8')
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level', 'WARN', '--windowed',
            '--onedir', '--name', 'Drift', '--icon', str(ico), '--add-data', f'{ico};assets',
            '--collect-data', 'ytmusicapi', '--collect-submodules', 'yt_dlp.extractor',
            '--collect-data', 'yt_dlp_ejs', '--collect-submodules', 'yt_dlp_ejs',   # YouTube's challenge solver scripts
            '--distpath', str(ROOT / 'dist'), '--workpath', str(ROOT / 'build'), '--specpath', str(ROOT / 'build')]
    for m in EXCLUDE:
        args += ['--exclude-module', m]
    for name in SKETCH_FILES:
        args += ['--add-data', f'{ROOT / "drift" / "ui" / "sketches" / name};drift/ui/sketches']
    subprocess.run(args + [str(entry)], check=True, cwd=ROOT,
                   creationflags=0x00004000)   # BELOW_NORMAL_PRIORITY_CLASS: the PC stays smooth while it builds
    shutil.copy2(core, ROOT / 'dist' / 'Drift' / 'drift-core.exe')
    for name in ('qjs.exe', 'qjs-LICENSE.txt'):   # QuickJS-ng 0.18.0 (MIT), see online.js_runtimes
        shutil.copy2(ROOT / 'vendor' / name, ROOT / 'dist' / 'Drift' / name)
    prune(ROOT / 'dist' / 'Drift' / '_internal')
    return ROOT / 'dist' / 'Drift'


def prune(internal):
    """QtQml pulls in all of Qt Quick, 3D, PDF, Multimedia... (~90 MB) that Drift never loads. Keep only the Qt DLLs
    the modules in KEEP_QT and the plugins really import (read from their import tables), drop the QML files and the
    Qt translations (Drift's words are its own)."""
    try:
        import pefile
    except ImportError:
        print('prune skipped (pip install pefile)')
        return
    qt = internal / 'PyQt6' / 'Qt6'
    formats = qt / 'plugins' / 'imageformats'
    for junk in (*(formats / f'{n}.dll' for n in ('qpdf', 'qtiff', 'qicns', 'qtga', 'qwbmp')),
                 qt / 'plugins' / 'tls' / 'qopensslbackend.dll', qt / 'qml', qt / 'translations'):
        shutil.rmtree(junk, ignore_errors=True) if junk.is_dir() else junk.unlink(missing_ok=True)
    dlls = {p.name.lower(): p for p in (qt / 'bin').glob('*.dll')}
    todo = [internal / 'PyQt6' / f'{m}.pyd' for m in KEEP_QT] + list((qt / 'plugins').rglob('*.dll'))
    need = set()
    while todo:
        pe = pefile.PE(str(todo.pop()), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
        for d in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', []):
            name = d.dll.decode().lower()
            if name in dlls and name not in need:
                need.add(name)
                todo.append(dlls[name])
        pe.close()
    for name, path in dlls.items():
        if name not in need:
            path.unlink()
    for pyd in (internal / 'PyQt6').glob('Qt*.pyd'):
        if pyd.stem not in KEEP_QT:
            pyd.unlink()


def close_running():
    """Ask a running Drift to quit (it saves where you were), so its files can be replaced."""
    from PyQt6.QtNetwork import QLocalSocket
    from drift.app import PIPE
    s = QLocalSocket()
    s.connectToServer(PIPE)
    if s.waitForConnected(300):
        s.write(b'quit')
        s.flush()
        s.waitForBytesWritten(500)
        for _ in range(50):
            time.sleep(0.2)
            probe = QLocalSocket()
            probe.connectToServer(PIPE)
            if not probe.waitForConnected(100):
                break
        time.sleep(0.5)
        quit_core()
        return True
    return quit_core()


def quit_core():
    """The core plays on without the window: stop it too, or its .exe cannot be replaced."""
    import json
    import socket
    from drift.core import read_port
    info = read_port()
    if not info:
        return False
    port, token, _pid = info
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=2) as s:
            s.sendall((json.dumps({'token': token, 'cmd': 'quit'}) + '\n').encode())
    except OSError:
        return False
    for _ in range(30):
        time.sleep(0.1)
        if not read_port():
            break
    time.sleep(0.3)
    return True


def shortcut(path, target):
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{path}');$s.TargetPath='{target}';"
          f"$s.WorkingDirectory='{target.parent}';$s.IconLocation='{target},0';$s.Description='Drift music player';$s.Save()")
    subprocess.run(['powershell', '-NoProfile', '-Command', ps], check=True, creationflags=0x08000000)


def install(dist):
    was_running = close_running()
    if INSTALL.exists():
        shutil.rmtree(INSTALL / '_internal', ignore_errors=True)
    shutil.copytree(dist, INSTALL, dirs_exist_ok=True)
    start = Path(os.environ['APPDATA']) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'Drift.lnk'
    shortcut(start, EXE)
    desktop = Path(os.environ['USERPROFILE']) / 'OneDrive' / 'Desktop'
    if not desktop.is_dir():
        desktop = Path(os.environ['USERPROFILE']) / 'Desktop'
    shortcut(desktop / 'Drift.lnk', EXE)
    pinned = Path(os.environ['APPDATA']) / 'Microsoft' / 'Internet Explorer' / 'Quick Launch' / 'User Pinned' / 'TaskBar' / 'Drift.lnk'
    if pinned.exists():
        shortcut(pinned, EXE)      # an existing taskbar pin keeps pointing at the new build
    return was_running


if __name__ == '__main__':
    ico = icon()
    dist = build(ico)
    print('built', dist)
    if '--no-install' not in sys.argv:
        install(dist)
        # through Explorer, like a double-click: started straight from a hidden shell the window stayed invisible
        subprocess.Popen(['explorer.exe', str(EXE)], cwd=str(INSTALL))
        print('installed', EXE)
