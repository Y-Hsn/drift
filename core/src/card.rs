//! The player card: a small picture of what plays, bottom right above the clock, when the Pause key or a media key
//! is pressed (media keys stay quiet while a game or a presentation is full screen; the Pause key always shows it).
//! The cover is a sun with its glow, the title in Sitka, the artist in spaced DIN capitals, and the song's shore in
//! miniature: its loudness as hills, lit up to where it plays. Previous / play-pause / next on the right; a click
//! anywhere else opens Drift. Pressing Pause while it shows plays or pauses. It fades in, rests ~3.5 s (longer while
//! the mouse is on it) and fades out; then everything it held is let go.
//!
//! Drawn by the core itself with GDI+ (part of Windows) into one small layered window: works with the Drift window
//! closed, costs ~1 MB while it shows and nothing after. Covers: %LOCALAPPDATA%/Drift/art/<fnv32 of the song id>.png
//! (the window writes them) or, failing that, the song's thumbnail fetched once with curl.
use crate::{Ctx, Item};
use std::cell::RefCell;
use std::path::PathBuf;
use std::time::{Duration, Instant};
use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, POINT, RECT, SIZE, WPARAM};
use windows_sys::Win32::Graphics::Gdi::*;
use windows_sys::Win32::Graphics::GdiPlus::*;
use windows_sys::Win32::UI::HiDpi::GetDpiForSystem;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{TrackMouseEvent, TME_LEAVE, TRACKMOUSEEVENT};
use windows_sys::Win32::UI::Shell::SHQueryUserNotificationState;
use windows_sys::Win32::UI::WindowsAndMessaging::*;

const PARGB: i32 = 0xE200B; // PixelFormat32bppPARGB
const CW: f32 = 420.0; // the card, logical px
const CH: f32 = 116.0;
const PAD: f32 = 18.0; // room around it for the shadow
const T_ANIM: usize = 1;
const T_TICK: usize = 2;
const T_SOON: usize = 3;
pub const WM_ART: u32 = WM_APP + 3;
const FADE_IN: f32 = 190.0;
const FADE_OUT: f32 = 300.0;
const REST: Duration = Duration::from_millis(3500);

#[derive(PartialEq, Clone, Copy)]
enum Phase {
    Hidden,
    In,
    Shown,
    Out,
}

struct Art {
    id: String,
    img: *mut GpImage,
    top: (f32, f32, f32),
    bottom: (f32, f32, f32),
    acc: (f32, f32, f32),
}

struct Card {
    hwnd: HWND,
    s: f32,
    w: i32,
    h: i32,
    x: i32,
    y: i32,
    mem: HDC,
    dib: HBITMAP,
    old: HGDIOBJ,
    bits: *mut u8,
    phase: Phase,
    t: f32,
    last: Instant,
    hold_until: Instant,
    hover: bool,
    art: Option<Art>,
    fetching: Option<String>,
}

thread_local! {
    static CARD: RefCell<Option<Card>> = const { RefCell::new(None) };
    static GDIP: RefCell<usize> = const { RefCell::new(0) };
}

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

fn ctx() -> &'static Ctx {
    crate::tray::ctx()
}

// ---- colour ---------------------------------------------------------------------------------------------------
fn hsl_of(r: f32, g: f32, b: f32) -> (f32, f32, f32) {
    let mx = r.max(g).max(b);
    let mn = r.min(g).min(b);
    let l = (mx + mn) / 2.0;
    if mx - mn < 1e-6 {
        return (0.0, 0.0, l);
    }
    let d = mx - mn;
    let s = if l > 0.5 { d / (2.0 - mx - mn) } else { d / (mx + mn) };
    let h = if mx == r {
        (g - b) / d + if g < b { 6.0 } else { 0.0 }
    } else if mx == g {
        (b - r) / d + 2.0
    } else {
        (r - g) / d + 4.0
    };
    (h / 6.0, s, l)
}

fn rgb_of(h: f32, s: f32, l: f32) -> (f32, f32, f32) {
    if s <= 0.0 {
        return (l, l, l);
    }
    let q = if l < 0.5 { l * (1.0 + s) } else { l + s - l * s };
    let p = 2.0 * l - q;
    let f = |mut t: f32| {
        if t < 0.0 {
            t += 1.0;
        }
        if t > 1.0 {
            t -= 1.0;
        }
        if t < 1.0 / 6.0 {
            p + (q - p) * 6.0 * t
        } else if t < 0.5 {
            q
        } else if t < 2.0 / 3.0 {
            p + (q - p) * (2.0 / 3.0 - t) * 6.0
        } else {
            p
        }
    };
    (f(h + 1.0 / 3.0), f(h), f(h - 1.0 / 3.0))
}

fn argb(a: f32, c: (f32, f32, f32)) -> u32 {
    let k = |v: f32| (v.clamp(0.0, 1.0) * 255.0).round() as u32;
    (k(a) << 24) | (k(c.0) << 16) | (k(c.1) << 8) | k(c.2)
}

