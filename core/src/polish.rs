//! Sinc conversion, loudness matching, optional stereo finish and linked peak protection.
use rodio::Source;
use std::collections::VecDeque;
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Arc;
use std::time::Duration;
use symphonia::core::audio::Channels;

use crate::peak::PeakMeter;

pub const TARGET_LUFS: f32 = -13.0;
const MAX_BOOST_DB: f32 = 5.0;
const MAX_CUT_DB: f32 = -14.0;
const GUESS_DB: f32 = -3.5; // before a stream is measured (most masters are loud): close, then a glide
const SIDE_GAIN: f32 = 0.08; // +0.67 dB in the upper side signal, with unity at DC
const PEAK_CEILING: f32 = 0.86; // -1.31 dBTP target, including margin for gain motion
pub const WAVE_BARS: usize = 160;

/// Shared between the player thread and the sound: what level, which finish, and where the fader should go.
pub struct Shared {
    gain: AtomicU32,
    fade: AtomicU32,
    pub level: AtomicBool,
    pub tone: AtomicBool,
    snap: AtomicBool, // jump to the gain at once (a known song starting), instead of gliding
}

fn f(a: &AtomicU32) -> f32 {
    f32::from_bits(a.load(Ordering::Relaxed))
}

impl Shared {
    pub fn new() -> Arc<Shared> {
        Arc::new(Shared {
            gain: AtomicU32::new(10f32.powf(GUESS_DB / 20.0).to_bits()),
            fade: AtomicU32::new(1f32.to_bits()),
            level: AtomicBool::new(true),
            tone: AtomicBool::new(true),
            snap: AtomicBool::new(true),
        })
    }
    pub fn set_gain_db(&self, db: f32, snap: bool) {
        self.gain.store(10f32.powf(db / 20.0).to_bits(), Ordering::Relaxed);
        if snap {
            self.snap.store(true, Ordering::Relaxed);
        }
    }
    pub fn guess(&self) {
        self.set_gain_db(GUESS_DB, true);
    }
    pub fn fade_to(&self, v: f32) {
        self.fade.store(v.to_bits(), Ordering::Relaxed);
    }
}

/// The gain that brings a song measured at `lufs` to the target.
pub fn gain_for(lufs: f32) -> f32 {
    if !lufs.is_finite() || lufs < -60.0 {
        return 0.0;
    }
    (TARGET_LUFS - lufs).clamp(MAX_CUT_DB, MAX_BOOST_DB)
}

// --- filters ------------------------------------------------------------------------------------------------------
#[derive(Clone, Copy, Default)]
struct Biquad {
    b0: f32,
    b1: f32,
    b2: f32,
    a1: f32,
    a2: f32,
    z1: f32,
    z2: f32,
}

impl Biquad {
    fn new(b: [f64; 3], a: [f64; 3]) -> Biquad {
        let n = a[0];
        Biquad { b0: (b[0] / n) as f32, b1: (b[1] / n) as f32, b2: (b[2] / n) as f32, a1: (a[1] / n) as f32,
                 a2: (a[2] / n) as f32, z1: 0.0, z2: 0.0 }
    }
    fn highpass(rate: f64, f0: f64, q: f64) -> Biquad {
        let w = 2.0 * std::f64::consts::PI * f0 / rate;
        let alpha = w.sin() / (2.0 * q);
        let cw = w.cos();
        Biquad::new([(1.0 + cw) / 2.0, -(1.0 + cw), (1.0 + cw) / 2.0], [1.0 + alpha, -2.0 * cw, 1.0 - alpha])
    }
    #[inline]
    fn run(&mut self, x: f32) -> f32 {
        let y = self.b0 * x + self.z1;
        self.z1 = self.b1 * x - self.a1 * y + self.z2;
        self.z2 = self.b2 * x - self.a2 * y;
        y
    }
}

/// ITU BS.1770 K-weighting (the loudness meter's ear) for one channel.
fn k_weight(rate: f64) -> [Biquad; 2] {
    // Bilinear transforms of the BS.1770 reference stages. The RLB stage
    // retains the reference numerator [1, -2, 1] after denominator normalization.
    let k = (std::f64::consts::PI * 1681.974450955533 / rate).tan();
    let q = 0.7071752369554196;
    let vh = 10f64.powf(3.999843853973347 / 20.0);
    let vb = vh.powf(0.4996667741545416);
    let shelf = Biquad::new(
        [vh + vb * k / q + k*k, 2.0 * (k*k - vh), vh - vb * k / q + k*k],
        [1.0 + k/q + k*k, 2.0 * (k*k - 1.0), 1.0 - k/q + k*k],
    );
    let k = (std::f64::consts::PI * 38.13547087602444 / rate).tan();
    let q = 0.5003270373238773;
    let a0 = 1.0 + k/q + k*k;
    let rlb = Biquad::new([1.0, -2.0, 1.0],
        [1.0, 2.0 * (k*k - 1.0) / a0, (1.0 - k/q + k*k) / a0]);
    [shelf, rlb]
}

