package io.github.yhsn.drift

import android.content.Context
import android.net.Uri
import androidx.annotation.OptIn
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.ResolvingDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import io.github.yhsn.drift.extract.Audio
import io.github.yhsn.drift.extract.Song
import io.github.yhsn.drift.extract.Tube
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.ConcurrentHashMap

/**
 * The queue on the phone's own player (an ExoPlayer playlist), kept in step with the page's queue (player.js):
 * the page says what plays and what comes next, the player goes on by itself (also with the screen off) and tells
 * the page when it moved. YouTube songs are items with a "yt:<video id>" address; their audio link is looked up
 * when the player opens them (and ahead of time for the next song, so songs follow each other without a wait).
 * When the queue runs dry and the page is asleep, the player drifts on by itself: YouTube's mix of what is playing
 * when online, the shelf when offline.
 */
@OptIn(UnstableApi::class)
class Engine(ctx: Context) : Player.Listener {
    private val app = DriftApp.app
    private val shelf = app.shelf
    private val songs = HashMap<String, JSONObject>()       // media id -> the page's song object
    private val retried = HashMap<String, Int>()  // fresh links tried per song (YouTube refuses one now and then)
    private val recent = ArrayDeque<String>()
    private val prefs = ctx.getSharedPreferences("engine", Context.MODE_PRIVATE)
    private var topping = false
    private var prevPos = 0.0
    private var readyFor: String? = null

    /** The open page (MainActivity), null while there is none. */
    var listener: ((JSONObject) -> Unit)? = null
    @Volatile var snapshot: String = "{}"
        private set

    val player: ExoPlayer = ExoPlayer.Builder(ctx)
        .setMediaSourceFactory(androidx.media3.exoplayer.source.DefaultMediaSourceFactory(dataSource(ctx)))
        .setAudioAttributes(
            androidx.media3.common.AudioAttributes.Builder()
                .setUsage(androidx.media3.common.C.USAGE_MEDIA)
                .setContentType(androidx.media3.common.C.AUDIO_CONTENT_TYPE_MUSIC)
                .build(), true)
        .setHandleAudioBecomingNoisy(true)
        .setWakeMode(androidx.media3.common.C.WAKE_MODE_NETWORK)
        .build()

    init {
        player.addListener(this)
        prefs.getString("recent", "")!!.split(',').filter { it.isNotEmpty() }.forEach(recent::addLast)
    }

    private fun dataSource(ctx: Context): ResolvingDataSource.Factory {
        val upstream = DefaultDataSource.Factory(ctx, OkHttpDataSource.Factory(app.http))
        return ResolvingDataSource.Factory(upstream) { spec: DataSpec ->
            if (spec.uri.scheme != "yt") spec
            else {
                val a = try { Links.get(spec.uri.schemeSpecificPart) } catch (e: Exception) {
                    throw IOException("no audio link for ${spec.uri}", e)
                }
                spec.withUri(Uri.parse(a.url)).withAdditionalHeaders(mapOf("User-Agent" to a.userAgent))
            }
        }
    }

    // ---- the page's commands ----

    /** {items: [song...], at, play}: items[at] plays (or stays playing), the items after it come next. */
    fun queue(o: JSONObject) {
        val arr = o.optJSONArray("items") ?: return
        val items = (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }
        val at = o.optInt("at", 0)
        val play = o.optBoolean("play", false)
        if (at !in items.indices) return
        val want = items[at].optString("id")
        val cur = player.currentMediaItem?.mediaId
        if (cur == want) {
            keepAfter(player.currentMediaItemIndex, items.drop(at + 1))
            if (play) start()
            return
        }
        if (!play && cur != null) {           // the page is behind (the player moved while it slept): it follows us
            moved()
            return
        }
        val j = indexOf(want)
        if (j >= 0) {
            keepAfter(j, items.drop(at + 1))
            player.seekTo(j, 0)
        } else {
            val from = maxOf(0, at - 5)
            val pairs = items.subList(from, items.size).mapNotNull { s -> item(s)?.let { s.optString("id") to it } }
            if (pairs.isEmpty()) return
            val k = pairs.indexOfFirst { it.first == want }.coerceAtLeast(0)
            player.setMediaItems(pairs.map { it.second }, k, 0)
        }
        player.prepare()
        player.playWhenReady = play
    }

    fun start() {
        if (player.playbackState == Player.STATE_IDLE) player.prepare()
        if (player.playbackState == Player.STATE_ENDED) player.seekTo(player.currentMediaItemIndex, 0)
        player.play()
    }

    fun pause() = player.pause()
    fun seek(secs: Double) = player.seekTo((secs * 1000).toLong().coerceAtLeast(0))
    fun next() { if (player.hasNextMediaItem()) player.seekToNextMediaItem() else topUp() }
    fun previous() = player.seekToPrevious()

    // ---- the queue itself ----

