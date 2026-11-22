package io.github.yhsn.drift.extract;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.io.InputStream;
import java.util.List;
import java.util.Set;

import okhttp3.Request;
import okhttp3.Response;

import org.junit.BeforeClass;
import org.junit.Test;

/** Talks to YouTube for real (internet needed): search -> audio link -> the link answers with sound bytes. */
public class LiveTubeTest {
    static final Net net = new Net();

    @BeforeClass
    public static void setUp() {
        Tube.init(net);
    }

    @Test
    public void searchStreamAndRadio() throws Exception {
        long t0 = System.currentTimeMillis();
        List<Song> hits = Tube.search("The Weeknd Blinding Lights", 8);
        System.out.println("search " + (System.currentTimeMillis() - t0) + " ms");
        hits.forEach(s -> System.out.println("  " + s));
        assertFalse("no results", hits.isEmpty());
        Song top = hits.get(0);
        assertTrue("top hit is not the song: " + top, top.title.toLowerCase().contains("blinding lights"));
        assertTrue("top hit is not The Weeknd: " + top, top.artist.toLowerCase().contains("weeknd"));

        t0 = System.currentTimeMillis();
        Audio a = Tube.audio(top.id);
        System.out.println("audio " + (System.currentTimeMillis() - t0) + " ms: " + a + ", expires in "
                + (a.expires - System.currentTimeMillis()) / 60000 + " min");
        System.out.println("  client c=" + a.url.replaceAll(".*[?&]c=([A-Z_]+).*", "$1") + ", ua=" + a.userAgent);
        assertTrue(a.fresh());

        // what ExoPlayer does: an open-ended range request from the start, then a jump into the middle
        for (String range : new String[] {"bytes=0-", "bytes=1000000-1999999"}) {
            t0 = System.currentTimeMillis();
            Request req = new Request.Builder().url(a.url).header("User-Agent", a.userAgent).header("Range", range).build();
            try (Response r = net.http.newCall(req).execute()) {
                byte[] head = new byte[1 << 20];
                int got = 0, n;
                InputStream in = r.body().byteStream();
                while (got < head.length && (n = in.read(head, got, head.length - got)) > 0) got += n;
                long ms = System.currentTimeMillis() - t0;
                System.out.println("  " + range + " -> HTTP " + r.code() + " " + r.header("Content-Type") + " "
                        + r.header("Content-Range") + ", first " + got / 1024 + " KB in " + ms + " ms");
                assertTrue("HTTP " + r.code(), r.code() == 200 || r.code() == 206);
                assertTrue("too little sound: " + got, got > 100_000);
                if (range.startsWith("bytes=0") && a.mime.contains("mp4")) {
                    assertEquals("not an mp4 file", "ftyp", new String(head, 4, 4, "ISO-8859-1"));
                }
            }
        }

        t0 = System.currentTimeMillis();
        List<Song> radio = Tube.radio(top.id, 10, Set.of());
        System.out.println("radio " + (System.currentTimeMillis() - t0) + " ms");
        radio.forEach(s -> System.out.println("  " + s));
        assertTrue("radio too short: " + radio.size(), radio.size() >= 5);
    }
}
