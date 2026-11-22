//! Hear what this PC plays (a video, a film, anything) and turn it into a Shazam-format fingerprint; the window asks
//! Shazam which song it is. Only the fingerprint (peaks of the sound, not the sound) leaves the PC.
//! Capture = WASAPI loopback of the speakers Drift plays through (not the microphone). Fingerprints are sent at 4, 7,
//! 10 and 13 s; the window names a song once two of them agree (hear.py), so a clear song is named after ~7 s. The fingerprint algorithm and format follow SongRec
//! (github.com/marin-m/SongRec, GPL-3.0), which documents Shazam's signature format.
use crate::Ctx;
use cpal::traits::{DeviceTrait, StreamTrait};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const RATE: usize = 16_000;
const SEND_AT: [u64; 4] = [4, 7, 10, 13]; // seconds of sound per try
const KEEP: usize = 12 * RATE; // Shazam reads at most ~12 s

static ON: AtomicBool = AtomicBool::new(false);
static STOP: AtomicBool = AtomicBool::new(false);
static WAITING: Mutex<Vec<Value>> = Mutex::new(Vec::new()); // heard while no window was connected

pub fn listening() -> bool {
    ON.load(Ordering::SeqCst)
}

pub fn stop() {
    STOP.store(true, Ordering::SeqCst);
}

/// The window just connected: give it what was heard while it was starting.
pub fn flush(ctx: &Ctx) {
    let waiting: Vec<Value> = std::mem::take(&mut *WAITING.lock().unwrap());
    for v in waiting {
        ctx.emit(v);
    }
}

fn say(ctx: &Ctx, v: Value) {
    if !ctx.emit(v.clone()) {
        WAITING.lock().unwrap().push(v);
    }
}

/// Start listening (the shortcut or the window's Hear button). Drift's own song pauses meanwhile and comes back after.
pub fn start(ctx: &Ctx) {
    if ON.swap(true, Ordering::SeqCst) {
        return;
    }
    STOP.store(false, Ordering::SeqCst);
    WAITING.lock().unwrap().clear();
    let ctx = ctx.clone();
    std::thread::spawn(move || {
        let was_playing = ctx.info.lock().unwrap().playing;
        if was_playing {
            let _ = ctx.audio.send(crate::audio::Cmd::Pause);
        }
        say(&ctx, json!({"ev": "hearing", "on": true}));
        let why = capture(&ctx).err();
        ON.store(false, Ordering::SeqCst);
        say(&ctx, json!({"ev": "hearing", "on": false, "error": why}));
        if was_playing && !STOP.load(Ordering::SeqCst) {
            let _ = ctx.audio.send(crate::audio::Cmd::Play);
        }
    });
}

