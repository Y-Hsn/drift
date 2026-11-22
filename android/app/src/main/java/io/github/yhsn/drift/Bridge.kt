package io.github.yhsn.drift

import android.webkit.JavascriptInterface
import io.github.yhsn.drift.extract.Tube
import org.json.JSONArray
import org.json.JSONObject

/**
 * window.DriftNative: what the page (phone/native.js) may ask of the app. Calls arrive on WebView's bridge thread;
 * player commands go to the main thread, network work to the worker pool. Answers to [call] come back as
 * {ev: "reply", id, ok, data | error} through [reply].
 */
class Bridge(private val reply: (JSONObject) -> Unit) {
    private val app = DriftApp.app
    private val shelf = app.shelf

    @JavascriptInterface fun version(): String = Updater.versionName(app)

    /** {id, playing, pos, dur}: where the player is right now. */
    @JavascriptInterface fun state(): String = Hub.engine?.snapshot ?: "{}"

    @JavascriptInterface fun queue(json: String) {
        val o = runCatching { JSONObject(json) }.getOrNull() ?: return
        Hub.with { it.queue(o) }
    }

    @JavascriptInterface fun play() = Hub.with { it.start() }
    @JavascriptInterface fun pause() = Hub.with { it.pause() }
    @JavascriptInterface fun seek(secs: Double) = Hub.with { it.seek(secs) }
    @JavascriptInterface fun next() = Hub.with { it.next() }
    @JavascriptInterface fun previous() = Hub.with { it.previous() }

    // ---- shelf files ----
    @JavascriptInterface fun fileBegin(id: String, kind: String): String = shelf.begin(id, kind)
    @JavascriptInterface fun fileChunk(h: String, b64: String): String = shelf.chunk(h, b64)
    @JavascriptInterface fun fileEnd(h: String, ok: Boolean): Boolean = shelf.end(h, ok)
    @JavascriptInterface fun fileDrop(id: String) = shelf.drop(id)
    @JavascriptInterface fun fileHas(id: String): Boolean = shelf.has(id)
    @JavascriptInterface fun fileUsed(): Double = shelf.used().toDouble()

    /** Slow work, answered later: search {q, n}, similar {seed, n, avoid[]}, update {}. */
    @JavascriptInterface fun call(id: Int, method: String, args: String) {
        app.work.execute {
            val out = JSONObject().put("ev", "reply").put("id", id)
            try {
                val a = JSONObject(args.ifEmpty { "{}" })
                val data: Any = when (method) {
                    "search" -> JSONArray().apply {
                        Tube.search(a.getString("q"), a.optInt("n", 10)).forEach { put(Engine.songJson(it, "Streaming")) }
                    }
                    "similar" -> {
                        val avoid = a.optJSONArray("avoid")?.let { arr -> (0 until arr.length()).map(arr::optString) }.orEmpty()
                        Engine.songsJson(Engine.radio(a.getJSONObject("seed"), a.optInt("n", 6), avoid.toSet()))
                    }
                    "update" -> Updater.check(app)?.json() ?: JSONObject.NULL
                    else -> throw IllegalArgumentException("unknown call $method")
                }
                out.put("ok", true).put("data", data)
            } catch (e: Exception) {
                out.put("ok", false).put("error", e.message ?: e.javaClass.simpleName)
            }
            reply(out)
        }
    }

    /** Self-update hook: opens a newer APK (from [call] "update") in the browser; Android's installer takes over. */
    @JavascriptInterface fun install(url: String) {
        if (url.startsWith(Updater.DOWNLOADS)) app.main.post { Updater.install(app, url) }
    }
}