const WHITE: (f32, f32, f32) = (1.0, 1.0, 1.0);
const INK: (f32, f32, f32) = (0.043, 0.043, 0.05);

// ---- the cover ------------------------------------------------------------------------------------------------
fn fnv(s: &str) -> u32 {
    s.bytes().fold(0x811c_9dc5u32, |a, b| (a ^ b as u32).wrapping_mul(0x0100_0193))
}

fn art_paths(id: &str) -> [PathBuf; 2] {
    let dir = crate::data_dir().join("art");
    let h = fnv(id);
    [dir.join(format!("{h:08x}.png")), dir.join(format!("{h:08x}.jpg"))]
}

fn load_art(item: &Item) -> Option<Art> {
    let path = art_paths(&item.id).into_iter().find(|p| p.is_file())?;
    let w = wide(&path.to_string_lossy());
    let mut img: *mut GpImage = std::ptr::null_mut();
    unsafe {
        if GdipLoadImageFromFile(w.as_ptr(), &mut img) != Ok || img.is_null() {
            return None;
        }
        let (mut iw, mut ih) = (0u32, 0u32);
        GdipGetImageWidth(img, &mut iw);
        GdipGetImageHeight(img, &mut ih);
        // colours: the average sets the card's ground, the most vivid one its light
        let (mut sr, mut sg, mut sb, mut n) = (0.0f32, 0.0f32, 0.0f32, 0.0f32);
        let mut best = (0.0f32, (0.56f32, 0.65f32, 1.0f32));
        for gy in 0..10 {
            for gx in 0..10 {
                let mut c = 0u32;
                let x = ((gx as f32 + 0.5) * iw as f32 / 10.0) as i32;
                let y = ((gy as f32 + 0.5) * ih as f32 / 10.0) as i32;
                if GdipBitmapGetPixel(img as *mut GpBitmap, x, y, &mut c) != Ok {
                    continue;
                }
                let (r, g, b) = (((c >> 16) & 255) as f32 / 255.0, ((c >> 8) & 255) as f32 / 255.0, (c & 255) as f32 / 255.0);
                sr += r;
                sg += g;
                sb += b;
                n += 1.0;
                let (_h, s, l) = hsl_of(r, g, b);
                if (0.18..=0.92).contains(&l) {
                    let score = s * (1.0 - (l - 0.55).abs());
                    if score > best.0 {
                        best = (score, (r, g, b));
                    }
                }
            }
        }
        let n = n.max(1.0);
        let (h, s, _l) = hsl_of(sr / n, sg / n, sb / n);
        let (ah, asat, al) = hsl_of(best.1 .0, best.1 .1, best.1 .2);
        let acc = if best.0 < 0.12 {
            (0.56, 0.65, 1.0)
        } else {
            rgb_of(ah, asat.clamp(0.55, 0.85), al.clamp(0.58, 0.72))
        };
        let sat = (0.12 + s * 0.7).min(0.55);
        Some(Art {
            id: item.id.clone(),
            img,
            top: rgb_of(h, sat, 0.13),
            bottom: rgb_of(h, sat * 0.8, 0.06),
            acc,
        })
    }
}

fn drop_art(art: Option<Art>) {
    if let Some(a) = art {
        unsafe { GdipDisposeImage(a.img) };
    }
}

/// The thumbnail, fetched once into the art folder (then the card draws itself again).
fn fetch_art(item: &Item, hwnd: HWND) {
    let Some(url) = item.thumb.clone().filter(|u| u.starts_with("http")) else { return };
    let target = art_paths(&item.id)[1].clone();
    let h = hwnd as usize;
    std::thread::spawn(move || {
        use std::os::windows::process::CommandExt;
        let _ = std::fs::create_dir_all(target.parent().unwrap());
        let tmp = target.with_extension("part");
        let ok = std::process::Command::new("curl.exe")
            .args(["-s", "-L", "--max-time", "8", "-o"])
            .arg(&tmp)
            .arg(&url)
            .creation_flags(0x0800_0000)
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        if ok && std::fs::rename(&tmp, &target).is_ok() {
            unsafe { PostMessageW(h as HWND, WM_ART, 0, 0) };
        } else {
            let _ = std::fs::remove_file(&tmp);
        }
    });
}

// ---- drawing --------------------------------------------------------------------------------------------------
struct G(*mut GpGraphics, f32);

