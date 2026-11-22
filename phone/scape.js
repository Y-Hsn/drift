// The Now screen as on the PC: the song's loudness is the hills (and the seek bar), the cover is the sun over a lake.
// Drawn once per song into an offscreen canvas; while playing only the played part of the ridge is repainted.
export function palette(img) {
  const c = document.createElement('canvas'); c.width = c.height = 24;
  const g = c.getContext('2d', { willReadFrequently: true });
  try { g.drawImage(img, 0, 0, 24, 24); } catch (e) { return null; }
  let d; try { d = g.getImageData(0, 0, 24, 24).data; } catch (e) { return null; }
  let best = null, bestScore = -1, r = 0, gg = 0, b = 0;
  for (let i = 0; i < d.length; i += 4) {
    r += d[i]; gg += d[i + 1]; b += d[i + 2];
    const mx = Math.max(d[i], d[i + 1], d[i + 2]), mn = Math.min(d[i], d[i + 1], d[i + 2]);
    const sat = mx ? (mx - mn) / mx : 0, score = sat * (mx / 255) * (mx > 60 ? 1 : 0.2);
    if (score > bestScore) { bestScore = score; best = [d[i], d[i + 1], d[i + 2]]; }
  }
  const n = d.length / 4;
  return { accent: best || [240, 160, 90], avg: [r / n, gg / n, b / n] };
}

const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
const css = c => `rgb(${c[0]},${c[1]},${c[2]})`;

function shapeOf(song) {
  if (song.shape) {
    try { return Uint8Array.from(atob(song.shape), ch => ch.charCodeAt(0)); } catch (e) {}
  }
  let h = 2166136261; for (const ch of song.id) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  // no measured shape yet: gentle hills of its own, the same every time for this song
  const out = new Uint8Array(160), phase = (h & 255) / 40;
  let wobble = 0;
  for (let i = 0; i < 160; i++) {
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    wobble = wobble * 0.85 + (((h >>> 8) & 31) - 16) * 0.15;
    const x = i / 159, swell = 0.5 + 0.28 * Math.sin(x * Math.PI * 2.6 + phase) * Math.sin(x * Math.PI) + 0.08 * Math.sin(x * 19 + phase);
    out[i] = Math.max(20, Math.min(255, 255 * swell + wobble * 2));
  }
  return out;
}

export class Scape {
  constructor(canvas) {
    this.c = canvas; this.g = canvas.getContext('2d');
    this.song = null; this.cover = null; this.pal = null; this.base = null; this.frac = 0;
    new ResizeObserver(() => this.build()).observe(canvas);
  }
  set(song, coverImg) {
    this.song = song; this.cover = coverImg && coverImg.naturalWidth ? coverImg : null;
    this.pal = this.cover ? palette(this.cover) : null;
    this.shape = song ? shapeOf(song) : null;
    this.build();
  }
  colors() {
    const accent = this.pal ? this.pal.accent : [240, 160, 90];
    const night = [12, 15, 29];
    return { accent, skyTop: mix(night, accent, 0.10), skyLow: mix(night, accent, 0.42),
             hill: mix([30, 36, 62], accent, 0.18), lit: mix([236, 228, 214], accent, 0.35), lake: mix(night, accent, 0.06) };
  }
  ridge(w, top, bottom) {
    const pts = [], s = this.shape, n = s.length;
    for (let i = 0; i < n; i++) {
      const x = (i / (n - 1)) * w, v = (s[Math.max(0, i - 1)] + 2 * s[i] + s[Math.min(n - 1, i + 1)]) / 4 / 255;
      pts.push([x, bottom - v * (bottom - top)]);
    }
    return pts;
  }
  build() {
    const c = this.c, dpr = Math.min(2, devicePixelRatio || 1);
    const w = c.clientWidth, h = c.clientHeight;
    if (!w || !h) return;
    c.width = w * dpr; c.height = h * dpr;
    const off = document.createElement('canvas'); off.width = c.width; off.height = c.height;
    const g = off.getContext('2d'); g.scale(dpr, dpr);
    const k = this.colors(), horizon = h * 0.68;
    const sky = g.createLinearGradient(0, 0, 0, horizon);
    sky.addColorStop(0, css(k.skyTop)); sky.addColorStop(1, css(k.skyLow));
    g.fillStyle = sky; g.fillRect(0, 0, w, horizon);
    // the sun: the cover, round, low over the hills
    const r = Math.min(w * 0.26, h * 0.2), cx = w * 0.5, cy = horizon - h * 0.2;
    g.save(); g.shadowColor = css(k.accent); g.shadowBlur = 40;
    g.beginPath(); g.arc(cx, cy, r, 0, Math.PI * 2); g.fillStyle = css(mix(k.accent, [255, 255, 255], 0.25)); g.fill(); g.restore();
    if (this.cover) {
      g.save(); g.beginPath(); g.arc(cx, cy, r, 0, Math.PI * 2); g.clip();
      const s = Math.max(2 * r / this.cover.naturalWidth, 2 * r / this.cover.naturalHeight);
      g.drawImage(this.cover, cx - this.cover.naturalWidth * s / 2, cy - this.cover.naturalHeight * s / 2, this.cover.naturalWidth * s, this.cover.naturalHeight * s);
      g.restore();
    }
    // lake below the horizon, with the sun's light on it
    const lake = g.createLinearGradient(0, horizon, 0, h);
    lake.addColorStop(0, css(mix(k.lake, k.accent, 0.18))); lake.addColorStop(1, css(k.lake));
    g.fillStyle = lake; g.fillRect(0, horizon, w, h - horizon);
    g.globalAlpha = 0.22; g.fillStyle = css(k.accent);
    for (let i = 0; i < 9; i++) { const y = horizon + 6 + i * 7, half = r * (1 - i / 10); g.fillRect(cx - half, y, half * 2, 2); }
    g.globalAlpha = 1;
    this.base = off; this.k = k; this.horizon = horizon;
    if (this.shape) this.pts = this.ridge(w, horizon - h * 0.2, horizon);
    this.draw(this.frac, true);
  }
  draw(frac, force = false) {
    if (!this.base || !this.pts) return;
    if (!force && Math.abs(frac - this.frac) * this.c.clientWidth < 0.5) return;
    this.frac = frac;
    const g = this.g, dpr = this.c.width / this.c.clientWidth, w = this.c.clientWidth, hz = this.horizon;
    g.setTransform(1, 0, 0, 1, 0, 0); g.drawImage(this.base, 0, 0); g.scale(dpr, dpr);
    const hill = () => { g.beginPath(); g.moveTo(0, hz); this.pts.forEach(p => g.lineTo(p[0], p[1])); g.lineTo(w, hz); g.closePath(); };
    hill(); g.fillStyle = css(this.k.hill); g.fill();
    g.save(); g.beginPath(); g.rect(0, 0, w * frac, hz); g.clip();
    hill(); g.fillStyle = css(mix(this.k.hill, this.k.accent, 0.45)); g.fill();
    g.beginPath(); this.pts.forEach((p, i) => i ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1]));
    g.strokeStyle = css(this.k.lit); g.lineWidth = 1.5; g.stroke(); g.restore();
    const x = w * frac, i = Math.min(this.pts.length - 1, Math.round(frac * (this.pts.length - 1)));
    g.beginPath(); g.arc(x, this.pts[i][1], 3.5, 0, Math.PI * 2); g.fillStyle = css(this.k.lit); g.fill();
  }
}
