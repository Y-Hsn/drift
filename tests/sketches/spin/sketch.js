var fps = 25;
function draw(g, song, t) {
  var W = g.width, H = g.height, cx = W / 2, cy = H * 0.45, R = Math.min(W, H) * 0.33;
  g.layer('deck' + W + 'x' + H + song.title + song.shape.length, function (g) {
    g.fill('#2b1d14').rect(0, 0, W, H);
    g.fill('#3a2618').rect(cx - R * 1.4, cy - R * 1.15, R * 2.8, R * 2.3, 14);
    g.fill('#0c0c0e').circle(cx, cy, R);
    g.noFill();
    for (var i = 0; i < 160; i++) {          // the grooves: the song's loudness as rings
      var rr = R * 0.36 + (R * 0.6) * i / 159;
      g.stroke(drift.rgba(255, 255, 255, 0.03 + 0.09 * (song.shape[i] || 0)), 1).circle(cx, cy, rr);
    }
    g.noStroke();
  });
  g.save().translate(cx, cy).rotate(t * 200);   // 33 rpm
  if (song.cover) g.image('cover', -R * 0.34, -R * 0.34, R * 0.68, R * 0.68, { radius: R * 0.34 });
  else g.fill(song.colors.accent).circle(0, 0, R * 0.34)
       .fill(song.colors.dark).text(song.title, 0, -R * 0.12, { size: 11, align: 'center', width: R * 0.5 });
  g.fill('#0c0c0e').circle(0, 0, 4);                // the spindle
  g.restore();
  var a = -30 + 25 * song.progress;               // the tonearm follows the song
  g.save().translate(cx + R * 1.15, cy - R * 0.9).rotate(a);
  g.stroke('#c9c9cf', 5).line(0, 0, 0, R * 1.5).noStroke();
  g.restore();
}