impl G {
    fn brush(&self, c: u32) -> *mut GpBrush {
        let mut b: *mut GpSolidFill = std::ptr::null_mut();
        unsafe { GdipCreateSolidFill(c, &mut b) };
        b as *mut GpBrush
    }
    fn fill(&self, path: *mut GpPath, c: u32) {
        let b = self.brush(c);
        unsafe {
            GdipFillPath(self.0, b, path);
            GdipDeleteBrush(b);
        }
    }
    fn ellipse(&self, cx: f32, cy: f32, r: f32, c: u32) {
        let s = self.1;
        let b = self.brush(c);
        unsafe {
            GdipFillEllipse(self.0, b, (cx - r) * s, (cy - r) * s, 2.0 * r * s, 2.0 * r * s);
            GdipDeleteBrush(b);
        }
    }
    fn stroke(&self, path: *mut GpPath, c: u32, width: f32) {
        let mut pen: *mut GpPen = std::ptr::null_mut();
        unsafe {
            GdipCreatePen1(c, width * self.1, UnitPixel, &mut pen);
            GdipSetPenLineCap197819(pen, LineCapRound, LineCapRound, DashCapFlat);
            GdipDrawPath(self.0, pen, path);
            GdipDeletePen(pen);
        }
    }
    fn text(&self, text: &str, family: &[&str], size: f32, bold: bool, rect: (f32, f32, f32, f32), c: u32, right: bool) {
        let s = self.1;
        unsafe {
            let mut fam: *mut GpFontFamily = std::ptr::null_mut();
            for name in family {
                let w = wide(name);
                if GdipCreateFontFamilyFromName(w.as_ptr(), std::ptr::null_mut(), &mut fam) == Ok && !fam.is_null() {
                    break;
                }
                fam = std::ptr::null_mut();
            }
            if fam.is_null() {
                GdipGetGenericFontFamilySansSerif(&mut fam);
            }
            let mut font: *mut GpFont = std::ptr::null_mut();
            GdipCreateFont(fam, size * s, if bold { FontStyleBold } else { FontStyleRegular }, UnitPixel, &mut font);
            let mut fmt: *mut GpStringFormat = std::ptr::null_mut();
            GdipCreateStringFormat(StringFormatFlagsNoWrap, 0, &mut fmt);
            GdipSetStringFormatTrimming(fmt, StringTrimmingEllipsisCharacter);
            if right {
                GdipSetStringFormatAlign(fmt, StringAlignmentFar);
            }
            GdipSetStringFormatLineAlign(fmt, StringAlignmentCenter);
            let r = RectF { X: rect.0 * s, Y: rect.1 * s, Width: rect.2 * s, Height: rect.3 * s };
            let b = self.brush(c);
            let w: Vec<u16> = text.encode_utf16().collect();
            GdipDrawString(self.0, w.as_ptr(), w.len() as i32, font, &r, fmt, b);
            GdipDeleteBrush(b);
            GdipDeleteStringFormat(fmt);
            GdipDeleteFont(font);
            GdipDeleteFontFamily(fam);
        }
    }
}

fn path() -> *mut GpPath {
    let mut p: *mut GpPath = std::ptr::null_mut();
    unsafe { GdipCreatePath(FillModeWinding, &mut p) };
    p
}

fn rounded(s: f32, x: f32, y: f32, w: f32, h: f32, r: f32) -> *mut GpPath {
    let p = path();
    let (x, y, w, h, d) = (x * s, y * s, w * s, h * s, 2.0 * r * s);
    unsafe {
        GdipAddPathArc(p, x, y, d, d, 180.0, 90.0);
        GdipAddPathArc(p, x + w - d, y, d, d, 270.0, 90.0);
        GdipAddPathArc(p, x + w - d, y + h - d, d, d, 0.0, 90.0);
        GdipAddPathArc(p, x, y + h - d, d, d, 90.0, 90.0);
        GdipClosePathFigure(p);
    }
    p
}

fn spaced(text: &str) -> String {
    let mut out = String::new();
    for (i, ch) in text.chars().enumerate() {
        if i > 0 {
            out.push('\u{200A}');
        }
        out.push(ch);
    }
    out
}

fn mmss(ms: u64) -> String {
    let s = ms / 1000;
    format!("{}:{:02}", s / 60, s % 60)
}

/// The song's loudness as `n` hills (0..1), stretched to its own quiet..loud like the window's shore.
fn hills(wave: &[u8], n: usize) -> Vec<f32> {
    if wave.is_empty() {
        return (0..n).map(|i| 0.12 + 0.08 * ((i as f32) * 0.35).sin().abs()).collect();
    }
    let mut v: Vec<f32> = (0..n)
        .map(|i| {
            let a = i * wave.len() / n;
            let b = ((i + 1) * wave.len() / n).max(a + 1).min(wave.len());
            wave[a..b].iter().copied().max().unwrap_or(0) as f32
        })
        .collect();
    let lo = v.iter().cloned().fold(f32::MAX, f32::min);
    let hi = v.iter().cloned().fold(f32::MIN, f32::max);
    for x in v.iter_mut() {
        *x = 0.08 + 0.92 * (*x - lo) / (hi - lo).max(1.0);
    }
    v
}

