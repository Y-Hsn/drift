//! The sound: one output stream and one sink on their own thread. Local files are decoded directly; streams are
//! fetched by Windows' own curl.exe (ranged, so YouTube does not throttle them) into a small cache file, then played.
//! Songs are opened on a loader thread, so pause / next / seek never wait for the network.
use crate::polish::{self, Analysis, Polish, Shared};
use crate::Item;
use rodio::{Sink, Source};
use std::fs::File;
use std::io::Read;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, Instant};
use symphonia::core::audio::{Channels, SampleBuffer, SignalSpec};
use symphonia::core::codecs::{Decoder, DecoderOptions, CODEC_TYPE_NULL};
use symphonia::core::formats::{FormatOptions, FormatReader, SeekMode, SeekTo};
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;
use symphonia::core::probe::Hint;
use symphonia::core::units::Time;

pub enum Cmd {
    Load(Item, u64),
    Ready(u64, Box<Song>),
    LoadFailed(u64, String),
    Play,
    Pause,
    Seek(u64),
    Leave(String),
    Disarm,
    DeviceReload(Item, u64, u64),
    Volume(f32),
    Mute(bool),
    Stop,
    Measured(u64, String, Analysis),
    Sound(bool, bool), // level songs, tone finish
}

#[derive(Default)]
pub struct Info {
    pub pos: u64,
    pub listened: u64,
    pub audio_id: String,
    pub dur: u64,
    pub playing: bool,
    pub loaded: bool,
    pub loading: bool,
    pub ended: bool,
    pub failed: bool,
    pub error: String,
    pub wave_id: String, // the song the shape belongs to
    pub wave: Vec<u8>,
    pub lufs: f32,
    pub gain_db: f32,
    pub wave_new: bool, // a new shape for the window
}

// --- decoding -------------------------------------------------------------------------------------------------
/// One song, decoded packet by packet (symphonia directly: rodio's own wrapper rejects every .m4a).
pub struct Song {
    format: Box<dyn FormatReader>,
    decoder: Box<dyn Decoder>,
    track: u32,
    buf: Option<SampleBuffer<f32>>,
    idx: usize,
    spec: SignalSpec,
    dur: Option<Duration>,
    base: Duration, // where the sink's clock 0 is in the song (after a seek on load)
    path: PathBuf,
    known: Option<Analysis>,
}

impl Song {
    fn open(path: &Path) -> Result<Song, String> {
        let file = File::open(path).map_err(|e| e.to_string())?;
        let mss = MediaSourceStream::new(Box::new(file), Default::default());
        let mut hint = Hint::new();
        if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
            hint.with_extension(ext);
        }
        let opts = FormatOptions { enable_gapless: true, ..Default::default() };
        let probed = symphonia::default::get_probe()
            .format(&hint, mss, &opts, &MetadataOptions::default())
            .map_err(|e| format!("unsupported file ({e})"))?;
        let format = probed.format;
        let track = format.tracks().iter().find(|t| t.codec_params.codec != CODEC_TYPE_NULL).ok_or("no audio in file")?;
        let decoder = symphonia::default::get_codecs()
            .make(&track.codec_params, &DecoderOptions::default())
            .map_err(|e| format!("unsupported codec ({e})"))?;
        let dur = track.codec_params.time_base.zip(track.codec_params.n_frames).map(|(b, n)| {
            let t = b.calc_time(n);
            Duration::from_secs(t.seconds) + Duration::from_secs_f64(t.frac)
        });
        let id = track.id;
        let spec = SignalSpec::new(track.codec_params.sample_rate.unwrap_or(44100),
                                   track.codec_params.channels.unwrap_or(Channels::FRONT_LEFT | Channels::FRONT_RIGHT));
        let mut s = Song { format, decoder, track: id, buf: None, idx: 0, spec, dur, base: Duration::ZERO,
                            path: path.to_path_buf(), known: None };
        if !s.fill() {
            return Err("empty file".into());
        }
        Ok(s)
    }

    /// Decode the next packet of our track into the buffer; false at the end.
    fn fill(&mut self) -> bool {
        let mut errors = 0;
        loop {
            let packet = match self.format.next_packet() {
                Ok(p) => p,
                Err(_) => return false,
            };
            if packet.track_id() != self.track {
                continue;
            }
            match self.decoder.decode(&packet) {
                Ok(audio) => {
                    if audio.frames() == 0 {
                        continue;
                    }
                    let spec = *audio.spec();
                    let fits = matches!(&self.buf, Some(b) if self.spec == spec
                        && b.capacity() >= audio.capacity() * spec.channels.count());
                    if !fits {
                        self.buf = Some(SampleBuffer::new(audio.capacity() as u64, spec));
                    }
                    self.buf.as_mut().unwrap().copy_interleaved_ref(audio);
                    self.spec = spec;
                    self.idx = 0;
                    return true;
                }
                Err(symphonia::core::errors::Error::DecodeError(_)) if errors < 30 => errors += 1,
                Err(_) => return false,
            }
        }
    }

    fn seek_to(&mut self, pos: Duration) -> bool {
        let time = Time::new(pos.as_secs(), pos.subsec_nanos() as f64 / 1e9);
        if self.format.seek(SeekMode::Coarse, SeekTo::Time { time, track_id: Some(self.track) }).is_err() {
            return false;
        }
        self.decoder.reset();
        self.fill()
    }

    fn remaining(&self) -> usize {
        self.buf.as_ref().map_or(0, |b| b.len().saturating_sub(self.idx))
    }
}

