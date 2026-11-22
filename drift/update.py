"""Updates from GitHub Releases. As soon as a newer release is found (at most every 6 h, in the background, at low
priority) its zip is downloaded, checked against the SHA-256 GitHub publishes and unpacked into DATA/update/<version>;
a marker with the hashes says it is complete. Only then does the window offer "Restart to update" (also right at the
next start, without the network). One tap: a hidden PowerShell waits for the window and the core to close, moves the
new files into place (copies them if they are on another drive), puts the old ones back if anything fails, and starts
the new Drift, which carries on playing where the old one stopped. Your data (%LOCALAPPDATA%/Drift) is never touched.
Only for the packaged Drift.exe."""
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path

from . import __version__

if sys.platform == 'win32':
    import msvcrt

REPO = 'Y-Hsn/drift'
LATEST = f'https://api.github.com/repos/{REPO}/releases/latest'
ASSET = 'Drift-windows.zip'
EVERY = 6 * 3600
NEEDED = ('Drift.exe', 'drift-core.exe', '_internal')
VERSION = re.compile(r'[0-9]+(?:\.[0-9]+){0,2}')
SHA256 = re.compile(r'[0-9a-f]{64}')
MARKER = 'ready.json'
ARCHIVE_HASH = 'archive.sha256'
found = None                        # the verified staged release, kept when the window is rebuilt


def version_tuple(text):
    return tuple(int(n) for n in re.findall(r'\d+', text or '')[:3]) or (0,)


def app_dir():
    """The folder Drift.exe runs from (None when run from source: git is the update there)."""
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else None


def can_update():
    d = app_dir()
    if not d or not (d / 'Drift.exe').is_file():
        return False
    try:                                    # unzipped somewhere Drift may not write (Program Files): no updates
        if d.resolve().is_relative_to(folder().parent.resolve()):
            return False
        probe = d / '.drift-write-test'
        probe.write_bytes(b'')
        probe.unlink()
        return True
    except OSError:
        return False


def _get(url, timeout):
    req = urllib.request.Request(url, headers={'User-Agent': f'Drift/{__version__}',
                                               'Accept': 'application/vnd.github+json'})
    return urllib.request.urlopen(req, timeout=timeout)


def latest(timeout=8, current=__version__):
    """The newer release, as {'version', 'url', 'size', 'sha256', 'notes'}; None when up to date or unknown."""
    with _get(LATEST, timeout) as r:
        rel = json.loads(r.read(2_000_000))
    if rel.get('draft') or rel.get('prerelease'):
        return None
    version = (rel.get('tag_name') or '').lstrip('vV')
    if not VERSION.fullmatch(version) or version_tuple(version) <= version_tuple(current):
        return None
    asset = next((a for a in rel.get('assets') or [] if a.get('name') == ASSET), None)
    digest = (asset or {}).get('digest') or ''
    if not asset or not digest.startswith('sha256:') or not SHA256.fullmatch(digest[7:].lower()):
        return None                         # never install what cannot be checked
    return {'version': version, 'url': asset['browser_download_url'], 'size': int(asset.get('size') or 0),
            'sha256': digest.split(':', 1)[1].lower(), 'notes': (rel.get('body') or '').strip()}


def folder():
    from .store import DATA
    return DATA / 'update'