fn render(c: &mut Card) {
    let ctx = ctx();
    let (item, upcoming) = {
        let st = ctx.st.lock().unwrap();
        (st.current.clone(), st.upcoming.first().map(|u| u.title.clone()))
    };
    let (pos, dur, playing, wave) = {
        let i = ctx.info.lock().unwrap();
        let wave = match &item {
            Some(it) if i.wave_id == it.id => i.wave.clone(),
            _ => vec![],
        };
        (i.pos, i.dur, i.playing || i.loading, wave)
    };
    // the cover (and its colours) for this song
    if let Some(it) = &item {
        if c.art.as_ref().map(|a| a.id != it.id).unwrap_or(true) {
            drop_art(c.art.take());
            c.art = load_art(it);
            if c.art.is_none() && c.fetching.as_deref() != Some(&it.id) {
                c.fetching = Some(it.id.clone());
                fetch_art(it, c.hwnd);
            }
        }
    }
    let s = c.s;
    unsafe {
        std::ptr::write_bytes(c.bits, 0, (c.w * c.h * 4) as usize);
        let mut bmp: *mut GpBitmap = std::ptr::null_mut();
        GdipCreateBitmapFromScan0(c.w, c.h, c.w * 4, PARGB, c.bits, &mut bmp);
        let mut gr: *mut GpGraphics = std::ptr::null_mut();
        GdipGetImageGraphicsContext(bmp as *mut GpImage, &mut gr);
        GdipSetSmoothingMode(gr, SmoothingModeAntiAlias);
        GdipSetTextRenderingHint(gr, TextRenderingHintAntiAlias);
        GdipSetInterpolationMode(gr, InterpolationModeHighQualityBicubic);
        GdipSetPixelOffsetMode(gr, PixelOffsetModeHighQuality);
        let g = G(gr, s);
        let (top, bottom, acc) = match &c.art {
            Some(a) => (a.top, a.bottom, a.acc),
            None => ((0.07, 0.09, 0.17), (0.03, 0.04, 0.08), (0.56, 0.65, 1.0)),
        };
        let (x0, y0) = (PAD, PAD - 4.0);
        // a soft shadow under the card, then the card: the cover's colours at dusk
        for k in 0..9 {
            let e = k as f32 * 1.6;
            let p = rounded(s, x0 - e, y0 - e + 5.0, CW + 2.0 * e, CH + 2.0 * e, 20.0 + e);
            g.fill(p, argb(0.055 - k as f32 * 0.005, (0.0, 0.0, 0.0)));
            GdipDeletePath(p);
        }
        let body = rounded(s, x0, y0, CW, CH, 20.0);
        let mut grad: *mut GpLineGradient = std::ptr::null_mut();
        let p1 = PointF { X: 0.0, Y: y0 * s };
        let p2 = PointF { X: 0.0, Y: (y0 + CH) * s };
        GdipCreateLineBrush(&p1, &p2, argb(0.97, top), argb(0.97, bottom), WrapModeTileFlipXY, &mut grad);
        GdipFillPath(gr, grad as *mut GpBrush, body);
        GdipDeleteBrush(grad as *mut GpBrush);
        // a far glow of the cover's light, top right
        let glow = path();
        GdipAddPathEllipse(glow, (x0 + CW * 0.55) * s, (y0 - CH * 0.9) * s, CW * 0.8 * s, CH * 1.6 * s);
        let mut pg: *mut GpPathGradient = std::ptr::null_mut();
        GdipCreatePathGradientFromPath(glow, &mut pg);
        GdipSetPathGradientCenterColor(pg, argb(0.20, acc));
        let mut cnt = 1i32;
        let clear = argb(0.0, acc);
        GdipSetPathGradientSurroundColorsWithCount(pg, &clear, &mut cnt);
        GdipSetClipPath(gr, body, CombineModeReplace);
        GdipFillPath(gr, pg as *mut GpBrush, glow);
        GdipResetClip(gr);
        GdipDeleteBrush(pg as *mut GpBrush);
        GdipDeletePath(glow);
        g.stroke(body, argb(0.09, WHITE), 1.0);
        GdipDeletePath(body);
        // the sun: the cover in a circle, glowing
        let (cx, cy, r) = (x0 + 16.0 + 40.0, y0 + CH / 2.0, 40.0);
        let halo = path();
        GdipAddPathEllipse(halo, (cx - r * 1.55) * s, (cy - r * 1.55) * s, r * 3.1 * s, r * 3.1 * s);
        let mut hg: *mut GpPathGradient = std::ptr::null_mut();
        GdipCreatePathGradientFromPath(halo, &mut hg);
        GdipSetPathGradientCenterColor(hg, argb(0.55, acc));
        let mut cnt = 1i32;
        GdipSetPathGradientSurroundColorsWithCount(hg, &clear, &mut cnt);
        GdipFillPath(gr, hg as *mut GpBrush, halo);
        GdipDeleteBrush(hg as *mut GpBrush);
        GdipDeletePath(halo);
        let disc = path();
        GdipAddPathEllipse(disc, (cx - r) * s, (cy - r) * s, 2.0 * r * s, 2.0 * r * s);
        match &c.art {
            Some(a) => {
                let (mut iw, mut ih) = (0u32, 0u32);
                GdipGetImageWidth(a.img, &mut iw);
                GdipGetImageHeight(a.img, &mut ih);
                let (iw, mut ih) = (iw as f32, ih as f32);
                let mut sy = 0.0;
                if iw > 0.0 && (ih / iw - 0.75).abs() < 0.02 {
                    // a YouTube 4:3 thumbnail with black bars: the 16:9 picture inside it
                    let h169 = iw * 9.0 / 16.0;
                    sy = (ih - h169) / 2.0;
                    ih = h169;
                }
                let side = iw.min(ih);
                GdipSetClipPath(gr, disc, CombineModeReplace);
                GdipDrawImageRectRect(gr, a.img, (cx - r) * s, (cy - r) * s, 2.0 * r * s, 2.0 * r * s,
                                      (iw - side) / 2.0, sy + (ih - side) / 2.0, side, side, UnitPixel,
                                      std::ptr::null(), 0, std::ptr::null_mut());
                GdipResetClip(gr);
            }
            None => {
                g.fill(disc, argb(1.0, rgb_of(hsl_of(acc.0, acc.1, acc.2).0, 0.35, 0.22)));
                g.ellipse(cx, cy, r * 0.42, argb(0.9, (0.9, 0.92, 1.0)));
            }
        }
        g.stroke(disc, argb(0.32, WHITE), 1.0);
        GdipDeletePath(disc);
        // words
        let tx = cx + r + 22.0;
        let controls = 112.0;
        let tw = x0 + CW - controls - tx;
        let (title, artist) = match &item {
            Some(it) => (it.title.clone(), it.artist.clone()),
            None => ("Nothing playing".into(), "Press play: Drift picks for you".into()),
        };
        let eyebrow = if item.is_none() { "DRIFT" } else if playing { "NOW PLAYING" } else { "PAUSED" };
        g.text(&spaced(eyebrow), &["Bahnschrift SemiBold Condensed", "Bahnschrift", "Segoe UI"], 9.5, false,
               (tx, y0 + 14.0, tw, 14.0), argb(1.0, acc), false);
        g.text(&title, &["Sitka Banner Semibold", "Sitka Banner", "Georgia"], 18.0, false, (tx, y0 + 28.0, tw, 28.0),
               argb(1.0, (0.94, 0.94, 0.96)), false);
        g.text(&spaced(&artist.to_uppercase()), &["Bahnschrift Condensed", "Bahnschrift", "Segoe UI"], 10.5, false,
               (tx, y0 + 56.0, tw, 16.0), argb(0.72, WHITE), false);
        // the shore in miniature, lit up to where the song is
        let (sx, sw, base, reach) = (tx, x0 + CW - 18.0 - tx, y0 + CH - 16.0, 15.0);
        let v = hills(&wave, 48);
        let pts: Vec<(f32, f32)> = v
            .iter()
            .enumerate()
            .map(|(i, h)| (sx + i as f32 * sw / (v.len() - 1) as f32, base - h * reach))
            .collect();
        let edge = path();
        for i in 0..pts.len() - 1 {
            let p0 = if i > 0 { pts[i - 1] } else { pts[i] };
            let (p1, p2) = (pts[i], pts[i + 1]);
            let p3 = if i + 2 < pts.len() { pts[i + 2] } else { p2 };
            GdipAddPathBezier(edge, p1.0 * s, p1.1 * s, (p1.0 + (p2.0 - p0.0) / 6.0) * s, (p1.1 + (p2.1 - p0.1) / 6.0) * s,
                              (p2.0 - (p3.0 - p1.0) / 6.0) * s, (p2.1 - (p3.1 - p1.1) / 6.0) * s, p2.0 * s, p2.1 * s);
        }
        let land = path();
        GdipAddPathPath(land, edge, 1);
        GdipAddPathLine(land, (sx + sw) * s, pts[pts.len() - 1].1 * s, (sx + sw) * s, base * s);
        GdipAddPathLine(land, (sx + sw) * s, base * s, sx * s, base * s);
        GdipClosePathFigure(land);
        g.fill(land, argb(0.14, WHITE));
        let frac = if dur > 0 { (pos as f32 / dur as f32).clamp(0.0, 1.0) } else { 0.0 };
        let px = sx + sw * frac;
        if frac > 0.0 {
            GdipSetClipRect(gr, 0.0, 0.0, px * s, c.h as f32, CombineModeReplace);
            g.fill(land, argb(0.42, acc));
            g.stroke(edge, argb(1.0, acc), 1.5);
            GdipResetClip(gr);
            let k = ((frac * (pts.len() - 1) as f32) as usize).min(pts.len() - 1);
            g.ellipse(px, pts[k].1, 5.0, argb(0.35, acc));
            g.ellipse(px, pts[k].1, 2.2, argb(1.0, WHITE));
        }
        GdipDeletePath(land);
        GdipDeletePath(edge);
        let line = path();
        GdipAddPathLine(line, sx * s, (base + 0.5) * s, (sx + sw) * s, (base + 0.5) * s);
        g.stroke(line, argb(0.16, WHITE), 1.0);
        GdipDeletePath(line);
        // time, and what comes next
        let time = if dur > 0 { format!("{} / {}", mmss(pos), mmss(dur)) } else { String::new() };
        g.text(&time, &["Bahnschrift Condensed", "Bahnschrift", "Segoe UI"], 10.0, false,
               (x0 + CW - 18.0 - 90.0, y0 + 14.0, 90.0, 14.0), argb(0.55, WHITE), true);
        let _ = upcoming;
        // controls: previous, play/pause, next
        let (by, bx) = (y0 + 46.0, x0 + CW - controls + 10.0);
        let prev = (bx + 12.0, by);
        let play = (bx + 46.0, by);
        let next = (bx + 80.0, by);
        let tri = |p: *mut GpPath, pts: [(f32, f32); 3]| {
            GdipAddPathLine(p, pts[0].0 * s, pts[0].1 * s, pts[1].0 * s, pts[1].1 * s);
            GdipAddPathLine(p, pts[1].0 * s, pts[1].1 * s, pts[2].0 * s, pts[2].1 * s);
            GdipClosePathFigure(p);
        };
        let icon = argb(0.78, WHITE);
        let p = path();
        tri(p, [(prev.0 + 5.0, prev.1 - 6.0), (prev.0 - 4.0, prev.1), (prev.0 + 5.0, prev.1 + 6.0)]);
        g.fill(p, icon);
        GdipDeletePath(p);
        let bar = rounded(s, prev.0 - 6.5, prev.1 - 6.0, 2.2, 12.0, 1.0);
        g.fill(bar, icon);
        GdipDeletePath(bar);
        let p = path();
        tri(p, [(next.0 - 5.0, next.1 - 6.0), (next.0 + 4.0, next.1), (next.0 - 5.0, next.1 + 6.0)]);
        g.fill(p, icon);
        GdipDeletePath(p);
        let bar = rounded(s, next.0 + 4.3, next.1 - 6.0, 2.2, 12.0, 1.0);
        g.fill(bar, icon);
        GdipDeletePath(bar);
        g.ellipse(play.0, play.1, 17.0, argb(1.0, (0.93, 0.93, 0.95)));
        if playing {
            for dx in [-4.5f32, 1.5] {
                let b = rounded(s, play.0 + dx, play.1 - 6.5, 3.2, 13.0, 1.0);
                g.fill(b, argb(1.0, INK));
                GdipDeletePath(b);
            }
        } else {
            let p = path();
            tri(p, [(play.0 - 4.0, play.1 - 7.0), (play.0 + 7.0, play.1), (play.0 - 4.0, play.1 + 7.0)]);
            g.fill(p, argb(1.0, INK));
            GdipDeletePath(p);
        }
        GdipDeleteGraphics(gr);
        GdipDisposeImage(bmp as *mut GpImage);
    }
}

