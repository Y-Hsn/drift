"""Updates from GitHub Releases, one click: look for a newer release (at most every 6 h, in the background), fetch
its zip, check it against the SHA-256 GitHub publishes for it, unpack it next to Drift's data, then quit. A hidden
PowerShell waits for the window and the core to be gone, swaps the program files (the old ones are kept until the
new ones are all in place, and put back if anything fails), and starts the new Drift, which carries on playing
where the old one stopped. Your data (%LOCALAPPDATA%/Drift) is never touched. Only for the packaged Drift.exe."""
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
from pathlib import Path

from . import __version__

REPO = 'Y-Hsn/drift'
LATEST = f'https://api.github.com/repos/{REPO}/releases/latest'
ASSET = 'Drift-windows.zip'
EVERY = 6 * 3600
NEEDED = ('Drift.exe', 'drift-core.exe', '_internal')
found = None                        # the newer release this run found (kept when the window is rebuilt)


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
    if version_tuple(version) <= version_tuple(current):
        return None
    asset = next((a for a in rel.get('assets') or [] if a.get('name') == ASSET), None)
    digest = (asset or {}).get('digest') or ''
    if not asset or not digest.startswith('sha256:'):
        return None                         # never install what cannot be checked
    return {'version': version, 'url': asset['browser_download_url'], 'size': int(asset.get('size') or 0),
            'sha256': digest.split(':', 1)[1].lower(), 'notes': (rel.get('body') or '').strip()}


def folder():
    from .store import DATA
    return DATA / 'update'


def fetch(info, progress=lambda frac: None, timeout=20):
    """Download and check the zip, unpack it; the folder holding the new Drift.exe."""
    base = folder()
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    zpath = base / ASSET
    h, done = hashlib.sha256(), 0
    with _get(info['url'], timeout) as r, open(zpath, 'wb') as out:
        while chunk := r.read(256 * 1024):
            out.write(chunk)
            h.update(chunk)
            done += len(chunk)
            progress(done / info['size'] if info['size'] else 0.0)
    if info['size'] and done != info['size']:
        raise RuntimeError('the download was cut short')
    if h.hexdigest() != info['sha256']:
        raise RuntimeError('the download does not match the release (SHA-256)')
    new = base / 'new'
    with zipfile.ZipFile(zpath) as z:
        for entry in z.infolist():
            name = entry.filename.replace('\\', '/')
            target = (new / name).resolve()
            if not str(target).startswith(str(new.resolve()) + os.sep):
                raise RuntimeError('the zip has a file outside its folder')
            if name.endswith('/'):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(entry) as src, open(target, 'wb') as dst:
                shutil.copyfileobj(src, dst)
    zpath.unlink()
    exe = next(iter(sorted(new.rglob('Drift.exe'), key=lambda p: len(p.parts))), None)
    if not exe or not all((exe.parent / n).exists() for n in NEEDED):
        raise RuntimeError('the zip is not a whole Drift')
    return exe.parent


SWAP = r'''$ErrorActionPreference = 'Stop'
$app = '{app}'; $new = '{new}'; $log = '{log}'
function Log($m) {{ Add-Content -LiteralPath $log -Value "$(Get-Date -Format s) $m" }}
foreach ($id in @({pids})) {{ try {{ Wait-Process -Id $id -Timeout 30 -ErrorAction Stop }} catch {{}} }}
foreach ($i in 1..50) {{
    try {{ foreach ($f in 'Drift.exe', 'drift-core.exe') {{ [IO.File]::Open("$app\$f", 'Open', 'ReadWrite', 'None').Close() }}; break }}
    catch {{ Start-Sleep -Milliseconds 200 }}
}}
$old = Join-Path $app '.drift-old'
$names = @(Get-ChildItem -LiteralPath $new -Force | ForEach-Object Name)
try {{
    if (Test-Path -LiteralPath $old) {{ Remove-Item -LiteralPath $old -Recurse -Force }}
    New-Item -ItemType Directory -Path $old | Out-Null
    foreach ($n in $names) {{ if (Test-Path -LiteralPath "$app\$n") {{ Move-Item -LiteralPath "$app\$n" -Destination $old }} }}
    foreach ($n in $names) {{ Copy-Item -LiteralPath "$new\$n" -Destination $app -Recurse -Force }}   # may be another drive
    Log 'updated to {version}'
    Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Split-Path $new) -Recurse -Force -ErrorAction SilentlyContinue
}} catch {{
    Log "update failed, old Drift put back: $_"
    foreach ($n in $names) {{
        if (Test-Path -LiteralPath "$old\$n") {{
            Remove-Item -LiteralPath "$app\$n" -Recurse -Force -ErrorAction SilentlyContinue
            Move-Item -LiteralPath "$old\$n" -Destination $app
        }}
    }}
}}
if (${start}) {{ Start-Process -FilePath explorer.exe -ArgumentList ('"' + "$app\Drift.exe" + '"') }}
'''


def _ps(text):
    return text.replace("'", "''")


def apply(new, version, pids, app=None, start=True, wait=False):
    """Start the hidden swap; the caller quits Drift and the core right after. (app/start/wait: for tests.)"""
    app = app or app_dir()
    base = folder()
    script = base / 'swap.ps1'
    script.write_text(SWAP.format(app=_ps(str(app)), new=_ps(str(new)), log=_ps(str(base.parent / 'update.log')),
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
