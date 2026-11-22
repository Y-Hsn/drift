package io.github.yhsn.drift

import android.app.PendingIntent
import android.content.Intent
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService

/**
 * Keeps the music going: a foreground service with a media session, so Android shows the song on the lock screen
 * and in the notification (title, artwork, play/pause/next/previous) and lets it play with the screen off.
 */
class PlaybackService : MediaSessionService() {
    private var session: MediaSession? = null
    private var engine: Engine? = null

    override fun onCreate() {
        super.onCreate()
        val e = Engine(this)
        val open = PendingIntent.getActivity(this, 0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        session = MediaSession.Builder(this, e.player).setSessionActivity(open).build()
        engine = e
        Hub.engine = e
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = session

    override fun onDestroy() {
        Hub.engine = null
        session?.release()
        session = null
        engine?.release()
        engine = null
        super.onDestroy()
    }
}