/// Which control is under (x, y) (physical px in the window): 3 previous, 1 play/pause, 2 next.
fn hit(c: &Card, x: i32, y: i32) -> Option<usize> {
    let (x, y) = (x as f32 / c.s, y as f32 / c.s);
    let (x0, y0) = (PAD, PAD - 4.0);
    let (by, bx) = (y0 + 46.0, x0 + CW - 112.0 + 10.0);
    for (cx, id) in [(bx + 12.0, 3), (bx + 46.0, 1), (bx + 80.0, 2)] {
        if (x - cx).hypot(y - by) <= 19.0 {
            return Some(id);
        }
    }
    None
}

fn present(c: &Card) {
    let e = 1.0 - (1.0 - c.t).powi(3); // ease out
    let rise = ((1.0 - e) * 12.0 * c.s) as i32;
    unsafe {
        let screen = GetDC(std::ptr::null_mut());
        let dst = POINT { x: c.x, y: c.y + rise };
        let size = SIZE { cx: c.w, cy: c.h };
        let src = POINT { x: 0, y: 0 };
        let blend = BLENDFUNCTION { BlendOp: AC_SRC_OVER as u8, BlendFlags: 0, SourceConstantAlpha: (e * 255.0) as u8,
                                    AlphaFormat: AC_SRC_ALPHA as u8 };
        UpdateLayeredWindow(c.hwnd, screen, &dst, &size, c.mem, &src, 0, &blend, ULW_ALPHA);
        ReleaseDC(std::ptr::null_mut(), screen);
    }
}