@contextmanager
def _staging():
    """Give staging low CPU and disk priority. Lock it across quick closes and reopens."""
    base = folder()
    base.mkdir(parents=True, exist_ok=True)
    if sys.platform != 'win32':
        yield base
        return
    with (base / 'stage.lock').open('a+b') as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('An update is already being prepared.') from exc
        kernel = ctypes.windll.kernel32
        thread = ctypes.c_void_p(kernel.GetCurrentThread())
        background = kernel.SetThreadPriority(thread, 0x00010000)   # THREAD_MODE_BACKGROUND_BEGIN
        try:
            yield base
        finally:
            if background:
                kernel.SetThreadPriority(thread, 0x00020000)      # THREAD_MODE_BACKGROUND_END
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _hash(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def _tree_hash(new):
    if new.is_symlink() or new.is_junction():
        raise RuntimeError('The unpacked update links outside its folder.')
    digest = hashlib.sha256()
    for path in sorted(new.rglob('*')):
        if path.is_symlink() or path.is_junction():
            raise RuntimeError('An update file links outside its folder.')
        digest.update(json.dumps(path.relative_to(new).as_posix()).encode())
        digest.update(_hash(path).encode() if path.is_file() else b'/')
    return digest.hexdigest()


def _payload(new):
    exe = min(new.rglob('Drift.exe'), key=lambda p: (len(p.parts), p), default=None)
    if not exe or not all((exe.parent / name).is_file() for name in NEEDED[:2]) or not (
        exe.parent / NEEDED[2]
    ).is_dir():
        raise RuntimeError('the zip is not a whole Drift')
    return exe.parent


def _ready(stage, sha, path):
    return {'version': stage.name, 'sha256': sha, 'path': path}


def _verified(stage, expected_sha=None):
    try:
        info = json.loads((stage / MARKER).read_text(encoding='utf-8'))
        if info['version'] != stage.name or not SHA256.fullmatch(info['sha256']):
            return None
        if expected_sha is not None and info['sha256'] != expected_sha:
            return None
        if _hash(stage / ASSET) != info['sha256'] or _tree_hash(stage / 'new') != info['tree_sha256']:
            return None
        return _ready(stage, info['sha256'], _payload(stage / 'new'))
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        return None


def _versions(base):
    return sorted((p for p in base.iterdir() if VERSION.fullmatch(p.name) and p.is_dir()
                   and not p.is_symlink() and not p.is_junction()),
                  key=lambda p: version_tuple(p.name), reverse=True)


def _clean_older(base, version):
    for stage in _versions(base):
        if version_tuple(stage.name) < version_tuple(version):
            shutil.rmtree(stage, ignore_errors=True)


def _recover(stage):
    if (stage / MARKER).exists():
        return _verified(stage)
    try:
        sha = (stage / ARCHIVE_HASH).read_text(encoding='ascii')
        if SHA256.fullmatch(sha) and _hash(stage / ASSET) == sha:
            return _ready(stage, sha, _finish_stage(stage, sha))
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile):
        pass
    return None


def staged(current=__version__):
    """Find a ready update using only local files, checking both the archive and the unpacked files."""
    if not folder().is_dir():
        return None
    with _staging() as base:
        for stage in _versions(base):
            if version_tuple(stage.name) <= version_tuple(current):
                shutil.rmtree(stage, ignore_errors=True)
            elif info := _recover(stage):
                _clean_older(base, stage.name)
                return info
    return None


def _download(info, zpath, progress, timeout):
    partial = zpath.with_suffix('.part')
    h, done = hashlib.sha256(), 0
    with _get(info['url'], timeout) as r, partial.open('wb') as out:
        while chunk := r.read(256 * 1024):
            out.write(chunk)
            h.update(chunk)
            done += len(chunk)
            progress(done / info['size'] if info['size'] else 0.0)
    if info['size'] and done != info['size']:
        raise RuntimeError('the download was cut short')
    if h.hexdigest() != info['sha256']:
        raise RuntimeError('the download does not match the release (SHA-256)')
    partial.replace(zpath)


def _unpack(zpath, new):
    root = new.resolve()
    with zipfile.ZipFile(zpath) as z:
        for entry in z.infolist():
            name = entry.filename.replace('\\', '/')
            target = (new / name).resolve()
            if ':' in name or not target.is_relative_to(root) or target == root:
                raise RuntimeError('the zip has a file outside its folder')
            if name.endswith('/'):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(entry) as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst)


def _finish_stage(stage, sha):
    new = stage / 'new'
    if new.exists():
        shutil.rmtree(new)
    _unpack(stage / ASSET, new)
    payload = _payload(new)
    marker = stage / MARKER
    pending = marker.with_suffix('.tmp')
    pending.write_text(json.dumps({'version': stage.name, 'sha256': sha, 'tree_sha256': _tree_hash(new)}),
                       encoding='utf-8')
    pending.replace(marker)
    return payload


def fetch(info, progress=lambda frac: None, timeout=20):
    """Stage the release and return the folder holding the new Drift.exe."""
    version, sha = info['version'], info['sha256']
    if not VERSION.fullmatch(version) or not SHA256.fullmatch(sha):
        raise ValueError('Invalid update version or SHA-256.')
    with _staging() as base:
        stage = base / version
        if stage.is_symlink() or stage.is_junction():
            raise RuntimeError('The update folder links outside its folder.')
        stage.mkdir(exist_ok=True)
        if ready := _verified(stage, sha):
            _clean_older(base, version)
            return ready['path']
        (stage / MARKER).unlink(missing_ok=True)
        (stage / ARCHIVE_HASH).write_text(sha, encoding='ascii')
        zpath = stage / ASSET
        if not zpath.is_file() or _hash(zpath) != sha:
            _download(info, zpath, progress, timeout)
        payload = _finish_stage(stage, sha)
        _clean_older(base, version)
        return payload


def prepare():
    """Check GitHub and stage a newer release before reporting it to the window."""
    if info := latest():
        return {**info, 'path': fetch(info)}
    return None


