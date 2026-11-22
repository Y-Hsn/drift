package io.github.yhsn.drift.extract;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

/** Song titles from YouTube video names (offline). */
public class TitleTest {
    @Test
    public void cleansVideoNames() {
        assertEquals("Can't Feel My Face", Tube.title("The Weeknd - Can't Feel My Face (Official Video)", "The Weeknd"));
        assertEquals("Starboy", Tube.title("Starboy [Official Music Video]", "The Weeknd"));
        assertEquals("Blinding Lights", Tube.title("Blinding Lights", "The Weeknd"));
        assertEquals("Mask Off", Tube.title("Mask Off (Lyrics)", "Future"));
        assertEquals("Video", Tube.title("Video", "Lana Del Rey"));
        assertEquals("Save Your Tears (Remix)", Tube.title("Save Your Tears (Remix)", "The Weeknd"));
    }
}
