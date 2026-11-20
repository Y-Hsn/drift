// A new Drift sketch: a blank page to start from.
//
// This file draws the Now page. Change anything and save: Drift redraws at once.
// The guide (Settings -> Sketches -> How to make one) explains every call used here.
//
// What you get, every time Drift asks for a picture:
//   g      the canvas: g.width, g.height, g.top (the index along the top), g.player (where Drift's player sits)
//   song   the song: title, artist, cover, shape (its loudness, 160 values 0..1), progress (0..1),
//          position / duration (seconds), line (the sung line), playing, calm, next, colors (from the cover)
//   t      seconds since the room opened (for motion; set fps above 0 to use it)

var fps = 0;            // 0: draw only when something changes (lightest). Up to 30 for motion.

function draw(g, song, t) {
  var c = song.colors;
  var W = g.width, H = g.height;
  var bottom = g.player ? g.player.y : H;          // keep clear of Drift's player strip

  // 1. The ground: the cover's darkest colour, a soft glow of its own colour from below.
  //    It only changes with the song, so it is kept in a layer (drawn once, reused every frame).
  g.layer('ground ' + song.title + W + 'x' + H + c.background, function (g) {
    g.linear(0, 0, 0, H, [[0, c.background], [1, drift.mix(c.background, c.glow, 0.35)]]);
    g.rect(0, 0, W, H);
  });

  // Nothing playing: a short greeting.
  if (!song.has) {
    g.fill(c.light).text('Nothing playing', W / 2, H * 0.42, { size: 34, font: 'title', align: 'center' });
    g.fill(drift.alpha(c.light, 0.6)).text('Press Start Drift in Explore, or pick a song', W / 2, H * 0.42 + 56,
      { size: 11, font: 'caps', spacing: 2, align: 'center', caps: true });
    return;
  }

  // 2. The cover, as it is.
  var side = Math.min(W * 0.32, (bottom - g.top) * 0.5);
  var cx = W * 0.5 - side / 2, cy = g.top + (bottom - g.top) * 0.12;
  if (song.cover) g.image(song.cover, cx, cy, side, side, { radius: 4 });

  // 3. The song's shape: one line across the page; the part already played is lit.
  //    Click or drag on it to move through the song (see press/drag below).
  var y0 = cy + side + 70, amp = 46;
  seekBar.y = y0 - amp / 2;
  var played = [], rest = [];
  for (var i = 0; i < song.shape.length; i++) {
    var x = 40 + (W - 80) * i / (song.shape.length - 1);
    var y = y0 - song.shape[i] * amp;
    (i / (song.shape.length - 1) <= song.progress ? played : rest).push(x, y);
  }
  if (played.length > 1) rest.unshift(played[played.length - 2], played[played.length - 1]);
  g.noFill();
  g.stroke(drift.alpha(c.light, 0.25), 1.5).curve(rest);
  g.stroke(c.accent, 2.5).curve(played);
  g.noStroke();

  // 4. The words: title, artist, the line being sung.
  g.fill(c.light).text(song.title, W / 2, y0 + 34, { size: 30, font: 'title', align: 'center', width: W - 80 });
  g.fill(drift.alpha(c.light, 0.65)).text(song.artist, W / 2, y0 + 86,
    { size: 10, font: 'caps', spacing: 3, align: 'center', caps: true, width: W - 80 });
  if (song.line && !song.calm)
    g.fill(drift.alpha(c.light, 0.85)).text(song.line, W / 2, y0 + 120,
      { size: 15, font: 'title', italic: true, align: 'center', width: W - 80 });
}

// The ground behind every other page (Explore, Library...). Optional. Drawn once per song and size.
function room(g, song) {
  var c = song.colors;
  g.linear(0, 0, 0, g.height, [[0, c.background], [1, drift.mix(c.background, c.glow, 0.25)]]);
  g.rect(0, 0, g.width, g.height);
}

// The mouse. All optional. Here the shape line is the seek bar: draw() remembers where it is.
var seekBar = { y: -100 };

function onBar(x, y) { return Math.abs(y - seekBar.y) < 40 && x >= 40 && x <= g.width - 40; }
function seekTo(x) { drift.seek(drift.clamp((x - 40) / (g.width - 80), 0, 1)); }

function press(x, y, song) { if (onBar(x, y)) seekTo(x); }
function drag(x, y, song) { if (onBar(x, y)) seekTo(x); }
function hover(x, y, song) { drift.cursor(onBar(x, y) ? 'hand' : 'arrow'); }
