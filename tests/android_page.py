"""The phone page as the Android app runs it, with a fake window.DriftNative (headless Edge, phone-sized), against
tests/phone_door_mock.py: pair -> shelf files go to the app -> radio plays through the native player -> the player
moves on by itself -> YouTube search + streaming -> YouTube drift. Then an Android browser gets "Get the Drift app".
python tests/android_page.py SONG_DIR OUTDIR    (SONG_DIR: song01.m4a ... like tests/phone_e2e.py)"""
import asyncio
import re
import subprocess
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
SONGS, OUT = Path(sys.argv[1]), Path(sys.argv[2])
PORT = 8791

# A stand-in for android/app Bridge.kt + Engine.kt: keeps a playlist like the ExoPlayer one and answers like them.
FAKE = r"""
(() => {
  const yt = (id, title, artist) => ({ id: 'yt:' + id, yt: id, title, artist, album: '', secs: 200, art: '',
    remote: true, source: 'youtube', page: 'https://www.youtube.com/watch?v=' + id, stream: '' });
  const F = window.__fake = { queued: [], calls: [], kept: {}, open: {}, items: [], at: -1, playing: false };
  const say = m => setTimeout(() => window.__drift && window.__drift(m), 0);
  const cur = () => F.items[F.at];
  window.DriftNative = {
    version: () => '0.1.0',
    state: () => JSON.stringify({ ev: 'time', id: cur()?.id ?? null, playing: F.playing, pos: 0, dur: 0 }),
    queue(json) {
      const o = JSON.parse(json); F.queued.push(o);
      const want = o.items[o.at].id;
      if (cur()?.id === want) { F.items = [...F.items.slice(0, F.at + 1), ...o.items.slice(o.at + 1)]; if (o.play) this.play(); return; }
      if (!o.play && cur()) { say({ ev: 'moved', song: cur(), prev: 0 }); return; }
      F.items = o.items; F.at = o.at;
      say({ ev: 'moved', song: cur(), prev: 0 }); say({ ev: 'ready', dur: cur().secs || 30 });
      F.playing = !!o.play; say({ ev: 'state', playing: F.playing });
    },
    play() { F.playing = true; say({ ev: 'state', playing: true }); },
    pause() { F.playing = false; say({ ev: 'state', playing: false }); },
    seek() {}, next() {}, previous() {},
    call(id, method, args) {
      F.calls.push([method, JSON.parse(args)]);
      const data = method === 'search' ? [yt('J7p4bzqLvCw', 'Blinding Lights', 'The Weeknd'), yt('RmYCOm4ehKs', 'Save Your Tears', 'The Weeknd')]
                 : method === 'similar' ? [yt('qPRNIHxLhmc', 'I Feel It Coming', 'The Weeknd'), yt('XfEMj-z3TtA', 'STAY', 'The Kid LAROI'),
                                           yt('3_g2un5M350', 'Starboy', 'The Weeknd'), yt('_GWKkqNoyEA', 'Counting Stars', 'OneRepublic'),
                                           yt('jWpZuFM0Qtk', 'Safe and Sound', 'Capital Cities'), yt('xTvyyoF_LZY', 'Shape of You', 'Ed Sheeran')]
                 : null;
      setTimeout(() => window.__drift({ ev: 'reply', id, ok: true, data }), 30);
    },
    fileBegin(id, kind) { const h = 'h' + Math.random(); F.open[h] = { id, kind, n: 0 }; return h; },
    fileChunk(h, b64) { F.open[h].n += atob(b64).length; return 'ok'; },
    fileEnd(h, ok) { const f = F.open[h]; delete F.open[h]; if (ok) F.kept[f.id + ':' + f.kind] = f.n; return ok; },
    fileDrop(id) { delete F.kept[id + ':song']; delete F.kept[id + ':cover']; },
    fileHas: id => (id + ':song') in F.kept,
    fileUsed: () => Object.values(F.kept).reduce((a, b) => a + b, 0),
    install() {},
  };
  // the player reaching the end of a song by itself (also what happens with the screen off)
  F.advance = () => { F.at++; say({ ev: 'moved', song: cur(), prev: 25 }); };
})();
"""


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    door = subprocess.Popen([sys.executable, '-u', str(ROOT / 'tests' / 'phone_door_mock.py'), str(SONGS), str(PORT)],
                            stdout=subprocess.PIPE, text=True)
    link = re.search(r'invite: (\S+)', door.stdout.readline())[1]
    problems = []
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch(channel='msedge')
            ua = 'Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0 Mobile Safari/537.36'
            ctx = await b.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True,
                                      has_touch=True, user_agent=ua + ' wv')
            await ctx.add_init_script(FAKE)
            pg = await ctx.new_page()
            pg.on('console', lambda m: m.type == 'error' and problems.append('console: ' + m.text))
            pg.on('pageerror', lambda e: problems.append('page error: ' + str(e)))
            await pg.goto(link)
            await pg.wait_for_selector('#main:not([hidden])', timeout=15000)
            await pg.wait_for_function("document.getElementById('sync-line').textContent.includes('new songs')", timeout=60000)
            kept = await pg.evaluate('__fake.kept')
            sizes = {f.stem: f.stat().st_size for f in SONGS.glob('*.m4a')}
            assert {k[:-5]: v for k, v in kept.items() if k.endswith(':song')} == sizes, f'shelf files did not reach the app: {kept}'
            print('shelf files kept by the app:', len(kept), '| sync:', await pg.text_content('#sync-line'))
            sw = await pg.evaluate("navigator.serviceWorker ? navigator.serviceWorker.getRegistrations().then(r => r.length) : 0")
            assert sw == 0, 'a service worker was registered inside the app'

            # radio over the shelf, played by the native player
            await pg.click('#play')
            await pg.wait_for_timeout(800)
            qs = await pg.evaluate('__fake.queued')
            title = await pg.text_content('#now-title')
            started = [x for x in qs if x['play']]
            assert started and started[-1]['items'][started[-1]['at']]['title'] == title, f'play did not reach the native player: {title}'
            q = qs[-1]                                   # the latest queue the page sent (radio filled in)
            assert q['items'][q['at']]['title'] == title
            assert len(q['items']) - q['at'] > 3, 'the native player got no songs to come'
            print('native player plays:', title, '| then', [s['title'] for s in q['items'][q['at'] + 1:q['at'] + 4]])

            # the player goes on by itself: the page follows, counts the play, keeps its queue
            nxt = q['items'][q['at'] + 1]['title']
            await pg.evaluate('__fake.advance()')
            await pg.wait_for_timeout(500)
            assert await pg.text_content('#now-title') == nxt, 'page did not follow the native player'
            outbox = await pg.evaluate("""new Promise(ok => { const r = indexedDB.open('drift'); r.onsuccess = () => {
                const t = r.result.transaction('outbox').objectStore('outbox').getAll(); t.onsuccess = () => ok(t.result.map(e => e.kind)); }; })""")
            assert 'play' in outbox, f'the finished song was not counted: {outbox}'
            print('player moved on by itself ->', nxt, '| events:', outbox)
            await pg.screenshot(path=str(OUT / 'a1-now.png'))

            # YouTube search, streamed by the native player
            await pg.click('.tabs button[data-page="search"]')
            await pg.fill('#search', 'the weeknd')
            await pg.wait_for_selector('#page-search .label:text-is("Stream now · YouTube")', timeout=5000)
            await pg.screenshot(path=str(OUT / 'a2-search.png'))
            assert not await pg.is_visible('#online-label'), 'the open-music list shows inside the app'
            await pg.click('#page-search li:has(b:text-is("Blinding Lights"))')
            await pg.wait_for_timeout(1200)
            calls = [c[0] for c in await pg.evaluate('__fake.calls')]
            qs = await pg.evaluate('__fake.queued')
            started = [x for x in qs if x['play'] and x['items'][x['at']]['id'] == 'yt:J7p4bzqLvCw']
            assert started, 'the YouTube song did not start'
            last = qs[-1]
            upcoming = [s['title'] for s in last['items'][last['at'] + 1:]]
            assert 'similar' in calls and 'I Feel It Coming' in upcoming, f'no YouTube drift: {calls} {upcoming}'
            assert await pg.text_content('#now-title') == 'Blinding Lights'
            print('streams Blinding Lights | drift:', upcoming[:5])
            await pg.screenshot(path=str(OUT / 'a3-streaming.png'))

            # an Android phone's browser (no app) with the PC's code: offer the app
            await ctx.close()
            ctx2 = await b.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True, user_agent=ua)
            pg2 = await ctx2.new_page()
            await pg2.goto(link.replace('#i=', '#i=') )
            await pg2.wait_for_selector('#android-app:not([hidden])', timeout=10000)
            href = await pg2.get_attribute('#app-open', 'href')
            assert href.startswith('intent://pair?i=') and 'scheme=drift' in href, href
            print('Android browser: offers the app ->', href[:60], '...')
            await pg2.screenshot(path=str(OUT / 'a4-android-browser.png'))
            await b.close()
    finally:
        door.terminate()
    problems = [x for x in problems if 'favicon' not in x]
    if problems:
        print('PROBLEMS:', *problems, sep='\n  ')
    print('PASS')


asyncio.run(main())
