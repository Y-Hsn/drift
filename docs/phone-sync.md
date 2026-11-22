# Drift on the phone: shelf + sync (batch 1.1)

The phone keeps its own songs and always plays locally. The PC is a sync partner, never a streaming server, and may be
off. When both are online they sync: the PC fills the phone's shelf, the phone sends back what happened.

## Parts

| Part | Where | Job |
|---|---|---|
| Phone app | `phone/` -> GitHub Pages (`/drift/phone/`) | PWA: shelf, player, offline radio, search, pairing, sync client |
| Door | `core/src/door.rs` (drift-core) | Tiny HTTP server on 127.0.0.1, checks every request, serves song files, stores phone events |
| Shelf brain | `drift/phone.py` (run by the core as `Drift.exe --net phone ...`) | Picks the shelf for a phone, applies phone events to drift.db |
| Link | `core/src/door.rs` | Starts `cloudflared` (fetched on first pairing, SHA-256 pinned) while a phone is paired; posts the signed current address to ntfy.sh |
| Settings > Phones | `drift/ui/settings.py` | Pair a phone (QR), list phones, unpair, shelf size |

## Pairing

1. Settings > Phones > Pair a phone: the core makes an invite (random 128-bit, valid 5 min, works once) and the window
   shows a QR for `https://y-hsn.github.io/drift/phone/#i=<pc>.<invite>.<topic>`.
2. The phone opens it in Safari. The page removes the fragment from the address bar at once, redeems the invite
   (`POST /pair`), receives a one-use install ticket and keeps it in a first-party cookie (iOS copies cookies, not
   IndexedDB, when the app is added to the home screen), then shows "Share > Add to Home Screen".
3. First start from the home screen: the app trades the ticket for its device key (`POST /pair/finish`): a random
   256-bit secret made on the phone and stored in IndexedDB. The PC stores `{device id, name, key hash, created}`.
4. Unpair on the PC: the device's key stops working at once.

## Finding the PC

The PC posts `{url, gen, exp}` signed with HMAC(pc secret) and encrypted per paired phone to the ntfy topic
`drift-<random topic id>` when the link starts and every 6 h (ntfy keeps messages 12 h). The phone reads
`https://ntfy.sh/<topic>/json?poll=1&since=12h`, keeps the newest valid `gen`, and caches the last address that worked.

## Requests

Every request carries `X-Drift-Device`, `X-Drift-Time` (ms) and `X-Drift-Sign` = HMAC-SHA256(device key,
method | path | time | sha256(body)). The door refuses anything older than 2 min, replayed signatures and unknown
devices before Python wakes. CORS: only the phone app's origin.

| Route | Answer |
|---|---|
| `POST /pair`, `POST /pair/finish` | pairing (invite / ticket instead of a signature) |
| `GET /shelf?have=<rev>` | the shelf manifest: songs `{id, title, artist, album, secs, bytes, sha256, cover, why}`, playlists `{id, name, items[]}`, taste snapshot, `rev` |
| `GET /song/<id>` | the whole file (200); the phone verifies sha256 before the song shows as ready |
| `GET /cover/<id>` | cover jpg |
| `POST /events` | phone events since the last ack -> `{ack: seq}` |
| `POST /wish` | a search the phone wants; the PC finds + fetches it, it joins the shelf |

## Events (phone -> PC)

`{device, seq, at, kind, ...}`; `seq` increases per device; the PC acks the highest contiguous `seq` after writing it
to drift.db. Kinds: `play {song, secs}`, `skip {song, at}`, `like {song, on}`, `playlist.add {pl, item, song, after}`,
`playlist.remove {pl, item}`, `playlist.new {pl, name}`, `playlist.rename {pl, name}`, `wish {query}`.
Every playlist entry has its own item id, so a song twice in a list stays twice. Remove wins over a move of the same
entry; renames and likes: newest `at`, then device id.

## Shelf

Size the user picks on the phone (1 / 2 / 5 GB). Order of filling: pinned songs, songs in synced playlists, liked,
then the radio crate (what Drift radio would play next for this phone, from the PC's taste). Radio songs leave first
when space is needed. Songs arrive in the background while the app is open; a half-downloaded song never shows.
Storage: Cache Storage (`drift-songs`) for files, IndexedDB for the shelf index and the event outbox.

## Playback (tested on iOS 18.7)

One `<audio>` element + Media Session (title, artist, artwork, play/pause/next/previous). The next song is ready
before the current one ends. Locked auto-advance works (~0.8 s between songs). Radio offline = taste-ordered shuffle
over the crate, artists spaced out, nothing twice.