// --- resampling -------------------------------------------------------------------------------------------------
/// High-quality sample-rate conversion (polyphase windowed sinc, Kaiser window, ~90 dB stopband), so a 44.1 kHz
/// YouTube song reaches a 48 kHz device clean. rodio's own converter is linear and restarts on every decoded packet:
/// that was the buzz (a tiny gap ~47 times a second) and the broken highs.
pub struct Resampler {
    l: usize,
    m: usize,
    taps: usize,
    coef: Vec<f32>,
    hist: Vec<f32>, // two copies of each channel's ring, for a contiguous inner product
    head: usize,
    chans: usize,
    phase: usize,
    at: usize,
    received: usize,
    primed: bool,
    ended: bool,
}

fn gcd(a: usize, b: usize) -> usize {
    if b == 0 { a } else { gcd(b, a % b) }
}

fn bessel_i0(x: f64) -> f64 {
    let (mut sum, mut term, mut k) = (1.0, 1.0, 1.0);
    while term > 1e-12 * sum {
        term *= (x / (2.0 * k)) * (x / (2.0 * k));
        sum += term;
        k += 1.0;
    }
    sum
}

impl Resampler {
    /// None when no conversion is needed (or the ratio is too odd to build a table for).
    pub fn new(from: u32, to: u32, chans: usize) -> Option<Resampler> {
        if from == to || from == 0 || to == 0 {
            return None;
        }
        let g = gcd(from as usize, to as usize);
        let (l, m) = (to as usize / g, from as usize / g);
        if l > 2048 {
            return None;
        }
        let ratio = from as f64 / from.min(to) as f64;
        let taps = ((88.0 * ratio).ceil() as usize).min(384) | 1;
        let half = taps / 2;
        let bandwidth = 0.94 / ratio;
        let beta = 8.6;
        let i0b = bessel_i0(beta);
        let mut coef = vec![0f32; l * taps];
        let mut proto = vec![0.0; taps];
        for (p, row) in coef.chunks_exact_mut(taps).enumerate() {
            let frac = p as f64 / l as f64;
            for (k, h) in proto.iter_mut().enumerate() {
                let t = half as f64 - k as f64 + frac;
                let x = std::f64::consts::PI * bandwidth * t;
                let sinc = if x.abs() < 1e-12 { 1.0 } else { x.sin() / x };
                let r = t / (half + 1) as f64;
                let w = bessel_i0(beta * (1.0 - r * r).max(0.0).sqrt()) / i0b;
                *h = sinc * w;
            }
            let sum: f64 = proto.iter().sum();
            for (h, &x) in row.iter_mut().zip(&proto) {
                *h = (x / sum) as f32;
            }
        }
        Some(Resampler { l, m, taps, coef, hist: vec![0.0; 2 * taps * chans], head: taps - 1, chans, phase: 0,
                         at: 0, received: 0, primed: false, ended: false })
    }

    fn push(&mut self, frame: &[f32]) {
        self.head = (self.head + 1) % self.taps;
        for (ch, &x) in frame.iter().enumerate() {
            let at = ch * 2 * self.taps + self.head;
            self.hist[at] = x;
            self.hist[at + self.taps] = x;
        }
    }

    pub fn reset(&mut self) {
        self.hist.fill(0.0);
        self.head = self.taps - 1;
        self.phase = 0;
        self.at = 0;
        self.received = 0;
        self.primed = false;
        self.ended = false;
    }

    /// One output frame into `out`; `pull` gives the next input frame (false at the end).
    fn next_frame(&mut self, out: &mut [f32], mut pull: impl FnMut(&mut [f32]) -> bool) -> bool {
        let mut buf = [0f32; 8];
        let advance = self.phase / self.l;
        self.phase %= self.l;
        self.at += advance;
        let lookahead = if self.primed { 0 } else { self.taps / 2 + 1 };
        self.primed = true;
        for _ in 0..advance + lookahead {
            if !self.ended && pull(&mut buf[..self.chans]) {
                self.received += 1;
            } else {
                self.ended = true;
                buf.fill(0.0);
            }
            self.push(&buf[..self.chans]);
        }
        if self.ended && self.at >= self.received {
            return false;
        }
        let row = &self.coef[self.phase * self.taps..(self.phase + 1) * self.taps];
        for (ch, v) in out.iter_mut().enumerate() {
            let start = ch * 2 * self.taps + self.head + 1;
            let history = &self.hist[start..start + self.taps];
            *v = row.iter().zip(history).map(|(&h, &x)| h * x).sum();
        }
        self.phase += self.m;
        true
    }
}