    private fun indexOf(id: String): Int =
        (0 until player.mediaItemCount).firstOrNull { player.getMediaItemAt(it).mediaId == id } ?: -1

    /** Makes the items after index [j] the page's [rest], keeping the ones that are the same (and already loading). */
    private fun keepAfter(j: Int, rest: List<JSONObject>) {
        val restItems = rest.take(40).mapNotNull(::item)
        var k = 0
        val count = player.mediaItemCount
        while (k < restItems.size && j + 1 + k < count && player.getMediaItemAt(j + 1 + k).mediaId == restItems[k].mediaId) k++
        if (j + 1 + k < count) player.removeMediaItems(j + 1 + k, count)
        if (k < restItems.size) player.addMediaItems(restItems.subList(k, restItems.size))
    }

    /** A player item for one of the page's songs; null for a shelf song whose file is not on the phone. */
    private fun item(s: JSONObject): MediaItem? {
        val id = s.optString("id").ifEmpty { return null }
        val yt = ytOf(s)
        val stream = s.optString("stream")
        val uri = when {
            yt.isNotEmpty() -> Uri.parse("yt:$yt")
            s.optBoolean("remote") && stream.startsWith("https:") -> Uri.parse(stream)
            shelf.has(id) -> Uri.fromFile(shelf.song(id))
            else -> return null
        }
        if (uri.scheme == "file") shelf.know(s)
        val art = s.optString("art").takeIf { it.startsWith("https:") }?.let(Uri::parse)
            ?: shelf.cover(id).takeIf { it.isFile }?.let { Uri.fromFile(it) }
        val md = MediaMetadata.Builder()
            .setTitle(s.optString("title"))
            .setArtist(s.optString("artist"))
            .setAlbumTitle(s.optString("album").ifEmpty { null })
            .setArtworkUri(art)
            .setIsPlayable(true)
            .setIsBrowsable(false)
            .build()
        songs[id] = s
        return MediaItem.Builder().setMediaId(id).setUri(uri).setMediaMetadata(md).build()
    }

    // ---- what the player does ----

    override fun onPositionDiscontinuity(old: Player.PositionInfo, new: Player.PositionInfo, reason: Int) {
        if (old.mediaItemIndex != new.mediaItemIndex) prevPos = old.positionMs / 1000.0
    }

    override fun onMediaItemTransition(item: MediaItem?, reason: Int) {
        val id = item?.mediaId ?: return
        retried.clear()
        readyFor = null
        recent.remove(id); recent.addLast(id)
        while (recent.size > 300) recent.removeFirst()
        prefs.edit().putString("recent", recent.joinToString(",")).apply()
        moved(prevPos)
        prevPos = 0.0
        val old = player.currentMediaItemIndex - 20                       // history: the last 20 songs are enough
        if (old > 0) app.main.post { if (player.currentMediaItemIndex > 20) player.removeMediaItems(0, player.currentMediaItemIndex - 20) }
        ahead()
        topUp()
    }

    override fun onIsPlayingChanged(isPlaying: Boolean) = emit(JSONObject().put("ev", "state").put("playing", isPlaying))

    override fun onPlaybackStateChanged(state: Int) {
        val id = player.currentMediaItem?.mediaId
        if (state == Player.STATE_READY && id != null && readyFor != id) {
            readyFor = id
            emit(JSONObject().put("ev", "ready").put("dur", player.duration / 1000.0))
        }
        if (state == Player.STATE_ENDED) topUp(ended = true)
    }

    override fun onPlayerError(error: PlaybackException) {
        val cur = player.currentMediaItem ?: return
        val s = songs[cur.mediaId]
        val yt = s?.let { ytOf(it) }.orEmpty()
        val online = app.online()
        val tries = retried.merge(cur.mediaId, 1, Int::plus)!!
        if (yt.isNotEmpty() && online && tries <= 3) {   // an old or refused link: look it up again, same second
            Links.drop(yt)
            player.prepare()
            player.play()
            return
        }
        if (!online) {                                                 // offline: streamed songs wait, the shelf goes on
            for (i in player.mediaItemCount - 1 downTo player.currentMediaItemIndex + 1) {
                if (player.getMediaItemAt(i).localConfiguration?.uri?.scheme != "file") player.removeMediaItem(i)
            }
        }
        if (player.hasNextMediaItem()) {
            player.seekToNextMediaItem()
            player.prepare()
            player.play()
        } else topUp(ended = true)
    }

    /** Looks up the audio links of the next two YouTube songs now, so they start without a wait. */
    private fun ahead() {
        val i = player.currentMediaItemIndex
        for (k in i + 1..minOf(i + 2, player.mediaItemCount - 1)) {
            val uri = player.getMediaItemAt(k).localConfiguration?.uri ?: continue
            if (uri.scheme == "yt") Links.ahead(uri.schemeSpecificPart)
        }
    }