fn start_gdiplus() -> bool {
    GDIP.with(|t| {
        if *t.borrow() != 0 {
            return true;
        }
        let input = GdiplusStartupInput { GdiplusVersion: 1, DebugEventCallback: 0, SuppressBackgroundThread: 0,
                                          SuppressExternalCodecs: 0 };
        let mut token = 0usize;
        let ok = unsafe { GdiplusStartup(&mut token, &input, std::ptr::null_mut()) } == Ok;
        if ok {
            *t.borrow_mut() = token;
        }
        ok
    })
}

fn class() -> Vec<u16> {
    wide("DriftCard")
}

fn create() -> Option<Card> {
    if !start_gdiplus() {
        return None;
    }
    unsafe {
        let inst = windows_sys::Win32::System::LibraryLoader::GetModuleHandleW(std::ptr::null());
        let cls = class();
        let wc = WNDCLASSW { lpfnWndProc: Some(proc_), hInstance: inst, lpszClassName: cls.as_ptr(),
                             hCursor: LoadCursorW(std::ptr::null_mut(), IDC_HAND), ..std::mem::zeroed() };
        RegisterClassW(&wc);
        let s = GetDpiForSystem() as f32 / 96.0;
        let (w, h) = (((CW + 2.0 * PAD) * s).ceil() as i32, ((CH + 2.0 * PAD) * s).ceil() as i32);
        let mut area = RECT { left: 0, top: 0, right: 0, bottom: 0 };
        SystemParametersInfoW(SPI_GETWORKAREA, 0, &mut area as *mut RECT as *mut _, 0);
        let (x, y) = (area.right - w - (4.0 * s) as i32, area.bottom - h - (2.0 * s) as i32);
        let hwnd = CreateWindowExW(WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, cls.as_ptr(),
                                   wide("Drift").as_ptr(), WS_POPUP, x, y, w, h, std::ptr::null_mut(),
                                   std::ptr::null_mut(), inst, std::ptr::null());
        if hwnd.is_null() {
            return None;
        }
        let screen = GetDC(std::ptr::null_mut());
        let mem = CreateCompatibleDC(screen);
        ReleaseDC(std::ptr::null_mut(), screen);
        let mut bi: BITMAPINFO = std::mem::zeroed();
        bi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        bi.bmiHeader.biWidth = w;
        bi.bmiHeader.biHeight = -h; // top-down, like GDI+ expects
        bi.bmiHeader.biPlanes = 1;
        bi.bmiHeader.biBitCount = 32;
        let mut bits: *mut core::ffi::c_void = std::ptr::null_mut();
        let dib = CreateDIBSection(mem, &bi, DIB_RGB_COLORS, &mut bits, std::ptr::null_mut(), 0);
        if dib.is_null() || bits.is_null() {
            DeleteDC(mem);
            DestroyWindow(hwnd);
            return None;
        }
        let old = SelectObject(mem, dib);
        Some(Card { hwnd, s, w, h, x, y, mem, dib, old, bits: bits as *mut u8, phase: Phase::Hidden, t: 0.0,
                    last: Instant::now(), hold_until: Instant::now(), hover: false, art: None, fetching: None })
    }
}