// --- the source wrapper -------------------------------------------------------------------------------------------
struct Downmix {
    rows: [[f32; 8]; 2],
    channels: usize,
}

impl Downmix {
    fn new(layout: Channels, output: u16) -> Option<Self> {
        if output == 0 || output > 2 || layout.count() <= output as usize {
            return None;
        }
        let half_power = std::f32::consts::FRAC_1_SQRT_2;
        let mut rows = [[0.0; 8]; 2];
        for (i, ch) in layout.iter().take(8).enumerate() {
            use Channels as C;
            let (l, r) = match ch {
                C::FRONT_LEFT => (1.0, 0.0),
                C::FRONT_RIGHT => (0.0, 1.0),
                C::FRONT_CENTRE => (half_power, half_power),
                C::LFE1 | C::LFE2 => (0.0, 0.0),
                C::REAR_LEFT | C::SIDE_LEFT | C::FRONT_LEFT_CENTRE | C::TOP_FRONT_LEFT | C::TOP_REAR_LEFT
                | C::REAR_LEFT_CENTRE | C::FRONT_LEFT_WIDE | C::FRONT_LEFT_HIGH => (half_power, 0.0),
                C::REAR_RIGHT | C::SIDE_RIGHT | C::FRONT_RIGHT_CENTRE | C::TOP_FRONT_RIGHT | C::TOP_REAR_RIGHT
                | C::REAR_RIGHT_CENTRE | C::FRONT_RIGHT_WIDE | C::FRONT_RIGHT_HIGH => (0.0, half_power),
                _ => (0.5, 0.5),
            };
            rows[0][i] = if output == 1 { (l + r) * 0.5 } else { l };
            rows[1][i] = r;
        }
        Some(Self { rows, channels: output as usize })
    }

    fn fold(&self, src: [f32; 8]) -> [f32; 8] {
        let mut dst = [0.0; 8];
        for (y, row) in dst.iter_mut().zip(&self.rows[..self.channels]) {
            *y = row.iter().zip(src).map(|(a, b)| a * b).sum();
        }
        dst
    }
}

/// Look ahead by 2.5 ms. A single gain follows predicted peaks and keeps stereo balance and transients intact.
struct PeakGuard {
    channels: usize,
    delay: usize,
    frames: VecDeque<[f32; 8]>,
    peaks: VecDeque<(usize, f32)>,
    meter: [PeakMeter; 8],
    step: usize,
    gain: f32,
    attack: f32,
    release: f32,
}

impl PeakGuard {
    fn new(rate: u32, channels: usize) -> Self {
        let delay = (rate as usize / 400).max(16);
        Self { channels, delay, frames: VecDeque::with_capacity(delay + 1),
               peaks: VecDeque::with_capacity(delay + 13), meter: Default::default(),
               step: 0, gain: 1.0, attack: smoothing(rate, 0.00025), release: smoothing(rate, 0.07) }
    }

    fn reset(&mut self) {
        self.frames.clear();
        self.peaks.clear();
        self.meter = Default::default();
        self.step = 0;
        self.gain = 1.0;
    }

    fn observe(&mut self, input: [f32; 8], ceiling: f32) {
        self.step += 1;
        let mut peak = 0.0f32;
        for (meter, sample) in self.meter[..self.channels].iter_mut().zip(input) {
            peak = peak.max(meter.push(sample));
        }
        let oldest = self.step.saturating_sub(self.delay + 12);
        while self.peaks.front().is_some_and(|&(at, _)| at <= oldest) {
            self.peaks.pop_front();
        }
        while self.peaks.back().is_some_and(|&(_, v)| v <= peak) {
            self.peaks.pop_back();
        }
        self.peaks.push_back((self.step, peak));
        let largest = self.peaks[0].1;
        let target = if largest > ceiling { ceiling / largest } else { 1.0 };
        let coefficient = if target < self.gain { self.attack } else { self.release };
        self.gain += (target - self.gain) * coefficient;
    }

    fn pop(&mut self, ceiling: f32) -> Option<[f32; 8]> {
        let mut frame = self.frames.pop_front()?;
        let peak = frame[..self.channels].iter().fold(0.0f32, |a, x| a.max(x.abs()));
        let gain = if peak > ceiling { self.gain.min(ceiling / peak) } else { self.gain };
        for x in &mut frame[..self.channels] {
            *x *= gain;
        }
        Some(frame)
    }

