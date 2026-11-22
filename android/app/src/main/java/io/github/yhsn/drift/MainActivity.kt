package io.github.yhsn.drift

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.ComponentName
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.window.OnBackInvokedDispatcher
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import androidx.webkit.WebViewAssetLoader
import com.google.common.util.concurrent.ListenableFuture
import okhttp3.Request
import org.json.JSONObject
import java.io.FileInputStream

/**
 * The one screen: the phone app from phone/ (the same as on an iPhone), served from the APK's assets at
 * https://y-hsn.github.io/drift/phone/ (the PWA's own address, so pairing with the PC works the same), with
 * window.DriftNative for the native player, YouTube search and the shelf's files.
 */
class MainActivity : Activity() {
    private lateinit var web: WebView
    private var controller: ListenableFuture<MediaController>? = null
    private var resumed = false
    private val app get() = DriftApp.app
    private val sender: (JSONObject) -> Unit = { send(it) }

    private val tick = object : Runnable {
        override fun run() {
            Hub.engine?.tick()
            if (resumed) app.main.postDelayed(this, 500)
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        if (0 != applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) WebView.setWebContentsDebuggingEnabled(true)
        web = WebView(this)
        web.setBackgroundColor(getColor(R.color.night))
        setContentView(web)

        val assets = WebViewAssetLoader.AssetsPathHandler(this)
        val loader = WebViewAssetLoader.Builder()
            .setDomain(HOST)
            .addPathHandler("/drift/phone/c/") { path -> cover(Uri.decode(path)) }    // shelf covers, kept by the app
            .addPathHandler("/drift/phone/") { path -> assets.handle("phone/" + path.ifEmpty { "index.html" }) }
            .build()

        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = false
            allowContentAccess = false
            setSupportMultipleWindows(false)
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, req: WebResourceRequest): WebResourceResponse? =
                loader.shouldInterceptRequest(req.url) ?: art(req)

            override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
                val u = req.url
                if (u.host == HOST && u.path.orEmpty().startsWith("/drift/phone")) return false
                runCatching { startActivity(Intent(Intent.ACTION_VIEW, u)) }      // anything else: the browser
                return true
            }
        }
        web.addJavascriptInterface(Bridge(::send), "DriftNative")

        Hub.with { e ->
            e.listener = sender
            e.tick()
        }
        // binding the media session starts PlaybackService (and with it the player)
        controller = MediaController.Builder(this, SessionToken(this, ComponentName(this, PlaybackService::class.java))).buildAsync()

        if (Build.VERSION.SDK_INT >= 33) {
            onBackInvokedDispatcher.registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT) { moveTaskToBack(true) }
            if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
            }
        }
        web.loadUrl(START + invite(intent).let { if (it.isEmpty()) "" else "#$it" })
    }

    @Deprecated("Android 12 and older")
    @Suppress("DEPRECATION")
    override fun onBackPressed() { moveTaskToBack(true) }        // back = to the home screen, the music plays on

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val inv = invite(intent)
        if (inv.isNotEmpty()) web.evaluateJavascript("location.hash=${JSONObject.quote(inv)};location.reload()", null)
    }

    override fun onResume() {
        super.onResume()
        resumed = true
        app.main.post(tick)
    }

    override fun onPause() {
        resumed = false
        app.main.removeCallbacks(tick)
        super.onPause()
    }

    override fun onDestroy() {
        Hub.engine?.let { if (it.listener === sender) it.listener = null }
        controller?.let(MediaController::releaseFuture)
        web.destroy()
        super.onDestroy()
    }

    /** To the page: window.__drift(message). Times only while the screen shows the app. */
    private fun send(o: JSONObject) {
        if (o.optString("ev") == "time" && !resumed) return
        val js = "window.__drift&&window.__drift($o)"
        app.main.post { if (!isDestroyed) web.evaluateJavascript(js, null) }
    }

    private fun cover(id: String): WebResourceResponse? {
        val f = app.shelf.cover(id)
        return if (f.isFile) WebResourceResponse("image/jpeg", null, FileInputStream(f)) else null
    }

    /** YouTube's thumbnails, fetched here so the page may read their colours (they send no CORS header). */
    private fun art(req: WebResourceRequest): WebResourceResponse? {
        if (req.method != "GET" || req.url.scheme != "https" || req.url.host !in ART_HOSTS) return null
        return try {
            val r = app.http.newCall(Request.Builder().url(req.url.toString()).build()).execute()
            if (!r.isSuccessful) { r.close(); return null }
            WebResourceResponse(r.header("Content-Type", "image/jpeg")!!.substringBefore(';'), null, 200, "OK",
                mapOf("Access-Control-Allow-Origin" to "*", "Cache-Control" to "max-age=86400"), r.body!!.byteStream())
        } catch (e: Exception) { null }
    }

    companion object {
        const val HOST = "y-hsn.github.io"
        const val START = "https://$HOST/drift/phone/index.html"
        val ART_HOSTS = setOf("i.ytimg.com", "i1.ytimg.com", "i9.ytimg.com", "yt3.ggpht.com", "lh3.googleusercontent.com")
        private val SAFE = Regex("""[A-Za-z0-9_\-.=&%]{1,2000}""")

        /** The PC's pairing invite (i=...&u=...&t=...) from drift://pair?... or the QR's https link (#...). */
        fun invite(intent: Intent?): String {
            val d = intent?.data ?: return ""
            val raw = when (d.scheme) {
                "drift" -> d.encodedQuery
                "https" -> if (d.host == HOST) d.encodedFragment else null
                else -> null
            } ?: return ""
            return if (SAFE.matches(raw) && raw.contains("i=")) raw else ""
        }
    }
}