fn destroy(c: Card) {
    unsafe {
        KillTimer(c.hwnd, T_ANIM);
        KillTimer(c.hwnd, T_TICK);
        KillTimer(c.hwnd, T_SOON);
        DestroyWindow(c.hwnd);
        SelectObject(c.mem, c.old);
        DeleteObject(c.dib);
        DeleteDC(c.mem);
    }
    drop_art(c.art);
}

fn busy_screen() -> bool {
    let mut state = 0i32;
    unsafe { SHQueryUserNotificationState(&mut state) };
    matches!(state, 2..=4) // busy, a full-screen game, presentation mode
}

/// Show the card (or keep it up a little longer). `quiet`: only if nothing full screen is in front.
pub fn show(quiet: bool) {
    if quiet && busy_screen() {
        return;
    }
    CARD.with(|cell| {
        let mut slot = cell.borrow_mut();
        if slot.is_none() {
            *slot = create();
        }
        let Some(c) = slot.as_mut() else { return };
        render(c);
        c.hold_until = Instant::now() + REST;
        if c.phase == Phase::Hidden {
            c.t = 0.0;
            present(c);
            unsafe { ShowWindow(c.hwnd, SW_SHOWNOACTIVATE) };
        }
        if c.phase != Phase::Shown {
            c.phase = Phase::In;
        }
        c.last = Instant::now();
        unsafe {
            SetTimer(c.hwnd, T_ANIM, 15, None);
            SetTimer(c.hwnd, T_TICK, 500, None);
            SetTimer(c.hwnd, T_SOON, 220, None); // the core's state after the key press
        }
    });
}