    fn push(&mut self, frame: [f32; 8], ceiling: f32) -> Option<[f32; 8]> {
        self.observe(frame, ceiling);
        self.frames.push_back(frame);
        if self.frames.len() > self.delay { self.pop(ceiling) } else { None }
    }

    fn drain(&mut self, ceiling: f32) -> Option<[f32; 8]> {
        if self.frames.is_empty() {
            return None;
        }
        self.observe([0.0; 8], ceiling);
        while self.step <= self.delay {
            self.observe([0.0; 8], ceiling);
        }
        self.pop(ceiling)
    }
}

fn smoothing(rate: u32, seconds: f32) -> f32 {
    1.0 - (-1.0 / (rate as f32 * seconds)).exp()
}

pub struct Polish<S: Source<Item = f32>> {
    inner: S,
    sh: Arc<Shared>,
    rate: u32, // what we hand on (the device's rate when we convert)
    chans: u16,
    input_chans: usize,
    downmix: Option<Downmix>,
    rs: Option<Resampler>,
    side: Biquad,
    space: f32,
    guard: PeakGuard,
    ended: bool,
    frame: [f32; 8],
    pos: usize,
    len: usize,
    gain: f32, // linear, smoothed
    fade: f32, // 0..1, smoothed
    k_gain: f32,
    k_fade: f32,
    k_space: f32,
}

impl<S: Source<Item = f32>> Polish<S> {
    pub fn new(inner: S, sh: Arc<Shared>, device_rate: u32, output_chans: u16, layout: Channels) -> Polish<S> {
        let from = inner.sample_rate().max(8000);
        let input_chans = inner.channels().max(1) as usize;
        let downmix = Downmix::new(layout, output_chans);
        let chans = downmix.as_ref().map_or(input_chans.min(8) as u16, |d| d.channels as u16);
        let rs = if device_rate >= 8000 { Resampler::new(from, device_rate, chans as usize) } else { None };
        let rate = if rs.is_some() { device_rate } else { from };
        let space = if sh.tone.load(Ordering::Relaxed) { SIDE_GAIN } else { 0.0 };
        let mut p = Polish { rate, chans, input_chans, downmix, rs, side: Biquad::highpass(rate as f64, 190.0, 0.7071),
                             space, guard: PeakGuard::new(rate, chans as usize), ended: false,
                             frame: [0.0; 8], pos: 0, len: 0, gain: 1.0, fade: 0.0,
                             k_gain: smoothing(rate, 0.35), // ~1 s to settle on a new level
                             k_fade: smoothing(rate, 0.012), // ~40 ms fades
                             k_space: smoothing(rate, 0.018),
                             inner, sh };
        p.gain = p.target_gain();
        p.sh.snap.store(false, Ordering::Relaxed);
        p
    }

    fn target_gain(&self) -> f32 {
        if self.sh.level.load(Ordering::Relaxed) {
            f(&self.sh.gain)
        } else {
            1.0
        }
    }

    fn input_frame(&mut self) -> Option<[f32; 8]> {
        let n = self.chans as usize;
        let (inner, downmix, input_chans) = (&mut self.inner, &self.downmix, self.input_chans);
        let mut read = |dst: &mut [f32]| -> bool {
            let Some(first) = inner.next() else { return false; };
            let clean = |x: f32| if x.is_finite() { x } else { 0.0 };
            let mut src = [0.0; 8];
            src[0] = clean(first);
            for s in &mut src[1..input_chans.min(8)] {
                *s = clean(inner.next().unwrap_or(0.0));
            }
            for _ in 8..input_chans {
                inner.next();
            }
            let src = downmix.as_ref().map_or(src, |mix| mix.fold(src));
            dst.copy_from_slice(&src[..dst.len()]);
            true
        };
        let mut frame = [0.0; 8];
        let ok = match self.rs.as_mut() {
            Some(rs) => rs.next_frame(&mut frame[..n], read),
            None => read(&mut frame[..n]),
        };
        if !ok {
            return None;
        }
        let target = self.target_gain();
        if self.sh.snap.swap(false, Ordering::Relaxed) {
            self.gain = target;
        }
        self.gain += (target - self.gain) * self.k_gain;
        self.fade += (f(&self.sh.fade) - self.fade) * self.k_fade;
        if n == 2 {
            let target = if self.sh.tone.load(Ordering::Relaxed) { SIDE_GAIN } else { 0.0 };
            self.space += (target - self.space) * self.k_space;
            let extra = self.space * self.side.run((frame[0] - frame[1]) * 0.5);
            frame[0] += extra;
            frame[1] -= extra;
        }
        let g = self.gain * self.fade;
        for v in &mut frame[..n] {
            *v *= g;
        }
        Some(frame)
    }

