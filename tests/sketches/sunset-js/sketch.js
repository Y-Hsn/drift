// Sunset, redrawn from scratch as a Drift sketch (only the artist API, no Drift code).
// Every song is a landscape: its loudness is the hills, its cover the sun crossing the sky, a lake mirrors it.

var fps = 0;
var hoverX = -1;

function smooth(a, n) {            // a slower echo of the shape: a moving average, n steps wide
  var out = [];
  for (var i = 0; i < a.length; i++) {
    var s = 0, c = 0;
    for (var k = -n; k <= n; k++) { var j = i + k; if (j >= 0 && j < a.length) { s += a[j]; c++; } }
    out.push(s / c);
  }
  return out;
}

function ranges(song) {             // three ranges of hills: far and hazy, middle, near = the song itself
  var shape = song.shape.length ? song.shape : [];
  if (!shape.length) for (var i = 0; i < 160; i++) shape.push(0.25 + 0.1 * drift.noise(i / 14, 3));
  var rnd = drift.random(song.title);
  var seed = rnd() * 100;
  var far = smooth(shape, 14).map(function (v, i) { return 0.35 + 0.5 * v + 0.25 * drift.noise(i / 22, seed); });
  var mid = smooth(shape, 6).map(function (v, i) { return 0.25 + 0.6 * v + 0.15 * drift.noise(i / 11, seed + 7); });
  return { far: far, mid: mid, near: smooth(shape, 1) };
}

function ridge(vals, W, hy, amp) {  // points of a range along the horizon
  var pts = [];
  for (var i = 0; i < vals.length; i++) pts.push(W * i / (vals.length - 1), hy - vals[i] * amp);
  return pts;
}

function hill(g, pts, W, hy, color) {   // a filled range, closed along the horizon
  g.fill(color).noStroke();
  g.curve(pts.concat([W, hy + 1, 0, hy + 1]), true);
}