impl Iterator for Song {
    type Item = f32;
    fn next(&mut self) -> Option<f32> {
        if self.remaining() == 0 && !self.fill() {
            return None;
        }
        let v = self.buf.as_ref()?.samples()[self.idx];
        self.idx += 1;
        Some(v)
    }
}

impl Source for Song {
    fn current_frame_len(&self) -> Option<usize> {
        Some(self.remaining().max(1))
    }
    fn channels(&self) -> u16 {
        self.spec.channels.count() as u16
    }
    fn sample_rate(&self) -> u32 {
        self.spec.rate
    }
    fn total_duration(&self) -> Option<Duration> {
        self.dur
    }
    fn try_seek(&mut self, pos: Duration) -> Result<(), rodio::source::SeekError> {
        if self.seek_to(pos) {
            Ok(())
        } else {
            Err(rodio::source::SeekError::NotSupported { underlying_source: "drift song" })
        }
    }
}

// --- streams --------------------------------------------------------------------------------------------------
const CHUNK: u64 = 10 * 1024 * 1024; // ranged requests: YouTube throttles one long download to real time
const KEEP: usize = 8; // recent streams kept on disk, so Previous and replays start at once

#[derive(Clone, Default)]
struct Fetch {
    child: Arc<Mutex<Option<Child>>>,
    stop: Arc<AtomicBool>,
}

/// The player's handle on a download: dropping it (a newer song started) stops curl.
struct Download(Fetch);

impl Drop for Download {
    fn drop(&mut self) {
        self.0.stop.store(true, Ordering::Relaxed);
        if let Some(mut c) = self.0.child.lock().unwrap().take() {
            let _ = c.kill();
            let _ = c.wait();
        }
    }
}

fn cache_dir() -> PathBuf {
    let d = crate::data_dir().join("stream");
    let _ = std::fs::create_dir_all(&d);
    d
}

/// Where a streamed song is kept once fetched (by its id).
pub fn cached(id: &str) -> PathBuf {
    let h = id.bytes().fold(0xcbf2_9ce4_8422_2325u64, |a, b| (a ^ b as u64).wrapping_mul(0x100_0000_01b3));
    cache_dir().join(format!("{h:016x}.m4a"))
}

fn tidy_cache() {
    let mut files: Vec<_> = std::fs::read_dir(cache_dir()).into_iter().flatten().flatten()
        .filter_map(|e| Some((e.metadata().ok()?.modified().ok()?, e.path())))
        .collect();
    files.sort();
    let n = files.len();
    for (_, p) in files.into_iter().take(n.saturating_sub(KEEP)) {
        let _ = std::fs::remove_file(p);
    }
}