/// Draw the card into a PNG instead of on screen (tests look at it without anything popping up).
pub fn snapshot(path: &str) -> bool {
    if !start_gdiplus() {
        return false;
    }
    unsafe {
        let s = 1.5f32;
        let (w, h) = (((CW + 2.0 * PAD) * s).ceil() as i32, ((CH + 2.0 * PAD) * s).ceil() as i32);
        let mut buf = vec![0u8; (w * h * 4) as usize];
        let mut c = Card { hwnd: std::ptr::null_mut(), s, w, h, x: 0, y: 0, mem: std::ptr::null_mut(),
                           dib: std::ptr::null_mut(), old: std::ptr::null_mut(), bits: buf.as_mut_ptr(),
                           phase: Phase::Shown, t: 1.0, last: Instant::now(), hold_until: Instant::now(),
                           hover: false, art: None, fetching: Some(String::new()) };
        render(&mut c);
        drop_art(c.art.take());
        let mut bmp: *mut GpBitmap = std::ptr::null_mut();
        GdipCreateBitmapFromScan0(w, h, w * 4, PARGB, buf.as_mut_ptr(), &mut bmp);
        let png = windows_sys::core::GUID { data1: 0x557c_f406, data2: 0x1a04, data3: 0x11d3,
                                            data4: [0x9a, 0x73, 0x00, 0x00, 0xf8, 0x1e, 0xf3, 0x2e] };
        let wpath = wide(path);
        let ok = GdipSaveImageToFile(bmp as *mut GpImage, wpath.as_ptr(), &png, std::ptr::null()) == Ok;
        GdipDisposeImage(bmp as *mut GpImage);
        ok
    }
}

/// The Pause key: shows the card; pressed again while it shows, plays or pauses.
pub fn pause_key() {
    let up = CARD.with(|cell| cell.borrow().as_ref().map(|c| matches!(c.phase, Phase::In | Phase::Shown)).unwrap_or(false));
    if up {
        crate::tray::act(1);
    }
    show(false);
}

/// The song changed: a card on screen follows.
pub fn changed() {
    CARD.with(|cell| {
        if let Some(c) = cell.borrow_mut().as_mut() {
            if c.phase != Phase::Hidden {
                render(c);
                present(c);
            }
        }
    });
}

unsafe extern "system" fn proc_(h: HWND, msg: u32, w: WPARAM, l: LPARAM) -> LRESULT {
    match msg {
        WM_MOUSEACTIVATE => MA_NOACTIVATE as LRESULT,
        WM_TIMER => {
            let mut gone = false;
            CARD.with(|cell| {
                let mut slot = cell.borrow_mut();
                let Some(c) = slot.as_mut() else { return };
                match w {
                    T_ANIM => {
                        let dt = c.last.elapsed().as_secs_f32() * 1000.0;
                        c.last = Instant::now();
                        match c.phase {
                            Phase::In => {
                                c.t = (c.t + dt / FADE_IN).min(1.0);
                                if c.t >= 1.0 {
                                    c.phase = Phase::Shown;
                                }
                                present(c);
                            }
                            Phase::Shown => {
                                if !c.hover && Instant::now() >= c.hold_until {
                                    c.phase = Phase::Out;
                                }
                            }
                            Phase::Out => {
                                c.t = (c.t - dt / FADE_OUT).max(0.0);
                                present(c);
                                if c.t <= 0.0 {
                                    gone = true;
                                }
                            }
                            Phase::Hidden => {}
                        }
                    }
                    T_TICK | T_SOON => {
                        if w == T_SOON {
                            KillTimer(h, T_SOON);
                        }
                        render(c);
                        present(c);
                    }
                    _ => {}
                }
            });
            if gone {
                // let everything go: window, picture, cover
                CARD.with(|cell| {
                    if let Some(c) = cell.borrow_mut().take() {
                        destroy(c);
                    }
                });
            }
            0
        }
        WM_ART => {
            changed();
            0
        }
        WM_MOUSEMOVE => {
            CARD.with(|cell| {
                if let Some(c) = cell.borrow_mut().as_mut() {
                    if !c.hover {
                        c.hover = true;
                        let mut t = TRACKMOUSEEVENT { cbSize: std::mem::size_of::<TRACKMOUSEEVENT>() as u32,
                                                      dwFlags: TME_LEAVE, hwndTrack: h, dwHoverTime: 0 };
                        TrackMouseEvent(&mut t);
                        if c.phase == Phase::Out {
                            c.phase = Phase::In;
                            c.last = Instant::now();
                        }
                    }
                }
            });
            0
        }
        0x02A3 => {
            // WM_MOUSELEAVE
            CARD.with(|cell| {
                if let Some(c) = cell.borrow_mut().as_mut() {
                    c.hover = false;
                    c.hold_until = Instant::now() + Duration::from_millis(1500);
                }
            });
            0
        }
        WM_LBUTTONUP => {
            let (x, y) = ((l & 0xFFFF) as i16 as i32, ((l >> 16) & 0xFFFF) as i16 as i32);
            let target = CARD.with(|cell| cell.borrow().as_ref().map(|c| hit(c, x, y)));
            match target {
                Some(Some(id)) => {
                    crate::tray::act(id);
                    show(false);
                }
                Some(None) => {
                    crate::tray::act(5); // open Drift
                    CARD.with(|cell| {
                        if let Some(c) = cell.borrow_mut().as_mut() {
                            c.hover = false;
                            c.phase = Phase::Out;
                        }
                    });
                }
                None => {}
            }
            0
        }
        _ => DefWindowProcW(h, msg, w, l),
    }
}
