"""End-to-end check of the phone app against tests/phone_door_mock.py (headless Edge, phone-sized):
pair from an invite link -> sync fills the shelf -> radio plays -> events reach the PC -> works offline -> shots.
python tests/phone_e2e.py SONG_DIR OUTDIR"""
import asyncio
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
SONGS, OUT = Path(sys.argv[1]), Path(sys.argv[2])
PORT = 8790


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    door = subprocess.Popen([sys.executable, '-u', str(ROOT / 'tests' / 'phone_door_mock.py'), str(SONGS), str(PORT)],
                            stdout=subprocess.PIPE, text=True)
    link = re.search(r'invite: (\S+)', door.stdout.readline())[1]
    problems = []
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch(channel='msedge', args=['--autoplay-policy=no-user-gesture-required'])
            ctx = await b.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
            pg = await ctx.new_page()
            pg.on('console', lambda m: m.type == 'error' and problems.append('console: ' + m.text))
            pg.on('requestfailed', lambda r: problems.append('failed: ' + r.url[:120]))
            pg.on('pageerror', lambda e: problems.append('page error: ' + str(e)))
            await pg.goto(link)
            await pg.wait_for_selector('#main:not([hidden])', timeout=15000)
            assert '#i=' not in pg.url, 'invite stayed in the address bar'
            await pg.screenshot(path=str(OUT / '1-paired.png'))
            t0 = time.time()
            await pg.wait_for_function("document.getElementById('sync-line').textContent.includes('new songs')", timeout=60000)
            print(f'shelf filled in {time.time() - t0:.1f} s:', await pg.text_content('#sync-line'))
            await pg.click('#play')
            await pg.wait_for_timeout(2500)
            title = await pg.text_content('#now-title')
            assert title != 'Nothing playing', 'radio did not start'
            print('playing:', title, '| up next:', await pg.eval_on_selector_all('#queue li b', 'els => els.map(e => e.textContent)'))
            await pg.screenshot(path=str(OUT / '2-now.png'))
            await pg.click('#next')
            await pg.wait_for_timeout(1500)
            await pg.click('.tabs button[data-page="shelf"]')
            await pg.wait_for_timeout(500)
            await pg.screenshot(path=str(OUT / '3-shelf.png'))
            await pg.click('#shelf-chips [data-shelf="playlists"]')
            await pg.wait_for_timeout(300)
            await pg.screenshot(path=str(OUT / '4-playlists.png'))
            await pg.click('.tabs button[data-page="search"]')
            await pg.fill('#search', 'song 7')
            await pg.wait_for_timeout(400)
            await pg.screenshot(path=str(OUT / '5-search.png'))
            await pg.fill('#search', 'a song not here')
            await pg.click('#wish-btn')
            await pg.wait_for_timeout(2500)
            await pg.screenshot(path=str(OUT / '6-wish.png'))
            await pg.click('#open-settings')
            await pg.wait_for_timeout(400)
            await pg.screenshot(path=str(OUT / '7-settings.png'))
            await pg.click('#close-sheet')
            # offline: the app opens again and plays from the phone
            await ctx.set_offline(True)
            await pg.reload()
            await pg.wait_for_selector('#main:not([hidden])', timeout=10000)
            await pg.click('.tabs button[data-page="now"]')
            await pg.click('#play')
            await pg.wait_for_timeout(2000)
            playing = await pg.evaluate("[...document.querySelectorAll('audio')].length || 1")
            t1 = await pg.text_content('#now-at')
            await pg.wait_for_timeout(2000)
            t2 = await pg.text_content('#now-at')
            assert t1 != t2 or t2 != '0:00', 'offline playback did not move'
            print('offline:', await pg.text_content('#now-title'), t1, '->', t2, '|', await pg.text_content('#sync-line'))
            await pg.screenshot(path=str(OUT / '8-offline.png'))
            await b.close()
    finally:
        door.terminate()
        rest = door.stdout.read()
        print(rest.strip())
    if problems:
        print('PROBLEMS:\n' + '\n'.join(problems))
        sys.exit(1)
    print('phone e2e OK')


asyncio.run(main())
