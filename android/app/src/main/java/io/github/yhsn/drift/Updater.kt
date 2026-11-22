package io.github.yhsn.drift

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import okhttp3.Request
import org.json.JSONObject

/**
 * Self-update from GitHub Releases (hook, no button yet). A release carries the APK as an asset named
 * Drift-android-<versionCode>.apk; a higher versionCode than this app's is an update. Installing = opening that
 * link: the browser downloads it and Android's own installer asks the user (same key = installs over, data kept).
 * Nothing is published from here.
 */
object Updater {
    const val LATEST = "https://api.github.com/repos/Y-Hsn/drift/releases/latest"
    const val DOWNLOADS = "https://github.com/Y-Hsn/drift/releases/download/"
    private val NAME = Regex("""Drift-android-(\d+)\.apk""")

    class Update(val versionCode: Long, val url: String, val tag: String) {
        fun json(): JSONObject = JSONObject().put("versionCode", versionCode).put("url", url).put("tag", tag)
    }

    fun versionCode(ctx: Context): Long {
        val p = ctx.packageManager.getPackageInfo(ctx.packageName, 0)
        return if (Build.VERSION.SDK_INT >= 28) p.longVersionCode else @Suppress("DEPRECATION") p.versionCode.toLong()
    }

    fun versionName(ctx: Context): String = ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName ?: ""

    /** Blocks (network): call from the worker pool. */
    fun check(ctx: Context): Update? {
        val req = Request.Builder().url(LATEST).header("Accept", "application/vnd.github+json").build()
        val body = DriftApp.app.http.newCall(req).execute().use { if (it.isSuccessful) it.body?.string() else null } ?: return null
        val rel = JSONObject(body)
        val assets = rel.optJSONArray("assets") ?: return null
        val mine = versionCode(ctx)
        for (i in 0 until assets.length()) {
            val a = assets.getJSONObject(i)
            val code = NAME.matchEntire(a.optString("name"))?.groupValues?.get(1)?.toLongOrNull() ?: continue
            val url = a.optString("browser_download_url")
            if (code > mine && url.startsWith(DOWNLOADS)) return Update(code, url, rel.optString("tag_name"))
        }
        return null
    }

    fun install(ctx: Context, url: String) {
        ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}