/// Fetch `url` into the cache with curl, in 10 MB ranges; the path once complete.
fn fetch(item: &Item, url: &str, f: &Fetch) -> Result<PathBuf, String> {
    let done = cached(&item.id);
    let part = done.with_extension("part");
    let mut out = File::create(&part).map_err(|e| e.to_string())?;
    let mut at = 0u64;
    loop {
        if f.stop.load(Ordering::Relaxed) {
            return Err("stopped".into());
        }
        let mut c = Command::new("curl.exe");
        c.args(["-sS", "-L", "--fail", "--retry", "2", "--connect-timeout", "10", "--speed-time", "20", "-r"])
            .arg(format!("{}-{}", at, at + CHUNK - 1));
        for (k, v) in &item.headers {
            if !k.eq_ignore_ascii_case("accept-encoding") && !k.eq_ignore_ascii_case("range") {
                c.arg("-H").arg(format!("{k}: {v}"));
            }
        }
        c.arg(url).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
        c.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        let mut p = c.spawn().map_err(|e| format!("curl: {e}"))?;
        let mut pipe = p.stdout.take().ok_or("curl pipe")?;
        *f.child.lock().unwrap() = Some(p);
        let mut got = 0u64;
        let mut buf = vec![0u8; 64 * 1024];
        while let Ok(n) = pipe.read(&mut buf) {
            if n == 0 {
                break;
            }
            std::io::Write::write_all(&mut out, &buf[..n]).map_err(|e| e.to_string())?;
            got += n as u64;
        }
        let ok = match f.child.lock().unwrap().take() {
            Some(mut p) => p.wait().map(|s| s.success()).unwrap_or(false),
            None => false, // killed: a newer song started
        };
        if f.stop.load(Ordering::Relaxed) || (!ok && got == 0) {
            drop(out);
            let _ = std::fs::remove_file(&part);
            return Err(if f.stop.load(Ordering::Relaxed) { "stopped".into() } else { "the stream would not download".into() });
        }
        at += got;
        if got != CHUNK {
            break; // the last piece (or a server that sent it all at once)
        }
    }
    drop(out);
    std::fs::rename(&part, &done).map_err(|e| e.to_string())?;
    tidy_cache();
    Ok(done)
}

// --- the player -----------------------------------------------------------------------------------------------
/// The speakers: our own cpal stream with a roomy buffer (~120 ms instead of Windows' default ~10 ms), so opening
/// apps, dragging windows or a game loading never starves the sound. The callback thread asks Windows' multimedia
/// scheduler (MMCSS "Pro Audio") for real-time priority on its first call. Songs are mixed in like rodio does.
pub struct Output {
    _stream: cpal::Stream,
    mixer: Arc<rodio::dynamic_mixer::DynamicMixerController<f32>>,
    sink: Sink,
    rate: u32,
    channels: u16,
}

impl Output {
    fn fresh_sink(&self) -> Sink {
        let (sink, queue) = Sink::new_idle();
        self.mixer.add(queue);
        sink
    }
}

const BUFFER_MS: u32 = 120;

fn mmcss() {
    use windows_sys::Win32::System::Threading::AvSetMmThreadCharacteristicsW;
    let name: Vec<u16> = "Pro Audio\0".encode_utf16().collect();
    let mut idx = 0u32;
    unsafe { AvSetMmThreadCharacteristicsW(name.as_ptr(), &mut idx) };
}