fn capture(ctx: &Ctx) -> Result<(), String> {
    let device = crate::audio::pick_device().ok_or("no speakers to listen to")?;
    let config = device.default_output_config().map_err(|e| e.to_string())?;
    let (rate, channels) = (config.sample_rate().0 as usize, config.channels() as usize);
    let heard: Arc<Mutex<Vec<f32>>> = Arc::new(Mutex::new(Vec::with_capacity(rate * 14)));
    let stream = {
        let cfg: cpal::StreamConfig = config.clone().into();
        macro_rules! open {
            ($t:ty) => {{
                let h = heard.clone();
                device.build_input_stream(&cfg, move |data: &[$t], _: &cpal::InputCallbackInfo| {
                    let mut h = h.lock().unwrap();
                    for frame in data.chunks(channels.max(1)) {
                        let sum: f32 = frame.iter().map(|s| <f32 as cpal::FromSample<$t>>::from_sample_(*s)).sum();
                        h.push(sum / frame.len() as f32);
                    }
                }, |_| {}, None)
            }};
        }
        match config.sample_format() {
            cpal::SampleFormat::F32 => open!(f32),
            cpal::SampleFormat::I16 => open!(i16),
            cpal::SampleFormat::I32 => open!(i32),
            cpal::SampleFormat::U16 => open!(u16),
            cpal::SampleFormat::F64 => open!(f64),
            other => return Err(format!("cannot listen to {other:?} sound")),
        }
        .map_err(|e| e.to_string())?
    };
    stream.play().map_err(|e| e.to_string())?;
    let started = Instant::now();
    for (n, secs) in SEND_AT.iter().enumerate() {
        while started.elapsed() < Duration::from_secs(*secs) {
            if STOP.load(Ordering::SeqCst) {
                return Ok(());
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        let mono = heard.lock().unwrap().clone();
        let mut s16 = to_16k(&mono, rate);
        let skipped = s16.len().saturating_sub(KEEP); // where this window starts, after the start of listening
        s16.drain(..skipped);
        let quiet = s16.iter().all(|s| s.abs() < 1e-4);
        let uri = uri(&s16);
        say(ctx, json!({"ev": "heard", "try": n + 1, "last": n + 1 == SEND_AT.len(), "uri": uri,
                        "ms": s16.len() * 1000 / RATE, "from_ms": skipped * 1000 / RATE, "quiet": quiet,
                        "at": SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)}));
    }
    drop(stream);
    Ok(())
}

/// 16 kHz mono -> the data URI Shazam reads.
pub fn uri(samples: &[f32]) -> String {
    format!("data:audio/vnd.shazam.sig;base64,{}", base64(&encode(&signature(samples), samples.len() as u32)))
}

/// Any rate -> 16 kHz: a 7 kHz windowed-sinc low-pass (no folding of the highs into the fingerprint bands), then
/// linear steps.
pub fn to_16k(input: &[f32], rate: usize) -> Vec<f32> {
    if rate == RATE {
        return input.to_vec();
    }
    const TAPS: i64 = 31;
    let cut = 7000.0 / rate as f64;
    let taps: Vec<f64> = (0..TAPS).map(|i| {
        let x = (i - TAPS / 2) as f64;
        let sinc = if x == 0.0 { 2.0 * cut } else { (2.0 * std::f64::consts::PI * cut * x).sin() / (std::f64::consts::PI * x) };
        sinc * (0.54 - 0.46 * (2.0 * std::f64::consts::PI * i as f64 / (TAPS - 1) as f64).cos())
    }).collect();
    let norm: f64 = taps.iter().sum();
    let at = |i: i64| -> f64 {
        taps.iter().enumerate().map(|(k, t)| {
            let j = i + k as i64 - TAPS / 2;
            if j < 0 || j >= input.len() as i64 { 0.0 } else { input[j as usize] as f64 * t }
        }).sum::<f64>() / norm
    };
    let step = rate as f64 / RATE as f64;
    let n = (input.len() as f64 / step) as usize;
    (0..n).map(|j| {
        let x = j as f64 * step;
        let i = x.floor() as i64;
        let f = x - i as f64;
        (at(i) * (1.0 - f) + at(i + 1) * f) as f32
    }).collect()
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Peak {
    pub pass: u32,
    pub magnitude: u16,
    pub bin: u16,
}

/// 2048-point FFT (in place, radix 2).
fn fft(re: &mut [f32; 2048], im: &mut [f32; 2048], twiddle: &[(f32, f32)]) {
    let n = 2048;
    let mut j = 0;
    for i in 1..n {
        let mut bit = n >> 1;
        while j & bit != 0 {
            j ^= bit;
            bit >>= 1;
        }
        j |= bit;
        if i < j {
            re.swap(i, j);
            im.swap(i, j);
        }
    }
    let mut len = 2;
    while len <= n {
        let stride = n / len;
        for start in (0..n).step_by(len) {
            for k in 0..len / 2 {
                let (wr, wi) = twiddle[k * stride];
                let (a, b) = (start + k, start + k + len / 2);
                let (xr, xi) = (re[b] * wr - im[b] * wi, re[b] * wi + im[b] * wr);
                re[b] = re[a] - xr;
                im[b] = im[a] - xi;
                re[a] += xr;
                im[a] += xi;
            }
        }
        len <<= 1;
    }
}

fn magnitude(v: f32) -> f32 {
    v.ln().max(1.0 / 64.0) * 1477.3 + 6144.0
}

/// The peaks Shazam matches on, per band (250-520, 520-1450, 1450-3500, 3500-5500 Hz), from 16 kHz mono in -1..1.
pub fn signature(samples: &[f32]) -> [Vec<Peak>; 4] {
    let hann: Vec<f32> = (0..2048).map(|i| (0.5 * (1.0 - (2.0 * std::f64::consts::PI * (i + 1) as f64 / 2049.0).cos())) as f32).collect();
    let twiddle: Vec<(f32, f32)> = (0..1024).map(|k| {
        let a = -2.0 * std::f64::consts::PI * k as f64 / 2048.0;
        (a.cos() as f32, a.sin() as f32)
    }).collect();
    let mut ring = [0i16; 2048];
    let mut ring_at = 0usize;
    let mut ffts = vec![[0f32; 1025]; 256];
    let mut ffts_at = 0usize;
    let mut spread = vec![[0f32; 1025]; 256];
    let mut spread_at = 0usize;
    let mut done = 0u32;
    let mut bands: [Vec<Peak>; 4] = Default::default();
    let (mut re, mut im) = ([0f32; 2048], [0f32; 2048]);
    let s16: Vec<i16> = samples.iter().map(|s| (s.clamp(-1.0, 1.0) * 32767.0).round() as i16).collect();
    for chunk in s16.chunks_exact(128) {
        // the newest 2048 samples, windowed, through the FFT
        ring[ring_at..ring_at + 128].copy_from_slice(chunk);
        ring_at = (ring_at + 128) & 2047;
        for i in 0..2048 {
            re[i] = ring[(i + ring_at) & 2047] as f32 * hann[i];
            im[i] = 0.0;
        }
        fft(&mut re, &mut im, &twiddle);
        for (k, out) in ffts[ffts_at].iter_mut().enumerate() {
            *out = ((re[k] * re[k] + im[k] * im[k]) / (1 << 17) as f32).max(0.000_000_000_1);
        }
        ffts_at = (ffts_at + 1) & 255;

        // spread each peak over its neighbours in frequency, then over the passes before it
        let mut now = ffts[(ffts_at + 255) & 255];
        for p in 0..=1022 {
            now[p] = now[p].max(now[p + 1]).max(now[p + 2]);
        }
        spread[spread_at] = now;
        for back in [1usize, 3, 6] {
            let former = &mut spread[(spread_at + 256 - back) & 255];
            for p in 0..=1024 {
                former[p] = former[p].max(now[p]);
            }
        }
        spread_at = (spread_at + 1) & 255;
        done += 1;
        if done < 46 {
            continue;
        }

        // peaks 46 passes back that stand out from everything around them
        let f46 = &ffts[(ffts_at + 256 - 46) & 255];
        let s49 = &spread[(spread_at + 256 - 49) & 255];
        for bin in 10..=1014usize {
            let v = f46[bin];
            if v < 1.0 / 64.0 || v < s49[bin - 1] {
                continue;
            }
            let mut around = 0f32;
            for off in [-10i32, -7, -4, -3, 1, 2, 5, 8] {
                around = around.max(s49[(bin as i32 + off) as usize]);
            }
            if v <= around {
                continue;
            }
            for off in [-53i32, -45, 165, 172, 179, 186, 193, 200, 214, 221, 228, 235, 242, 249] {
                around = around.max(spread[((spread_at as i32 + off) & 255) as usize][bin - 1]);
            }
            if v <= around {
                continue;
            }
            let (m, before, after) = (magnitude(v), magnitude(f46[bin - 1]), magnitude(f46[bin + 1]));
            let var1 = m * 2.0 - before - after;
            if var1 <= 0.0 {
                continue;
            }
            let var2 = (after - before) * 32.0 / var1;
            let corrected = ((bin as i32 * 64) + var2 as i32) as u16;
            let hz = corrected as f32 * (16000.0 / 2.0 / 1024.0 / 64.0);
            let band = match hz as i32 {
                250..=519 => 0,
                520..=1449 => 1,
                1450..=3499 => 2,
                3500..=5500 => 3,
                _ => continue,
            };
            bands[band].push(Peak { pass: done - 46, magnitude: m as u16, bin: corrected });
        }
    }
    bands
}

fn crc32(data: &[u8]) -> u32 {
    let mut crc = 0xFFFF_FFFFu32;
    for &b in data {
        crc ^= b as u32;
        for _ in 0..8 {
            crc = if crc & 1 != 0 { (crc >> 1) ^ 0xEDB8_8320 } else { crc >> 1 };
        }
    }
    !crc
}

/// Shazam's binary signature: a 48-byte header (magic numbers, CRC-32, 16 kHz, length), then per band its peaks as
/// (pass offset, magnitude, frequency bin).
pub fn encode(bands: &[Vec<Peak>; 4], samples: u32) -> Vec<u8> {
    let mut b: Vec<u8> = Vec::with_capacity(4096);
    fn put(b: &mut Vec<u8>, v: u32) {
        b.extend_from_slice(&v.to_le_bytes());
    }
    for v in [0xcafe2580, 0, 0, 0x94119c00, 0, 0, 0, 3 << 27, 0, 0, samples + (16000.0f32 * 0.24) as u32,
              (15 << 19) + 0x40000, 0x40000000, 0] {
        put(&mut b, v);
    }
    for (band, peaks) in bands.iter().enumerate() {
        if peaks.is_empty() {
            continue;
        }
        let mut p: Vec<u8> = Vec::with_capacity(peaks.len() * 5);
        let mut pass = 0u32;
        for peak in peaks {
            if peak.pass - pass >= 255 {
                p.push(0xff);
                p.extend_from_slice(&peak.pass.to_le_bytes());
                pass = peak.pass;
            }
            p.push((peak.pass - pass) as u8);
            p.extend_from_slice(&peak.magnitude.to_le_bytes());
            p.extend_from_slice(&peak.bin.to_le_bytes());
            pass = peak.pass;
        }
        put(&mut b, 0x60030040 + band as u32);
        put(&mut b, p.len() as u32);
        let pad = (4 - p.len() % 4) % 4;
        b.extend_from_slice(&p);
        b.extend(std::iter::repeat(0u8).take(pad));
    }
    let size = (b.len() - 48) as u32;
    b[8..12].copy_from_slice(&size.to_le_bytes());
    b[52..56].copy_from_slice(&size.to_le_bytes());
    let crc = crc32(&b[8..]);
    b[4..8].copy_from_slice(&crc.to_le_bytes());
    b
}

fn base64(data: &[u8]) -> String {
    const A: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len() * 4 / 3 + 4);
    for c in data.chunks(3) {
        let n = (c[0] as u32) << 16 | (*c.get(1).unwrap_or(&0) as u32) << 8 | *c.get(2).unwrap_or(&0) as u32;
        for i in 0..4 {
            if i <= c.len() {
                out.push(A[(n >> (18 - 6 * i) & 63) as usize] as char);
            } else {
                out.push('=');
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[ignore] // opens the loopback of the speakers for 1 s (no sound is played)
    fn loopback_opens() {
        let device = crate::audio::pick_device().expect("speakers");
        let config = device.default_output_config().unwrap();
        let got = Arc::new(Mutex::new(0usize));
        let g = got.clone();
        let stream = device.build_input_stream(&config.clone().into(), move |d: &[f32], _: &cpal::InputCallbackInfo| {
            *g.lock().unwrap() += d.len();
        }, |_| {}, None).unwrap();
        stream.play().unwrap();
        std::thread::sleep(Duration::from_secs(1));
        drop(stream);
        println!("{:?} {} Hz x{}: {} samples in 1 s", config.sample_format(), config.sample_rate().0, config.channels(),
                 got.lock().unwrap());
    }

    #[test]
    fn crc_and_base64_match_the_standard() {
        assert_eq!(crc32(b"123456789"), 0xCBF4_3926);
        assert_eq!(base64(b"Man"), "TWFu");
        assert_eq!(base64(b"Ma"), "TWE=");
        assert_eq!(base64(b"M"), "TQ==");
    }

    #[test]
    fn fft_finds_a_pure_tone() {
        let twiddle: Vec<(f32, f32)> = (0..1024).map(|k| {
            let a = -2.0 * std::f64::consts::PI * k as f64 / 2048.0;
            (a.cos() as f32, a.sin() as f32)
        }).collect();
        let mut re = [0f32; 2048];
        let mut im = [0f32; 2048];
        for (i, r) in re.iter_mut().enumerate() {
            *r = (2.0 * std::f32::consts::PI * 100.0 * i as f32 / 2048.0).sin();
        }
        fft(&mut re, &mut im, &twiddle);
        let best = (0..1025).max_by(|a, b| (re[*a].hypot(im[*a])).total_cmp(&re[*b].hypot(im[*b]))).unwrap();
        assert_eq!(best, 100);
    }

    #[test]
    fn a_tone_mix_gives_peaks_in_its_bands_and_a_valid_header() {
        // three notes changing every half second: peaks land in the 250-520 and 520-1450 Hz bands
        let notes = [330.0f32, 440.0, 660.0, 880.0];
        let samples: Vec<f32> = (0..RATE * 6).map(|i| {
            let f = notes[(i / (RATE / 2)) % notes.len()];
            0.4 * (2.0 * std::f32::consts::PI * f * i as f32 / RATE as f32).sin()
        }).collect();
        let bands = signature(&samples);
        assert!(!bands[0].is_empty() && !bands[1].is_empty());
        for band in &bands {
            assert!(band.windows(2).all(|w| w[0].pass <= w[1].pass));
        }
        let bin = encode(&bands, samples.len() as u32);
        assert_eq!(&bin[0..4], &0xcafe2580u32.to_le_bytes());
        assert_eq!(u32::from_le_bytes(bin[8..12].try_into().unwrap()) as usize, bin.len() - 48);
        assert_eq!(u32::from_le_bytes(bin[4..8].try_into().unwrap()), crc32(&bin[8..]));
    }

    #[test]
    fn resampling_keeps_length_and_tone() {
        let rate = 48_000;
        let input: Vec<f32> = (0..rate).map(|i| (2.0 * std::f32::consts::PI * 1000.0 * i as f32 / rate as f32).sin()).collect();
        let out = to_16k(&input, rate);
        assert_eq!(out.len(), RATE);
        let energy: f32 = out[1000..15000].iter().map(|s| s * s).sum::<f32>() / 14000.0;
        assert!((energy - 0.5).abs() < 0.05, "{energy}");
    }
}
