package io.github.yhsn.drift.extract;

/** One YouTube song as the phone app sees it (id = the 11-letter video id). */
public final class Song {
    public final String id, title, artist, art;
    public final long secs;

    public Song(String id, String title, String artist, long secs, String art) {
        this.id = id;
        this.title = title;
        this.artist = artist;
        this.secs = secs;
        this.art = art;
    }

    public String page() {
        return "https://www.youtube.com/watch?v=" + id;
    }

    @Override
    public String toString() {
        return artist + " - " + title + " (" + secs + " s, " + id + ")";
    }
}