    /** One song or none left: drift on by ourselves (the page usually fills the queue before this). */
    private fun topUp(ended: Boolean = false) {
        val left = player.mediaItemCount - 1 - player.currentMediaItemIndex
        if (left > 1 || topping) return
        val seed = player.currentMediaItem?.mediaId?.let(songs::get)
        if (seed == null) { if (ended) emit(JSONObject().put("ev", "ended")); return }
        topping = true
        val avoid = HashSet(recent).apply { (0 until player.mediaItemCount).forEach { add(player.getMediaItemAt(it).mediaId) } }
        app.work.execute {
            val picks = try {
                if (app.online()) radio(seed, 8, avoid).ifEmpty { shelfRadio(avoid) } else shelfRadio(avoid)
            } catch (e: Exception) { shelfRadio(avoid) }
            app.main.post {
                topping = false
                val stillShort = player.mediaItemCount - 1 - player.currentMediaItemIndex <= 1
                if (picks.isNotEmpty() && stillShort) {
                    val wasEnded = player.playbackState == Player.STATE_ENDED
                    player.addMediaItems(picks.mapNotNull(::item))
                    if (wasEnded && player.hasNextMediaItem()) { player.seekToNextMediaItem(); player.prepare(); player.play() }
                } else if (player.playbackState == Player.STATE_ENDED) emit(JSONObject().put("ev", "ended"))
            }
        }
    }

    /** Offline drift: shelf songs the page told us about, not played lately, in a shuffle. */
    private fun shelfRadio(avoid: Set<String>): List<JSONObject> =
        shelf.known().filter { it.optString("id") !in avoid }.shuffled().take(8)
            .map { JSONObject(it.toString()).put("why", "Drift radio") }

    // ---- telling the page ----

    /** The page's song object for what plays now, with where the last song stopped (for play/skip counts). */
    fun moved(prev: Double = 0.0) {
        val s = player.currentMediaItem?.mediaId?.let(songs::get) ?: return
        emit(JSONObject().put("ev", "moved").put("song", s).put("prev", prev))
    }

    fun tick() {
        val id = player.currentMediaItem?.mediaId
        val o = JSONObject().put("ev", "time")
            .put("id", id ?: JSONObject.NULL)
            .put("playing", player.isPlaying)
            .put("pos", player.currentPosition / 1000.0)
            .put("dur", if (player.duration > 0) player.duration / 1000.0 else 0.0)
        snapshot = o.toString()
        listener?.invoke(o)
    }

    private fun emit(o: JSONObject) {
        tick()
        if (o.optString("ev") != "time") listener?.invoke(o)
    }

    fun release() {
        listener = null
        player.release()
    }

    companion object {
        /** A YouTube song as the page's song object (the same fields as open.js songs, plus yt). */
        fun songJson(s: Song, why: String): JSONObject = JSONObject()
            .put("id", "yt:" + s.id).put("yt", s.id).put("title", s.title).put("artist", s.artist)
            .put("album", "").put("secs", s.secs).put("art", s.art).put("remote", true).put("source", "youtube")
            .put("page", s.page()).put("stream", "").put("why", why)

        /** Drift radio online: YouTube's mix of the seed (found on YouTube first when it is a shelf song). */
        fun radio(seed: JSONObject, n: Int, avoid: Set<String>): List<JSONObject> {
            var yt = ytOf(seed)
            if (yt.isEmpty()) {
                val q = "${seed.optString("artist")} ${seed.optString("title")}".trim()
                if (q.isEmpty()) return emptyList()
                yt = Tube.search(q, 1).firstOrNull()?.id ?: return emptyList()
            }
            val skip = avoid.map { it.removePrefix("yt:") }.toSet()
            return Tube.radio(yt, n, skip).map { songJson(it, "Drifting online") }
        }

        /** The YouTube video id of a page song ("yt" field or a "yt:" id), "" for shelf songs. */
        fun ytOf(s: JSONObject): String {
            val yt = s.optString("yt")
            if (yt.isNotEmpty()) return yt
            val id = s.optString("id")
            return if (id.startsWith("yt:")) id.substring(3) else ""
        }

        fun songsJson(list: List<JSONObject>) = JSONArray().apply { list.forEach(::put) }
    }
}

/** Audio links by video id, looked up once and kept until shortly before YouTube expires them. */
object Links {
    private val cache = ConcurrentHashMap<String, Audio>()
    private val locks = ConcurrentHashMap<String, Any>()

    fun get(id: String): Audio {
        cache[id]?.takeIf { it.fresh() }?.let { return it }
        synchronized(locks.getOrPut(id) { Any() }) {
            cache[id]?.takeIf { it.fresh() }?.let { return it }
            return Tube.audio(id).also { cache[id] = it }
        }
    }

    fun ahead(id: String) = DriftApp.app.work.execute { runCatching { get(id) } }
    fun drop(id: String) { cache.remove(id) }
}