    fn pull_frame(&mut self) -> Option<[f32; 8]> {
        let enabled = self.sh.level.load(Ordering::Relaxed) || self.sh.tone.load(Ordering::Relaxed);
        let ceiling = if enabled { PEAK_CEILING } else { 1.0 };
        while !self.ended {
            let Some(frame) = self.input_frame() else {
                self.ended = true;
                break;
            };
            if let Some(out) = self.guard.push(frame, ceiling) {
                return Some(out);
            }
        }
        self.guard.drain(ceiling)
    }
}

impl<S: Source<Item = f32>> Iterator for Polish<S> {
    type Item = f32;
    #[inline]
    fn next(&mut self) -> Option<f32> {
        if self.pos >= self.len {
            self.frame = self.pull_frame()?;
            self.pos = 0;
            self.len = self.chans as usize;
        }
        let v = self.frame[self.pos];
        self.pos += 1;
        Some(v)
    }
}

impl<S: Source<Item = f32>> Source for Polish<S> {
    /// One endless span: the rate and channels never change mid-song, so rodio builds its (now pass-through)
    /// converter once instead of once per packet.
    fn current_frame_len(&self) -> Option<usize> {
        None
    }
    fn channels(&self) -> u16 {
        self.chans
    }
    fn sample_rate(&self) -> u32 {
        self.rate
    }
    fn total_duration(&self) -> Option<Duration> {
        self.inner.total_duration()
    }
    fn try_seek(&mut self, pos: Duration) -> Result<(), rodio::source::SeekError> {
        self.inner.try_seek(pos)?;
        self.pos = 0;
        self.len = 0;
        if let Some(rs) = self.rs.as_mut() {
            rs.reset();
        }
        self.guard.reset();
        self.ended = false;
        self.side.z1 = 0.0;
        self.side.z2 = 0.0;
        self.fade = 0.0; // a seek glides in instead of clicking
        Ok(())
    }
}

// --- analysis -----------------------------------------------------------------------------------------------------
#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct Analysis {
    pub lufs: f32,
    pub wave: Vec<u8>, // WAVE_BARS loudness bars, 0..255 (the seek bar)
    #[serde(default)]
    pub peak: f32,
}

impl Analysis {
    pub fn gain_db(&self) -> f32 {
        let desired = gain_for(self.lufs);
        if self.peak > 0.0 {
            // Leave room for transient peaks and the tone adjustment before the limiter.
            desired.min(-2.0 - 20.0 * self.peak.log10())
        } else {
            desired
        }
    }
}

/// Integrated loudness (BS.1770-4 gating) and the song's shape, decoding `samples` once.
#[cfg(test)]
fn measure<I: Iterator<Item = f32>>(samples: I, rate: u32, chans: u16) -> Option<Analysis> {
    measure_weighted(samples, rate, &vec![1.0; usize::from(chans.max(1))])
}

/// Native speaker positions in Symphonia's interleaved order. LFE participates
/// in true-peak protection but is excluded from programme loudness.
pub fn measure_layout<I: Iterator<Item = f32>>(samples: I, rate: u32,
    layout: symphonia::core::audio::Channels) -> Option<Analysis> {
    use symphonia::core::audio::Channels as C;
    let surround = C::SIDE_LEFT | C::SIDE_RIGHT | C::REAR_LEFT | C::REAR_RIGHT;
    let weights: Vec<_> = layout.iter().map(|c| {
        if c.intersects(C::LFE1 | C::LFE2) { 0.0 }
        else if c.intersects(surround) { 1.41 }
        else { 1.0 }
    }).collect();
    if weights.is_empty() { return None; }
    measure_weighted(samples, rate, &weights)
}

