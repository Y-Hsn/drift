package io.github.yhsn.drift.extract;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.schabi.newpipe.extractor.Image;
import org.schabi.newpipe.extractor.InfoItem;
import org.schabi.newpipe.extractor.MediaFormat;
import org.schabi.newpipe.extractor.NewPipe;
import org.schabi.newpipe.extractor.ServiceList;
import org.schabi.newpipe.extractor.localization.ContentCountry;
import org.schabi.newpipe.extractor.localization.Localization;
import org.schabi.newpipe.extractor.playlist.PlaylistInfo;
import org.schabi.newpipe.extractor.search.SearchExtractor;
import org.schabi.newpipe.extractor.services.youtube.YoutubeParsingHelper;
import org.schabi.newpipe.extractor.services.youtube.linkHandler.YoutubeSearchQueryHandlerFactory;
import org.schabi.newpipe.extractor.stream.AudioStream;
import org.schabi.newpipe.extractor.stream.DeliveryMethod;
import org.schabi.newpipe.extractor.stream.StreamInfo;
import org.schabi.newpipe.extractor.stream.StreamInfoItem;

/**
 * YouTube for Drift on Android: search (YouTube Music songs first), Drift radio (YouTube's own mix of a song, then
 * its related songs) and an audio-only link that ExoPlayer plays (m4a/AAC first, opus/webm second).
 * Everything here blocks: call it off the main thread.
 */
public final class Tube {
    public static final long MIN_SECS = 60, MAX_SECS = 12 * 60;     // songs, not hour-long mixes or short clips
    private static final Pattern VIDEO_ID = Pattern.compile("(?:v=|youtu\\.be/|/shorts/)([A-Za-z0-9_-]{11})");
    private static final Pattern EXPIRE = Pattern.compile("[?&]expire=(\\d+)");
    private static volatile boolean ready;

    private Tube() {}

    public static synchronized void init(Net net) {
        if (ready) return;
        Locale l = Locale.getDefault();
        String country = l.getCountry().isEmpty() ? "US" : l.getCountry();
        NewPipe.init(net, new Localization("en", country), new ContentCountry(country));
        ready = true;
    }

    public static String idOf(String url) {
        Matcher m = VIDEO_ID.matcher(url == null ? "" : url);
        return m.find() ? m.group(1) : null;
    }

    /** Songs for a query: YouTube Music songs, topped up with plain YouTube videos when that finds few. */
    public static List<Song> search(String query, int n) {
        List<Song> out = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        searchInto(query, YoutubeSearchQueryHandlerFactory.MUSIC_SONGS, n, out, seen);
        if (out.size() < Math.min(4, n)) searchInto(query, YoutubeSearchQueryHandlerFactory.VIDEOS, n, out, seen);
        return out;
    }

    private static void searchInto(String query, String filter, int n, List<Song> out, Set<String> seen) {
        try {
            SearchExtractor ex = ServiceList.YouTube.getSearchExtractor(query, List.of(filter), "");
            ex.fetchPage();
            for (InfoItem it : ex.getInitialPage().getItems()) {
                if (out.size() >= n) break;
                Song s = song(it);
                if (s != null && seen.add(s.id)) out.add(s);
            }
        } catch (Exception e) {
            // one filter failing (YouTube changes its pages now and then) leaves the other
        }
    }

    /** Drift radio from a song: YouTube's mix of it (a radio YouTube builds), then its related songs. */
    public static List<Song> radio(String videoId, int n, Set<String> avoid) throws Exception {
        List<Song> out = new ArrayList<>();
        Set<String> seen = new HashSet<>(avoid);
        seen.add(videoId);
        try {
            PlaylistInfo mix = PlaylistInfo.getInfo(ServiceList.YouTube,
                    "https://www.youtube.com/watch?v=" + videoId + "&list=RD" + videoId);
            for (StreamInfoItem it : mix.getRelatedItems()) {
                if (out.size() >= n) break;
                Song s = song(it);
                if (s != null && seen.add(s.id)) out.add(s);
            }
        } catch (Exception e) {
            // no mix for this one: related songs below
        }
        if (out.size() < n) {
            StreamInfo info = StreamInfo.getInfo(ServiceList.YouTube, "https://www.youtube.com/watch?v=" + videoId);
            for (InfoItem it : info.getRelatedItems()) {
                if (out.size() >= n) break;
                Song s = song(it);
                if (s != null && seen.add(s.id)) out.add(s);
            }
        }
        return out;
    }

