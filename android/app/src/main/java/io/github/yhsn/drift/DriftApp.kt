package io.github.yhsn.drift

import android.app.Application
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Handler
import android.os.Looper
import io.github.yhsn.drift.extract.Net
import io.github.yhsn.drift.extract.Tube
import okhttp3.OkHttpClient
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** One HTTP client, one small worker pool and the shelf for the whole app. */
class DriftApp : Application() {
    lateinit var http: OkHttpClient
    lateinit var shelf: Shelf
    val work: ExecutorService = Executors.newFixedThreadPool(3)
    val main = Handler(Looper.getMainLooper())

    override fun onCreate() {
        super.onCreate()
        app = this
        http = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .build()
        Tube.init(Net(http))
        shelf = Shelf(filesDir)
    }

    fun online(): Boolean {
        val cm = getSystemService(ConnectivityManager::class.java) ?: return true
        val caps = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    companion object {
        lateinit var app: DriftApp
            private set
    }
}

/** The player lives in PlaybackService; the page reaches it through here once the service is up. */
object Hub {
    private val waiting = ArrayList<(Engine) -> Unit>()
    var engine: Engine? = null
        set(value) {
            field = value
            if (value != null) { waiting.forEach { it(value) }; waiting.clear() }
        }

    /** Runs [f] on the main thread with the engine, now or as soon as the service has started. */
    fun with(f: (Engine) -> Unit) {
        DriftApp.app.main.post { engine?.let(f) ?: waiting.add(f) }
    }
}
