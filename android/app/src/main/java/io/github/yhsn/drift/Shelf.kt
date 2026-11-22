package io.github.yhsn.drift

import android.util.Base64
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/**
 * The shelf's song files and covers, kept by the app (not in the WebView's cache) so the native player can play
 * them with the screen off. The page hands the bytes over in base64 pieces (native.js files.put); a file only
 * counts once all of it has arrived. meta.json = what the page told us about each shelf song, for the native
 * radio when the page is asleep and the phone is offline.
 */
class Shelf(root: File) {
    private val dir = File(root, "shelf").apply { mkdirs() }
    private val metaFile = File(dir, "meta.json")
    private val meta: JSONObject = runCatching { JSONObject(metaFile.readText()) }.getOrDefault(JSONObject())
    private val open = ConcurrentHashMap<String, Pending>()

    private class Pending(val id: String, val target: File, val part: File, val out: FileOutputStream)

    private fun key(id: String): String =
        MessageDigest.getInstance("SHA-1").digest(id.toByteArray()).joinToString("") { "%02x".format(it) }

    fun song(id: String) = File(dir, key(id) + ".song")
    fun cover(id: String) = File(dir, key(id) + ".jpg")
    fun has(id: String) = song(id).isFile

    fun used(): Long = dir.listFiles()?.sumOf { it.length() } ?: 0L

    /** Starts receiving a song ("song") or cover ("cover"); returns the handle for [chunk] / [end]. */
    fun begin(id: String, kind: String): String {
        val h = UUID.randomUUID().toString()
        val part = File(dir, "$h.part")
        open[h] = Pending(id, if (kind == "cover") cover(id) else song(id), part, FileOutputStream(part))
        return h
    }

    /** "ok", "full" (the phone has no room left) or "error". */
    fun chunk(h: String, b64: String): String {
        val p = open[h] ?: return "error"
        return try {
            p.out.write(Base64.decode(b64, Base64.DEFAULT))
            "ok"
        } catch (e: IOException) {
            end(h, false)
            if (dir.usableSpace < 64L shl 20) "full" else "error"
        } catch (e: IllegalArgumentException) {
            end(h, false)
            "error"
        }
    }

    fun end(h: String, ok: Boolean): Boolean {
        val p = open.remove(h) ?: return false
        runCatching { p.out.close() }
        if (!ok || p.part.length() == 0L) { p.part.delete(); return false }
        p.target.delete()
        return p.part.renameTo(p.target)
    }

    fun drop(id: String) {
        song(id).delete()
        cover(id).delete()
        synchronized(meta) { if (meta.remove(id) != null) saveMeta() }
    }

    /** Remembers a shelf song's details (title, artist...) for the offline radio. */
    fun know(s: JSONObject) {
        val id = s.optString("id")
        if (id.isEmpty() || !has(id)) return
        synchronized(meta) {
            if (meta.optJSONObject(id)?.toString() == s.toString()) return
            meta.put(id, s)
            saveMeta()
        }
    }

    fun known(): List<JSONObject> = synchronized(meta) {
        meta.keys().asSequence().filter(::has).mapNotNull { meta.optJSONObject(it) }.toList()
    }

    private fun saveMeta() {
        DriftApp.app.work.execute {
            val text = synchronized(meta) { meta.toString() }
            val tmp = File(dir, "meta.json.tmp")
            runCatching { tmp.writeText(text); tmp.renameTo(metaFile) }
        }
    }
}