fn measure_weighted<I: Iterator<Item = f32>>(samples: I, rate: u32, weights: &[f64]) -> Option<Analysis> {
    let n = weights.len();
    let rate = rate.max(8000);
    let mut kw: Vec<[Biquad; 2]> = (0..n).map(|_| k_weight(rate as f64)).collect();
    let step = (rate / 10) as usize; // 100 ms sub-blocks
    let mut subs: Vec<f64> = Vec::with_capacity(4096); // K-weighted mean square per sub-block (summed over channels)
    let mut acc = 0f64;
    let mut frames = 0usize;
    let mut c = 0usize;
    let mut peaks: Vec<_> = (0..n).map(|_| crate::peak::PeakMeter::default()).collect();
    for x in samples {
        let x = if x.is_finite() { x } else { 0.0 };
        peaks[c].push(x);
        let mut y = x;
        for b in kw[c].iter_mut() {
            y = b.run(y);
        }
        acc += weights[c] * (y as f64) * (y as f64);
        c += 1;
        if c == n {
            c = 0;
            frames += 1;
            if frames == step {
                subs.push(acc / step as f64);
                acc = 0.0;
                frames = 0;
            }
        }
    }
    if subs.len() < 4 {
        return None;
    }
    let peak = peaks.into_iter().map(|p| p.finish()).fold(0.0f32, f32::max);
    // 400 ms blocks, 75 % overlap
    let blocks: Vec<f64> = subs.windows(4).map(|w| w.iter().sum::<f64>() / 4.0).collect();
    let lk = |ms: f64| -0.691 + 10.0 * ms.max(1e-12).log10();
    let abs: Vec<f64> = blocks.iter().copied().filter(|&z| lk(z) > -70.0).collect();
    if abs.is_empty() {
        return Some(Analysis { lufs: -70.0, wave: vec![0; WAVE_BARS], peak });
    }
    let rel = lk(abs.iter().sum::<f64>() / abs.len() as f64) - 10.0;
    let gated: Vec<f64> = abs.into_iter().filter(|&z| lk(z) > rel).collect();
    let lufs = if gated.is_empty() { -70.0 } else { lk(gated.iter().sum::<f64>() / gated.len() as f64) } as f32;
    // the shape: loudness per bar on a dB scale (quiet intros read as quiet, choruses stand up)
    let per = subs.len() as f64 / WAVE_BARS as f64;
    let mut bars = vec![0f32; WAVE_BARS];
    for (i, b) in bars.iter_mut().enumerate() {
        let (a, z) = ((i as f64 * per) as usize, (((i + 1) as f64 * per) as usize).max((i as f64 * per) as usize + 1));
        let slice = &subs[a.min(subs.len() - 1)..z.min(subs.len())];
        let ms = if slice.is_empty() { 0.0 } else { slice.iter().sum::<f64>() / slice.len() as f64 };
        *b = lk(ms) as f32;
    }
    let top = bars.iter().cloned().fold(-70f32, f32::max);
    let wave = bars.iter().map(|&db| (((db - (top - 30.0)) / 30.0).clamp(0.0, 1.0).powf(1.4) * 255.0) as u8).collect();
    Some(Analysis { lufs, wave, peak })
}

#[cfg(test)]
mod layout_tests {
    use super::*;
    use symphonia::core::audio::{Channels, Layout};

    #[test]
    fn surround_loudness_uses_speaker_roles_but_measures_every_peak() {
        let layout = Layout::FivePointOne.into_channels();
        let one_channel = |speaker| {
            let active = layout.iter().position(|c| c == speaker).unwrap();
            let signal = (0..24_000).flat_map(move |t| {
                let mut frame = [0.0; 6];
                frame[active] = 0.5 * (t as f32 * std::f32::consts::PI / 24.0).sin();
                frame
            });
            measure_layout(signal, 48_000, layout).unwrap()
        };
        let front = one_channel(Channels::FRONT_CENTRE);
        let rear = one_channel(Channels::REAR_LEFT);
        let sub = one_channel(Channels::LFE1);
        assert!((rear.lufs - front.lufs - 1.49).abs() < 0.02);
        assert_eq!(sub.lufs, -70.0);
        assert!(sub.peak >= 0.499);
    }
}

fn store_path(key: &str) -> std::path::PathBuf {
    let h = key.bytes().fold(0xcbf2_9ce4_8422_2325u64, |a, b| (a ^ b as u64).wrapping_mul(0x100_0000_01b3));
    let d = crate::data_dir().join("sound");
    let _ = std::fs::create_dir_all(&d);
    d.join(format!("{h:016x}.json"))
}

pub fn known(key: &str) -> Option<Analysis> {
    serde_json::from_slice(&std::fs::read(store_path(key)).ok()?).ok()
}

pub fn remember(key: &str, a: &Analysis) {
    if let Ok(b) = serde_json::to_vec(a) {
        let _ = std::fs::write(store_path(key), b);
    }
    // keep the folder small: the newest 3000 songs
    if let Ok(rd) = std::fs::read_dir(crate::data_dir().join("sound")) {
        let mut files: Vec<_> = rd.flatten().filter_map(|e| Some((e.metadata().ok()?.modified().ok()?, e.path()))).collect();
        if files.len() > 3000 {
            files.sort();
            let n = files.len() - 3000;
            for (_, p) in files.into_iter().take(n) {
                let _ = std::fs::remove_file(p);
            }
        }
    }
}

