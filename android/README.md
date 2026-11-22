# Drift for Android

A free, sideloaded APK (no Play Store). It shows the same phone app as the iPhone (`../phone/`, bundled as it is) and
adds what a web page cannot do on Android:

- **YouTube on the phone itself**: search (YouTube Music songs first) and audio-only streams through
  [NewPipeExtractor](https://github.com/TeamNewPipe/NewPipeExtractor) v0.27.1 (GPL-3.0, like Drift). PC on or off.
- **A native player**: Media3 ExoPlayer in a `MediaSessionService` (foreground service). Plays with the screen
  locked, shows title + artwork on the lock screen and in the notification, play/pause/next/previous, goes from song
  to song by itself; the next YouTube song's audio link is looked up ahead, so there is no wait between songs.
- **Drift radio online** = YouTube's mix of the song playing (`list=RD<id>`), then its related songs; a shelf song
  is first found on YouTube. Offline = the shelf synced from the PC.
- **Pairing**: the PC's QR (`https://y-hsn.github.io/drift/phone/#i=...`) opened in an Android browser offers
  "Open in the Drift app" / "Get the Drift app"; the app takes the same invite (`drift://pair?i=...&u=...&t=...`, or
  the https link itself when "Open supported links" is on for Drift) and pairs + syncs exactly like the PWA.
  Until the PC side is ready, "Play from YouTube now, pair later" opens the player without a PC.

## Build (one command)

```powershell
cd android; .\gradlew.bat :app:assembleDebug
```

APK: `android/app/build/outputs/apk/debug/app-debug.apk` (signed with this PC's debug key `~/.android/debug.keystore`).
`:app:assembleRelease` gives a minified 2.3 MB APK (same debug key, not tested on a phone yet).

Needs: JDK 21+ (JDK 23 works), the Android SDK at `%LOCALAPPDATA%\Android\Sdk` (`local.properties`), with
`platforms/android-36` + `build-tools/36.1.0` (installed with `cmdline-tools\latest\bin\android.exe sdk install ...`).
Gradle 9.9.2 comes through the wrapper. Run it at below-normal priority on the owner's PC:
`cmd /c "start /b /belownormal /wait gradlew.bat :app:assembleDebug"`.

**Keep the signing key.** Android installs an update only when it is signed with the same key. Every APK so far is
signed with this PC's debug key; before friends rely on it, make one release key, back it up, and set it in
`app/build.gradle.kts` (then everyone reinstalls once).

## Tests

- `.\gradlew.bat :extract:test`: on the JVM, talks to YouTube for real: searches "The Weeknd Blinding Lights",
  gets the m4a audio link, checks it answers 206 with sound (open range + a jump into the middle), builds a radio.
  Also `TitleTest` (offline: video names -> song titles).
- `python tests/android_page.py SONG_DIR OUTDIR` (repo root): the phone page as the app runs it, with a fake
  `window.DriftNative`, against `tests/phone_door_mock.py`: pair -> shelf files go to the app -> radio through the
  native player -> the player moves on by itself -> YouTube search + streaming -> YouTube drift -> an Android browser
  is offered the app. `tests/phone_e2e.py` checks the browser/iPhone path is unchanged.

## How it fits together

| File | Job |
|---|---|
| `extract/` (plain Java) | `Tube`: search, radio (mix + related), audio link (progressive m4a first, the user agent YouTube wants for it); `Net`: OkHttp for NewPipe |
| `app/.../MainActivity.kt` | WebView with `WebViewAssetLoader` on `https://y-hsn.github.io/drift/phone/` (assets/phone), so the page has the PWA's own origin (pairing CORS unchanged); covers from the app's shelf; YouTube thumbnails fetched natively (CORS); `window.DriftNative`; invite links |
| `app/.../Bridge.kt` | `window.DriftNative`: queue/play/pause/seek, async `call` (search, similar, update), shelf files in base64 pieces |
| `app/.../Engine.kt` | the ExoPlayer playlist kept in step with player.js; `yt:<id>` items resolved through `ResolvingDataSource` + `Links` (cache until expiry, looked up ahead); retries an expired link once; drifts on by itself when the page is asleep |
| `app/.../PlaybackService.kt` | `MediaSessionService`: foreground service, lock screen, notification |
| `app/.../Shelf.kt` | shelf song files + covers in the app's storage; what the page said about each song (offline radio) |
| `app/.../Updater.kt` | self-update hook: newest GitHub release asset `Drift-android-<versionCode>.apk` > this versionCode -> open it (Android's installer takes over). Not wired to a button yet |
| `../phone/native.js` | the page side: `nativeAudio()` (the native player in the `<audio>` shape + a `moved` event), `online` (YouTube search/similar), `files`, `fill` (radio with YouTube), `youtube` (Search list), the Android browser offer |

Hooks in `../phone/` are marked `[Drift for Android]` (player.js, app.js, store.js, index.html, sw.js, style.css);
outside the app `window.DriftNative` is missing and they do nothing.

## For the PC side (not done here)

- Plays/likes of streamed songs arrive as events with `meta.source = 'youtube'` and `meta.page =
  https://www.youtube.com/watch?v=<id>`; `drift/phone.py` (`_know`) should accept YouTube pages (it takes Audius only).
- The release that carries the APK should attach it as `Drift-android-<versionCode>.apk` (the updater looks for that).
- `https://github.com/Y-Hsn/drift/releases/latest` is what "Get the Drift app" opens.

## Licence

GPL-3.0, as Drift. NewPipeExtractor is GPL-3.0; Media3, AndroidX and OkHttp are Apache-2.0.
