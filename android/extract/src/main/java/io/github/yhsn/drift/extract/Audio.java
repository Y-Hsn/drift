package io.github.yhsn.drift.extract;

/** A playable audio-only link for one song. YouTube's links stop working after some hours: see {@link #expires}. */
public final class Audio {
    public final String id, url, mime, codec, userAgent;   // userAgent: the one YouTube expects for this link
    public final int bitrate;
    public final long expires;      // ms since 1970

    Audio(String id, String url, String mime, String codec, int bitrate, long expires, String userAgent) {
        this.id = id;
        this.url = url;
        this.mime = mime;
        this.codec = codec;
        this.bitrate = bitrate;
        this.expires = expires;
        this.userAgent = userAgent;
    }

    public boolean fresh() {
        return System.currentTimeMillis() < expires - 10 * 60_000L;
    }

    @Override
    public String toString() {
        return mime + " " + codec + " " + bitrate / 1000 + " kbps";
    }
}