SWAP = r'''$ErrorActionPreference = 'Stop'
$app = '{app}'; $new = '{new}'; $stage = '{stage}'; $log = '{log}'
$sameVolume = ${same_volume}
function Log($m) {{
    Add-Content -LiteralPath $log -Value "$(Get-Date -Format s) $m" -ErrorAction SilentlyContinue
}}
function Restart-Drift {{
    if (${start}) {{ Start-Process -FilePath explorer.exe -ArgumentList ('"' + "$app\Drift.exe" + '"') }}
}}
$old = Join-Path $app ('.drift-old-' + [guid]::NewGuid())
$saved = @()
$installed = @()
try {{
    $waiting = @({pids})
    $until = (Get-Date).AddSeconds(30)
    while ($waiting.Count -and (Get-Process -Id $waiting -ErrorAction SilentlyContinue)) {{
        if ((Get-Date) -ge $until) {{ throw 'Drift did not close' }}
        Start-Sleep -Milliseconds 75
    }}
    $until = (Get-Date).AddSeconds(5)
    while ($true) {{
        try {{
            foreach ($f in 'Drift.exe', 'drift-core.exe') {{
                if ([IO.File]::Exists("$app\$f")) {{
                    [IO.File]::Open("$app\$f", 'Open', 'ReadWrite', 'None').Close()
                }}
            }}
            break
        }} catch {{
            if ((Get-Date) -ge $until) {{ throw }}
            Start-Sleep -Milliseconds 75
        }}
    }}
    $names = @(Get-ChildItem -LiteralPath $new -Force | ForEach-Object Name)
    if ('Drift.exe' -notin $names -or 'drift-core.exe' -notin $names -or '_internal' -notin $names) {{
        throw 'The staged update is incomplete'
    }}
    New-Item -ItemType Directory -Path $old | Out-Null
    foreach ($n in $names) {{
        if (Test-Path -LiteralPath "$app\$n") {{
            Move-Item -LiteralPath "$app\$n" -Destination $old
            $saved += $n
        }}
    }}
    foreach ($n in $names) {{
        $installed += $n
        if ($sameVolume) {{
            Move-Item -LiteralPath "$new\$n" -Destination $app
        }} else {{
            Copy-Item -LiteralPath "$new\$n" -Destination $app -Recurse -Force
        }}
    }}
    Restart-Drift
}} catch {{
    Log "update failed: $_"
    $restored = $true
    foreach ($n in $installed) {{
        try {{
            if (Test-Path -LiteralPath "$app\$n") {{
                if ($sameVolume) {{
                    Move-Item -LiteralPath "$app\$n" -Destination $new
                }} else {{
                    Remove-Item -LiteralPath "$app\$n" -Recurse -Force
                }}
            }}
        }} catch {{
            $restored = $false
            Log "could not remove new $n from Drift: $_"
        }}
    }}
    foreach ($n in $saved) {{
        try {{
            Move-Item -LiteralPath "$old\$n" -Destination "$app\$n"
        }} catch {{
            $restored = $false
            Log "could not restore $n from $old : $_"
        }}
    }}
    if ($restored) {{
        Log 'old Drift put back'
        Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue
        Restart-Drift
    }}
    exit 1
}}
Log 'updated to {version}'
Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
'''


def _ps(text):
    return text.replace("'", "''")


def apply(new, version, pids, app=None, start=True, wait=False):
    """Start the hidden swap; the caller quits Drift and the core right after. (app/start/wait: for tests.)"""
    app = app or app_dir()
    base = folder()
    stage = base / version
    if not VERSION.fullmatch(version) or not new.resolve().is_relative_to(stage.resolve() / 'new'):
        raise ValueError('The update is outside its staging folder.')
    data = base.parent.resolve()
    target = app.resolve()
    if target.is_relative_to(data) or any(data.is_relative_to(target / p.name) for p in new.iterdir()):
        raise ValueError('The update would replace Drift data.')
    same_volume = new.stat().st_dev == app.stat().st_dev
    script = base / 'swap.ps1'
    script.write_text(SWAP.format(app=_ps(str(app)), new=_ps(str(new)), stage=_ps(str(stage)),
                                  log=_ps(str(base.parent / 'update.log')), same_volume='true' if same_volume else 'false',
                                  pids=', '.join(str(int(p)) for p in pids if p), version=_ps(version),
                                  start='true' if start else 'false'),
                      encoding='utf-8-sig')
    flags = 0x0800_0000 | 0x0000_0200        # no window, own process group: it outlives Drift
    p = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                          '-WindowStyle', 'Hidden', '-File', str(script)],
                         creationflags=flags, close_fds=True, cwd=str(base))
    if wait:
        p.wait(60)


def due(store):
    return time.time() - float(store.get('update_checked') or 0) >= EVERY