    /** The audio-only link to play: progressive m4a (AAC) first, then opus, highest bitrate of the kind. */
    public static Audio audio(String videoId) throws Exception {
        StreamInfo info = StreamInfo.getInfo(ServiceList.YouTube, "https://www.youtube.com/watch?v=" + videoId);
        AudioStream best = info.getAudioStreams().stream()
                .filter(a -> a.isUrl() && a.getDeliveryMethod() == DeliveryMethod.PROGRESSIVE_HTTP)
                .filter(a -> a.getAudioTrackType() == null || a.getAudioTrackType().name().equals("ORIGINAL"))
                .max(Comparator.comparingInt((AudioStream a) -> a.getFormat() == MediaFormat.M4A ? 1 : 0)
                        .thenComparingInt(AudioStream::getAverageBitrate))
                .orElseThrow(() -> new IllegalStateException("no audio stream for " + videoId));
        String url = best.getContent();
        Matcher m = EXPIRE.matcher(url);
        long expires = m.find() ? Long.parseLong(m.group(1)) * 1000L : System.currentTimeMillis() + 3 * 3600_000L;
        MediaFormat f = best.getFormat();
        // links made for the visionOS client only answer that client's user agent
        String ua = YoutubeParsingHelper.isVisionOsStreamingUrl(url)
                ? YoutubeParsingHelper.getVisionOsUserAgent(NewPipe.getPreferredLocalization()) : Net.UA;
        return new Audio(videoId, url, f == null ? "audio/mp4" : f.getMimeType(), best.getCodec(),
                best.getAverageBitrate(), expires, ua);
    }

    static Song song(InfoItem it) {
        if (!(it instanceof StreamInfoItem)) return null;
        StreamInfoItem s = (StreamInfoItem) it;
        String id = idOf(s.getUrl());
        long secs = s.getDuration();
        if (id == null || (secs > 0 && (secs < MIN_SECS || secs > MAX_SECS))) return null;
        String artist = s.getUploaderName() == null ? "" : s.getUploaderName().replaceFirst(" - Topic$", "");
        return new Song(id, title(s.getName(), artist), artist, Math.max(0, secs), art(s.getThumbnails()));
    }

    private static final Pattern VIDEO_WORDS = Pattern.compile(
            "\\s*[(\\[](official\\s+)?(music\\s+|lyric\\s+|audio\\s+|hd\\s+|4k\\s+)?(video|audio|lyrics?|visuali[sz]er)[)\\]]\\s*$",
            Pattern.CASE_INSENSITIVE);

    /** "The Weeknd - Starboy (Official Video)" by The Weeknd -> "Starboy". */
    static String title(String name, String artist) {
        String t = name == null ? "" : name.trim();
        if (!artist.isEmpty() && t.toLowerCase(Locale.ROOT).startsWith(artist.toLowerCase(Locale.ROOT) + " - ")) {
            t = t.substring(artist.length() + 3);
        }
        String clean = VIDEO_WORDS.matcher(t).replaceFirst("");
        return clean.isEmpty() ? t : clean;
    }

    /** The biggest thumbnail up to 720 px high (enough for the lock screen, not a waste of data). */
    static String art(List<Image> images) {
        Image best = null;
        for (Image i : images) {
            int h = i.getHeight() <= 0 ? 360 : i.getHeight();
            int bh = best == null ? -1 : (best.getHeight() <= 0 ? 360 : best.getHeight());
            if (h <= 720 && h > bh) best = i;
        }
        if (best == null && !images.isEmpty()) best = images.get(0);
        return best == null ? "" : best.getUrl();
    }
}