function draw(g, song, t) {
  var c = song.colors, W = g.width, H = g.height;
  var hy = Math.round(H * 0.56);                     // the horizon
  var k = 0.58;                                      // how strongly the lake mirrors
  var amp = { far: H * 0.2, mid: H * 0.14, near: H * 0.09 };
  var R = ranges(song);
  var p = song.progress;

  // the sky and its stars: change only with the song and the size
  g.layer('sky ' + song.title + W + 'x' + H + c.background + c.glow, function (g) {
    g.linear(0, 0, 0, hy, [[0, c.background], [0.62, c.surface], [1, c.glow]]).rect(0, 0, W, hy + 1);
    var rnd = drift.random('stars' + song.title);
    for (var i = 0; i < 140; i++) {
      var x = rnd() * W, y = Math.pow(rnd(), 1.6) * (hy - amp.far) * 0.9;
      g.fill(drift.alpha(c.light, (0.15 + 0.55 * rnd()) * song.night)).circle(x, y, 0.4 + rnd() * 0.8);
    }
    g.linear(0, hy, 0, H, [[0, c.shallow], [1, c.water]]).rect(0, hy, W, H - hy);
  });

  // the sun: the cover, rising left at the start, setting right at the end; its glow follows the loudness
  var sx = W * (0.1 + 0.8 * p), sy = hy - amp.far * 0.9 - Math.sin(Math.PI * p) * (hy - amp.far - g.top - 120) * 0.85;
  var r = Math.min(W, H) * 0.085;
  var loud = song.shape.length ? song.shape[Math.min(159, Math.floor(p * 160))] : 0.4;
  g.radial(sx, sy, r * (3.2 + 2.5 * loud), [[0, drift.alpha(c.accent, 0.55)], [0.35, drift.alpha(c.accent, 0.18)], [1, drift.alpha(c.accent, 0)]]);
  g.circle(sx, sy, r * (3.2 + 2.5 * loud));
  if (song.cover) g.image(song.cover, sx - r, sy - r, 2 * r, 2 * r, { radius: r });
  else g.fill(drift.lighter(c.accent, 0.3)).circle(sx, sy, r);
  // its light on the water
  for (var i = 0; i < 9; i++) {
    var w = r * (1.6 - i * 0.12);
    g.fill(drift.alpha(c.accent, 0.22 - i * 0.02)).rect(sx - w / 2, hy + 8 + i * 9, w, 2, 1);
  }

  // the hills and their mirror in the lake: change only with the song
  g.layer('land ' + song.title + song.shape.length + W + 'x' + H + c.dark, function (g) {
    var layers = [[R.far, amp.far, c.soft], [R.mid, amp.mid, c.mid], [R.near, amp.near, c.dark]];
    layers.forEach(function (L) { hill(g, ridge(L[0], W, hy, L[1]), W, hy, L[2]); });
    g.save().clip(0, hy, W, H - hy).translate(0, hy * (1 + k)).scale(1, -k).alpha(0.55);
    layers.forEach(function (L) { hill(g, ridge(L[0], W, hy, L[1]), W, hy, L[2]); });
    g.restore();
  });

  // the played shore: lit up to the playhead, with a rim of light and a lantern
  var near = ridge(R.near, W, hy, amp.near);
  var px = p * W;
  if (song.has && px > 0) {
    g.save().clip(0, 0, px, H);
    hill(g, near, W, hy, c.lit);
    g.noFill().stroke(drift.alpha(c.light, 0.7), 1.4).curve(near).noStroke();
    g.restore();
    var ly = hy - R.near[Math.min(159, Math.floor(p * 159))] * amp.near;
    g.radial(px, ly, 14, [[0, drift.alpha(c.light, 0.9)], [1, drift.alpha(c.light, 0)]]).circle(px, ly, 14);
    g.fill('#ffffff').circle(px, ly, 2.6);
  }
  if (song.best >= 0) {                                   // the best part: a star over the shore
    var bx = song.best * W, by = hy - R.near[Math.floor(song.best * 159)] * amp.near - 16;
    g.fill(c.light).path('M ' + bx + ' ' + (by - 5) + ' L ' + (bx + 1.4) + ' ' + (by - 1.4) + ' L ' + (bx + 5) + ' ' + by +
      ' L ' + (bx + 1.4) + ' ' + (by + 1.4) + ' L ' + bx + ' ' + (by + 5) + ' L ' + (bx - 1.4) + ' ' + (by + 1.4) +
      ' L ' + (bx - 5) + ' ' + by + ' L ' + (bx - 1.4) + ' ' + (by - 1.4) + ' Z');
  }
  if (hoverX >= 0 && song.duration) {                      // hovering the shore: the time there
    var s = Math.round(hoverX / W * song.duration);
    var label = Math.floor(s / 60) + ':' + ('0' + s % 60).slice(-2);
    g.fill(drift.alpha(c.light, 0.9)).text(label, hoverX, hy - amp.near - 34, { size: 9, font: 'caps', align: 'center', spacing: 1 });
  }

  // the words
  var pad = Math.max(40, W * 0.055);
  if (!song.has) {
    g.fill(c.light).text('Good evening', pad, hy + H * 0.12, { size: 44, font: 'title' });
    return;
  }
  if (!song.calm && song.line) {
    g.fill(drift.alpha(c.light, 0.92)).text(song.line, W / 2, g.top + 28, { size: 15, font: 'title', italic: true, align: 'center', width: W * 0.7 });
    g.fill(drift.alpha(c.light, 0.35)).text(song.nextLine, W / 2, g.top + 62, { size: 12, font: 'title', italic: true, align: 'center', width: W * 0.7 });
  }
  var ty = hy + H * 0.13;
  var m = Math.floor(song.duration / 60) + ':' + ('0' + Math.floor(song.duration % 60)).slice(-2);
  g.fill(c.accent).text('Drifting  ·  ' + m, pad, ty - 26, { size: 7.5, font: 'caps', weight: 700, spacing: 2.2, caps: true });
  g.fill('#f3efe9').text(song.title, pad, ty, { size: 44, font: 'title', width: W - 2 * pad });
  g.fill(drift.alpha('#f3efe9', 0.75)).text(song.artist, pad, ty + 72, { size: 9.5, font: 'caps', spacing: 3.2, caps: true });
  if (!song.calm && song.next.length && g.player) {        // THEN: what comes next, along the bottom
    var y = g.player.y - 26, x = pad;
    g.fill(drift.alpha(c.light, 0.5)).text('THEN', x, y, { size: 7.5, font: 'caps', weight: 700, spacing: 2, baseline: 'middle' });
    x += 46;
    for (var i = 0; i < song.next.length && x < W - 200; i++) {
      g.fill('#f3efe9').text(song.next[i].title, x, y, { size: 10.5, weight: 600, baseline: 'middle' });
      x += drift.measure(song.next[i].title, { size: 10.5, weight: 600 }) + 8;
      g.fill(drift.alpha('#f3efe9', 0.5)).text(song.next[i].artist, x, y, { size: 10, baseline: 'middle' });
      x += drift.measure(song.next[i].artist, { size: 10 }) + 28;
    }
  }
}

function room(g, song) {             // every other page: the same sky after dusk
  var c = song.colors;
  g.linear(0, 0, 0, g.height, [[0, c.background], [0.55, drift.mix(c.background, c.water, 0.5)], [1, c.water]]).rect(0, 0, g.width, g.height);
  g.radial(g.width * 0.86, g.height * 0.02, Math.max(320, g.width * 0.55), [[0, drift.alpha(c.glow, 0.22)], [1, drift.alpha(c.glow, 0)]]).rect(0, 0, g.width, g.height);
}

// the shore is the seek bar
function onShore(y) { var hy = g.height * 0.56; return y > hy - g.height * 0.09 - 18 && y < hy + 22; }
function hover(x, y) { var nx = onShore(y) ? x : -1; drift.cursor(nx >= 0 ? 'hand' : 'arrow'); if (nx !== hoverX) { hoverX = nx; drift.redraw(); } }
function press(x, y) { if (onShore(y)) drift.seek(x / g.width); }
function drag(x, y) { hoverX = x; drift.seek(x / g.width); }
function doubleClick() { drift.calm(); }