/// The key a song's analysis is kept under: the song id, plus the file's size for local files (a re-tagged or
/// replaced file is measured again).
pub fn key_for(id: &str, path: &Path) -> String {
    let size = std::fs::metadata(path).map(|m| m.len()).unwrap_or(0);
    // Keep older files available; the new meter gets its own cache entry.
    format!("bs1770-tp4-v1|{id}|{size}")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sine(rate: u32, secs: f32, amp: f32) -> Vec<f32> {
        let n = (rate as f32 * secs) as usize;
        (0..n).flat_map(|i| {
            let v = amp * (2.0 * std::f32::consts::PI * 1000.0 * i as f32 / rate as f32).sin();
            [v, v]
        }).collect()
    }

    #[test]
    fn loudness_of_a_sine_matches_bs1770() {
        // BS.1770: a full-scale 1 kHz sine reads -3.01 LUFS in one channel, so about 0 LUFS in two.
        let a = measure(sine(48000, 3.0, 1.0).into_iter(), 48000, 2).unwrap();
        assert!((a.lufs - 0.0).abs() < 0.6, "got {}", a.lufs);
        let q = measure(sine(48000, 3.0, 0.1).into_iter(), 48000, 2).unwrap();
        assert!((q.lufs + 20.0).abs() < 0.6, "got {}", q.lufs);
        assert_eq!(a.wave.len(), WAVE_BARS);
    }

    #[test]
    fn resampler_is_clean_and_keeps_level() {
        // a 1 kHz tone at 44.1 kHz -> 48 kHz: same level, and almost nothing else in it (no buzz, no images)
        let mut rs = Resampler::new(44100, 48000, 1).unwrap();
        let src: Vec<f32> = (0..44100).map(|i| 0.5 * (2.0 * std::f32::consts::PI * 1000.0 * i as f32 / 44100.0).sin()).collect();
        let mut it = src.into_iter();
        let mut out = vec![];
        let mut f = [0f32; 1];
        while rs.next_frame(&mut f, |d| match it.next() { Some(v) => { d[0] = v; true } None => false }) {
            out.push(f[0]);
        }
        assert!((out.len() as i64 - 48000).abs() < 10, "{} frames", out.len());
        let body = &out[2000..46000];
        // fit the ideal 1 kHz sine at 48 kHz (best phase) and measure what is left over
        let (mut sc, mut cc) = (0f64, 0f64);
        for (i, &y) in body.iter().enumerate() {
            let w = 2.0 * std::f64::consts::PI * 1000.0 * (i + 2000) as f64 / 48000.0;
            sc += y as f64 * w.sin();
            cc += y as f64 * w.cos();
        }
        let (a, b) = (2.0 * sc / body.len() as f64, 2.0 * cc / body.len() as f64);
        let amp = (a * a + b * b).sqrt();
        assert!((amp - 0.5).abs() < 0.005, "level {amp}");
        let mut resid = 0f64;
        for (i, &y) in body.iter().enumerate() {
            let w = 2.0 * std::f64::consts::PI * 1000.0 * (i + 2000) as f64 / 48000.0;
            let e = y as f64 - (a * w.sin() + b * w.cos());
            resid += e * e;
        }
        let snr = 10.0 * ((amp * amp / 2.0) / (resid / body.len() as f64)).log10();
        assert!(snr > 70.0, "only {snr:.1} dB clean"); // the f32 test tone itself limits this (~75 dB)
    }

    #[test]
    fn gain_is_capped() {
        assert_eq!(gain_for(-40.0), MAX_BOOST_DB);
        assert_eq!(gain_for(-5.0), -8.0);
        assert_eq!(gain_for(f32::NAN), 0.0);
    }

    #[test]
    fn normalization_leaves_headroom_for_a_loud_transient() {
        let a = Analysis { lufs: -24.0, wave: vec![], peak: 1.0 };
        assert_eq!(a.gain_db(), -2.0);
        let legacy: Analysis = serde_json::from_str(r#"{"lufs":-24.0,"wave":[]}"#).unwrap();
        assert_eq!(legacy.gain_db(), gain_for(-24.0));
    }

    #[test]
    fn peak_guard_catches_intersample_peaks_and_preserves_stereo_ratio() {
        let mut limiter = PeakGuard::new(48_000, 2);
        let mut left = PeakMeter::default();
        let mut frames = Vec::new();
        for i in 0..4_800 {
            let x = if (100..4_400).contains(&i) {
                ((i as f64 + 0.5) * std::f64::consts::FRAC_PI_2).sin() as f32
            } else { 0.0 };
            let input = [x, x * 0.36, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0];
            frames.extend(limiter.push(input, PEAK_CEILING));
        }
        while let Some(frame) = limiter.drain(PEAK_CEILING) {
            frames.push(frame);
        }
        assert_eq!(frames.len(), 4_800);
        for frame in frames {
            assert!((frame[1] - frame[0] * 0.36).abs() < 1e-6);
            left.push(frame[0]);
        }
        let peak = left.finish();
        println!("guarded intersample peak {:+.2} dBTP", 20.0 * peak.log10());
        assert!(peak < 10f32.powf(-1.0 / 20.0), "peak {peak}");
    }

    #[test]
    fn peak_guard_is_transparent_below_threshold_and_drains_short_files() {
        let mut limiter = PeakGuard::new(48_000, 2);
        let frames = [[0.1; 8], [0.2; 8], [-0.25; 8]];
        for frame in frames {
            assert!(limiter.push(frame, PEAK_CEILING).is_none());
        }
        for frame in frames {
            assert_eq!(limiter.drain(PEAK_CEILING), Some(frame));
        }
        assert!(limiter.drain(PEAK_CEILING).is_none());
        limiter.push([0.6; 8], PEAK_CEILING);
        limiter.reset();
        assert!(limiter.drain(PEAK_CEILING).is_none());
    }

    #[test]
    fn studio_finish_preserves_mono_and_only_opens_upper_stereo() {
        use rodio::buffer::SamplesBuffer;
        let layout = Channels::FRONT_LEFT | Channels::FRONT_RIGHT;
        let render = |frequency: f64, right: f32, tone: bool| -> Vec<f32> {
            let input = (0..24_000).flat_map(|i| {
                let x = (0.2 * (i as f64 * std::f64::consts::TAU * frequency / 48_000.0).sin()) as f32;
                [x, x * right]
            }).collect::<Vec<_>>();
            let sh = Shared::new();
            sh.level.store(false, Ordering::Relaxed);
            sh.tone.store(tone, Ordering::Relaxed);
            Polish::new(SamplesBuffer::new(2, 48_000, input), sh, 48_000, 2, layout).collect()
        };
        assert_eq!(render(2000.0, 1.0, true), render(2000.0, 1.0, false));
        let rms = |input: Vec<f32>| (input[12_000..].iter().map(|x| x * x).sum::<f32>() / 36_000.0).sqrt();
        let bass = 20.0 * (rms(render(42.0, -1.0, true)) / rms(render(42.0, -1.0, false))).log10();
        let upper = 20.0 * (rms(render(2000.0, -1.0, true)) / rms(render(2000.0, -1.0, false))).log10();
        assert!(bass.abs() < 0.07, "bass {bass:.3} dB");
        assert!((0.55..0.75).contains(&upper), "side {upper:.3} dB");
    }

    #[test]
    fn downmix_preserves_center_surrounds_and_the_right_channel() {
        use Channels as C;
        let stereo = C::FRONT_LEFT | C::FRONT_RIGHT;
        let mix = Downmix::new(stereo, 1).unwrap();
        assert_eq!(mix.fold([0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])[0], 0.5);
        let mix = Downmix::new(stereo | C::FRONT_CENTRE | C::LFE1 | C::SIDE_LEFT | C::SIDE_RIGHT, 2).unwrap();
        let at = |i| {
            let mut frame = [0.0; 8];
            frame[i] = 1.0;
            let folded = mix.fold(frame);
            [folded[0], folded[1]]
        };
        let half_power = std::f32::consts::FRAC_1_SQRT_2;
        assert_eq!(at(2), [half_power, half_power]);
        assert_eq!(at(3), [0.0, 0.0]);
        assert_eq!(at(4), [half_power, 0.0]);
        assert_eq!(at(5), [0.0, half_power]);
    }

    #[test]
    fn resampler_preserves_upper_passband_and_rejects_downsampling_aliases() {
        let render = |from: u32, hz: u32| {
            let mut resampler = Resampler::new(from, 48_000, 1).unwrap();
            let mut source = (0..from).map(|i| (std::f64::consts::TAU * hz as f64 * i as f64 / from as f64).sin() as f32);
            let mut dst = [0.0];
            let mut out = Vec::new();
            while resampler.next_frame(&mut dst, |f| {
                if let Some(v) = source.next() { f[0] = v; true } else { false }
            }) { out.push(dst[0]); }
            assert_eq!(out.len(), 48_000);
            let mean_square = out[2400..45_600].iter().map(|x| x * x).sum::<f32>() / 43_200.0;
            10.0 * (2.0 * mean_square).log10()
        };
        let pass = render(44_100, 19_000);
        let stop = render(96_000, 27_000);
        println!("19 kHz passband {pass:+.2} dB, 27 kHz folded energy {stop:.1} dB");
        assert!(pass.abs() < 0.3, "passband {pass:.2} dB");
        assert!(stop < -80.0, "alias rejection {stop:.1} dB");
    }
}