fn open_output() -> Option<Output> {
    use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
    let device = cpal::default_host().default_output_device()?;
    let sup = device.default_output_config().ok()?;
    let (chans, rate) = (sup.channels(), sup.sample_rate().0);
    let want = rate * BUFFER_MS / 1000;
    let frames = match sup.buffer_size() {
        cpal::SupportedBufferSize::Range { min, max } => want.clamp(*min, (*max).max(*min)),
        cpal::SupportedBufferSize::Unknown => want,
    };
    let build = |size: cpal::BufferSize| -> Option<(cpal::Stream, Arc<rodio::dynamic_mixer::DynamicMixerController<f32>>)> {
        let (mixer, mut rx) = rodio::dynamic_mixer::mixer::<f32>(chans, rate);
        let config = cpal::StreamConfig { channels: chans, sample_rate: cpal::SampleRate(rate), buffer_size: size };
        let err = |_e| {};
        let mut boosted = false;
        let stream = match sup.sample_format() {
            cpal::SampleFormat::F32 => device.build_output_stream::<f32, _, _>(&config, move |data, _| {
                if !boosted { mmcss(); boosted = true; }
                data.iter_mut().for_each(|d| *d = rx.next().unwrap_or(0.0));
            }, err, None),
            cpal::SampleFormat::I16 => device.build_output_stream::<i16, _, _>(&config, move |data, _| {
                if !boosted { mmcss(); boosted = true; }
                data.iter_mut().for_each(|d| *d = (rx.next().unwrap_or(0.0).clamp(-1.0, 1.0) * 32767.0) as i16);
            }, err, None),
            _ => return None,
        }.ok()?;
        stream.play().ok()?;
        Some((stream, mixer))
    };
    let (stream, mixer) = build(cpal::BufferSize::Fixed(frames)).or_else(|| build(cpal::BufferSize::Default))?;
    let (sink, queue) = Sink::new_idle();
    mixer.add(queue);
    Some(Output { _stream: stream, mixer, sink, rate, channels: chans })
}

fn device_name() -> String {
    use cpal::traits::{DeviceTrait, HostTrait};
    cpal::default_host().default_output_device().and_then(|d| d.name().ok()).unwrap_or_default()
}

/// Open `item` at `at` ms on a loader thread; it answers with Ready or LoadFailed.
fn load(item: Item, at: u64, gen: u64, tx: mpsc::Sender<Cmd>) -> Download {
    let f = Fetch::default();
    let mine = f.clone();
    std::thread::spawn(move || {
        let r = (|| -> Result<Song, String> {
            let src = item.src.clone().ok_or("no source")?;
            let path = if src.starts_with("http") {
                let c = cached(&item.id);
                if c.is_file() { c } else { fetch(&item, &src, &mine)? }
            } else {
                PathBuf::from(&src)
            };
            let mut song = Song::open(&path)?;
            song.known = polish::known(&polish::key_for(&item.id, &path));
            if at > 0 && song.seek_to(Duration::from_millis(at)) {
                song.base = Duration::from_millis(at);
            }
            Ok(song)
        })();
        let _ = tx.send(match r {
            Ok(s) => Cmd::Ready(gen, Box::new(s)),
            Err(e) => Cmd::LoadFailed(gen, e),
        });
    });
    Download(f)
}

/// Measure a song's loudness and shape on a quiet background thread (the game in front never notices).
fn analyse(id: String, path: PathBuf, gen: u64, tx: mpsc::Sender<Cmd>) {
    std::thread::spawn(move || {
        unsafe {
            use windows_sys::Win32::System::Threading::{GetCurrentThread, SetThreadPriority, THREAD_MODE_BACKGROUND_BEGIN};
            SetThreadPriority(GetCurrentThread(), THREAD_MODE_BACKGROUND_BEGIN as i32);
        }
        let Ok(song) = Song::open(&path) else { return };
        let spec = song.spec;
        if let Some(a) = polish::measure_layout(song, spec.rate, spec.channels) {
            polish::remember(&polish::key_for(&id, &path), &a);
            let _ = tx.send(Cmd::Measured(gen, id, a));
        }
    });
}

pub fn run(tx: mpsc::Sender<Cmd>, rx: mpsc::Receiver<Cmd>, info: Arc<Mutex<Info>>) {
    let mut out = open_output();
    let mut device = device_name();
    let events = crate::listening::writer();
    let mut meter = crate::listening::Meter::default();
    let mut sampled = Instant::now();
    let mut volume = 0.6f32;
    let mut muted = false;
    let mut current: Option<Item> = None;
    let mut download: Option<Download> = None;
    let mut gen = 0u64;
    let mut base = Duration::ZERO;
    let mut paused = false; // what the owner wants (also while a song is still loading)
    let mut had_source = false;
    let mut last_device_check = Instant::now();
    let gain = |v: f32, m: bool| if m { 0.0 } else { v.clamp(0.0, 1.0).powf(2.2) };
    let sh: Arc<Shared> = Shared::new();
    // fade the playing song out before it stops or pauses (no clicks); ~70 ms
    let fade_out = |sh: &Shared, out: &Option<Output>, live: bool| {
        if let Some(Output { sink: s, .. }) = out.as_ref() {
            if live && !s.is_paused() && !s.empty() {
                sh.fade_to(0.0);
                std::thread::sleep(Duration::from_millis(70));
            }
        }
    };

    loop {
        let cmd = rx.recv_timeout(Duration::from_millis(if had_source { 50 } else { 400 }));
        match cmd {
            Ok(Cmd::DeviceReload(_, _, expected)) if expected != gen => continue,
            Ok(Cmd::Load(ref item, at)) | Ok(Cmd::DeviceReload(ref item, at, _)) => {
                if matches!(cmd, Ok(Cmd::Load(..))) {
                    meter.reset();
                    paused = false;
                }
                gen += 1;
                drop(download.take()); // stop the old download before the new one starts
                fade_out(&sh, &out, had_source);
                if let Some(Output { sink: s, .. }) = out.as_ref() {
                    s.stop();
                }
                had_source = false;
                {
                    let mut i = info.lock().unwrap();
                    i.loaded = false;
                    i.loading = true;
                    i.playing = false;
                    i.pos = at;
                    i.listened = meter.heard;
                    i.audio_id = item.id.clone();
                    i.dur = item.duration;
                }
                download = Some(load(item.clone(), at, gen, tx.clone()));
                current = Some(item.clone());
            }
            Ok(Cmd::Ready(g, song)) => {
                if g != gen {
                    continue; // an older song finished opening after a newer one was asked for
                }
                if out.is_none() {
                    out = open_output();
                }
                let mut i = info.lock().unwrap();
                i.loading = false;
                if let Some(o) = out.as_mut() {
                    o.sink = o.fresh_sink();
                    let sink = &o.sink;
                    sink.set_volume(gain(volume, muted));
                    base = song.base;
                    let id = current.as_ref().map(|c| c.id.clone()).unwrap_or_default();
                    let mut song = song;
                    match song.known.take() {
                        Some(a) => {
                            sh.set_gain_db(a.gain_db(), true);
                            i.wave_id = id;
                            i.lufs = a.lufs;
                            i.gain_db = a.gain_db();
                            i.wave = a.wave;
                            i.wave_new = true;
                        }
                        None => {
                            sh.guess();
                            i.wave_id = id.clone();
                            i.wave.clear();
                            i.wave_new = true;
                            analyse(id, song.path.clone(), gen, tx.clone());
                        }
                    }
                    sh.fade_to(1.0);
                    if let Some(d) = song.dur {
                        i.dur = d.as_millis() as u64;
                    }
                    let layout = song.spec.channels;
                    sink.append(Polish::new(*song, sh.clone(), o.rate, o.channels, layout));
                    if paused {
                        sink.pause();
                    }
                    meter.baseline(0);
                    sampled = Instant::now();
                    had_source = true;
                    i.loaded = true;
                } else {
                    i.failed = true;
                    i.error = "no sound device".into();
                }
            }
            Ok(Cmd::LoadFailed(g, e)) => {
                if g == gen {
                    let mut i = info.lock().unwrap();
                    i.loading = false;
                    i.loaded = false;
                    if e != "stopped" {
                        i.failed = true;
                        i.error = e;
                    }
                }
            }
            Ok(Cmd::Play) => {
                paused = false;
                sh.fade_to(1.0);
                if let Some(Output { sink: s, .. }) = out.as_ref() {
                    s.play();
                }
            }
            Ok(Cmd::Pause) => {
                paused = true;
                fade_out(&sh, &out, had_source);
                if let Some(Output { sink: s, .. }) = out.as_ref() {
                    s.pause();
                }
            }
            Ok(Cmd::Measured(g, id, a)) => {
                if g == gen {
                    sh.set_gain_db(a.gain_db(), false); // glide there
                    let mut i = info.lock().unwrap();
                    i.wave_id = id;
                    i.lufs = a.lufs;
                    i.gain_db = a.gain_db();
                    i.wave = a.wave;
                    i.wave_new = true;
                }
            }
            Ok(Cmd::Sound(level, tone)) => {
                sh.level.store(level, Ordering::Relaxed);
                sh.tone.store(tone, Ordering::Relaxed);
            }
            Ok(Cmd::Leave(id)) => {
                if had_source && out.as_ref().is_some_and(|o| !o.sink.empty())
                    && current.as_ref().is_some_and(|t| t.id == id)
                    && meter.leave(info.lock().unwrap().dur) {
                    let _ = events.send((id, "skip"));
                }
            }
            Ok(Cmd::Disarm) => {
                meter.armed = false;
                info.lock().unwrap().audio_id.clear();
            }
            Ok(Cmd::Seek(ms)) => {
                if let Some(Output { sink: s, .. }) = out.as_ref() {
                    if had_source && s.try_seek(Duration::from_millis(ms)).is_ok() {
                        base = Duration::ZERO;
                        meter.baseline(ms);
                    }
                }
            }
            Ok(Cmd::Volume(v)) => {
                volume = v;
                if let Some(Output { sink: s, .. }) = out.as_ref() {
                    s.set_volume(gain(volume, muted));
                }
            }
            Ok(Cmd::Mute(m)) => {
                muted = m;
                if let Some(Output { sink: s, .. }) = out.as_ref() {
                    s.set_volume(gain(volume, muted));
                }
            }
            Ok(Cmd::Stop) => {
                gen += 1;
                fade_out(&sh, &out, had_source);
                if let Some(Output { sink: s, .. }) = out.as_ref() {
                    s.stop();
                }
                download = None;
                had_source = false;
                let mut i = info.lock().unwrap();
                i.loaded = false;
                i.loading = false;
                i.playing = false;
                i.pos = 0;
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => return,
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
        // position and the end of a song
        if let Some(Output { sink: s, .. }) = out.as_ref() {
            if had_source {
                let mut i = info.lock().unwrap();
                let sink_ms = s.get_pos().as_millis() as u64;
                let elapsed = sampled.elapsed().as_millis() as u64;
                sampled = Instant::now();
                if meter.sample(sink_ms, elapsed, i.dur) {
                    if let Some(t) = &current {
                        let _ = events.send((t.id.clone(), "listen"));
                    }
                }
                i.listened = meter.heard;
                i.pos = base.as_millis() as u64 + sink_ms;
                i.playing = !s.is_paused() && !s.empty();
                if s.empty() {
                    had_source = false;
                    i.loaded = false;
                    i.playing = false;
                    i.ended = true;
                    download = None;
                }
            }
        }
        // headphones in or out: follow Windows' default output, carry on where the song was
        if last_device_check.elapsed() > Duration::from_secs(2) {
            last_device_check = Instant::now();
            let now = device_name();
            if now != device && !now.is_empty() {
                device = now;
                let pos = info.lock().unwrap().pos;
                out = open_output();
                if had_source {
                    if let Some(item) = current.clone() {
                        let _ = tx.send(Cmd::DeviceReload(item, pos, gen));
                    }
                }
            }
        }
        let _ = &download;
    }
}

#[cfg(test)]
mod tests {
    /// DRIFT_SONG=<file> cargo test --release -- --ignored real_song --nocapture: how long a song takes to measure.
    #[test]
    #[ignore]
    fn real_song() {
        let path = std::path::PathBuf::from(std::env::var("DRIFT_SONG").expect("DRIFT_SONG"));
        let t = std::time::Instant::now();
        let song = super::Song::open(&path).unwrap();
        let spec = song.spec;
        let a = crate::polish::measure_layout(song, spec.rate, spec.channels).unwrap();
        println!("{:.1} LUFS, gain {:+.1} dB, {} ms", a.lufs, a.gain_db(), t.elapsed().as_millis());
    }
}
