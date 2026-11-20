//! drift-core: Drift's music core. Plays local files and streams, keeps the queue going, owns the media keys and
//! the tray icon. No window, a few MB of memory. The Drift window is only a remote control: it can close and the
//! music keeps playing here. Control: line-delimited JSON over 127.0.0.1 (port + token in %LOCALAPPDATA%/Drift/core).
#![windows_subsystem = "windows"]

mod audio;
mod card;
mod listening;
mod polish;
mod peak;
mod tray;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::Command;
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

#[derive(Clone, Serialize, Deserialize, Default, Debug)]
pub struct Item {
    pub id: String,
    #[serde(default)]
    pub src: Option<String>,
    #[serde(default)]
    pub headers: HashMap<String, String>,
    #[serde(default)]
    pub page: Option<String>,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub artist: String,
    #[serde(default)]
    pub duration: u64,
    #[serde(default)]
    pub save: bool,
    #[serde(default)]
    pub radio: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub thumb: Option<String>, // cover picture link (the player card fetches it when the window has not saved one)
}

impl Item {
    fn online(&self) -> bool {
        self.src.as_deref().map_or(true, |s| s.starts_with("http"))
    }
}

pub struct State {
    pub current: Option<Item>,
    pub upcoming: Vec<Item>,
    pub history: Vec<Item>,
    pub volume: f32,
    pub repeat: String,
    pub drift: bool,
    pub client: Option<TcpStream>,
    pub client_id: u64,
    pub helper: Vec<String>,
    pub ui: Vec<String>,
    pub port: u16,
    pub token: String,
    pub req: u64,
    pub pending: HashMap<u64, String>, // request id -> what it was for ("resolve", "refill")
    pub refilling: bool,
    pub saved: bool,
    pub last_active: Instant,
}

#[derive(Clone)]
pub struct Ctx {
    pub st: Arc<Mutex<State>>,
    pub audio: mpsc::Sender<audio::Cmd>,
    pub info: Arc<Mutex<audio::Info>>,
}

pub fn data_dir() -> PathBuf {
    let base = std::env::var("DRIFT_DATA").map(PathBuf::from).unwrap_or_else(|_| {
        PathBuf::from(std::env::var("LOCALAPPDATA").unwrap_or_else(|_| ".".into())).join("Drift")
    });
    let _ = std::fs::create_dir_all(&base);
    base
}

fn token() -> String {
    let t = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
    format!("{:x}{:x}", t.as_nanos(), std::process::id() as u128 * 0x9E37_79B9_7F4A_7C15)
}

impl Ctx {
    /// Tell the window (when one is connected) what happened; false when no window got it.
    pub fn emit(&self, v: Value) -> bool {
        let mut st = self.st.lock().unwrap();
        if let Some(c) = st.client.as_mut() {
            let line = v.to_string() + "\n";
            if c.write_all(line.as_bytes()).is_ok() {
                return true;
            }
            st.client = None;
        }
        false
    }

    fn connected(&self) -> bool {
        self.st.lock().unwrap().client.is_some()
    }

    /// Start `item` (src resolved first when it has none).
    pub fn load(&self, item: Item, start_ms: u64, from_core: bool) {
        {
            let mut st = self.st.lock().unwrap();
            if let Some(cur) = st.current.take() {
                st.history.push(cur);
                let n = st.history.len();
                if n > 100 {
                    st.history.drain(0..n - 100);
                }
            }
            st.current = Some(item.clone());
            st.saved = false;
            st.last_active = Instant::now();
        }
        tray::changed();
        if from_core {
            self.emit(json!({"ev": "track", "item": item}));
        }
        if item.src.is_none() {
            let _ = self.audio.send(audio::Cmd::Disarm);
            self.resolve(item);
        } else {
            let _ = self.audio.send(audio::Cmd::Load(item, start_ms));
        }
    }

    /// An online song without a stream link: the window finds it (when open), else a short-lived helper does.
    fn resolve(&self, item: Item) {
        let req = {
            let mut st = self.st.lock().unwrap();
            st.req += 1;
            let r = st.req;
            st.pending.insert(r, "resolve".into());
            r
        };
        if !self.emit(json!({"ev": "resolve", "req": req, "item": item})) {
            self.helper("resolve", req, &item);
        }
    }

    pub fn helper(&self, op: &str, req: u64, item: &Item) {
        let (cmd, port, token, upcoming) = {
            let st = self.st.lock().unwrap();
            let upcoming: Vec<Value> = if op == "refill" {
                st.upcoming.iter().take(2).map(|t| json!({
                    "id": t.id, "title": t.title, "artist": t.artist, "duration": t.duration,
                    "page": t.page, "radio": t.radio
                })).collect()
            } else { vec![] };
            (st.helper.clone(), st.port, st.token.clone(), upcoming)
        };
        let cmd = if cmd.is_empty() { default_ui() } else { cmd };
        if cmd.is_empty() {
            return;
        }
        let mut c = Command::new(&cmd[0]);
        c.args(&cmd[1..]).args(["--net", op, "--port", &port.to_string(), "--token", &token, "--req", &req.to_string(),
                                 "--item", &serde_json::to_string(item).unwrap_or_default()]);
        if op == "refill" {
            c.args(["--upcoming", &serde_json::to_string(&upcoming).unwrap_or_default()]);
        }
        use std::os::windows::process::CommandExt;
        c.creation_flags(0x0800_0000 | 0x0000_4000); // no window, below-normal priority
        if c.spawn().is_err() && op == "refill" {
            let mut st = self.st.lock().unwrap();
            st.pending.remove(&req);
            st.refilling = false;
        }
    }

    pub fn next(&self, auto: bool) {
        let (repeat, cur) = {
            let st = self.st.lock().unwrap();
            (st.repeat.clone(), st.current.clone())
        };
        if !auto {
            if let Some(c) = &cur {
                let _ = self.audio.send(audio::Cmd::Leave(c.id.clone()));
            }
        }
        if auto && repeat == "one" {
            if let Some(c) = cur {
                let _ = self.audio.send(audio::Cmd::Load(c, 0));
                return;
            }
        }
        let next = {
            let mut st = self.st.lock().unwrap();
            if st.upcoming.is_empty() && repeat == "all" && !st.history.is_empty() {
                let mut again: Vec<Item> = st.history.drain(..).collect();
                if let Some(c) = st.current.clone() {
                    again.push(c);
                }
                st.upcoming = again;
            }
            if st.upcoming.is_empty() {
                None
            } else {
                Some(st.upcoming.remove(0))
            }
        };
        match next {
            Some(item) => {
                self.load(item, 0, true);
                self.top_up();
            }
            None => {
                let _ = self.audio.send(audio::Cmd::Stop);
                self.emit(json!({"ev": "ended"}));
                self.top_up();
            }
        }
    }

    fn next_from_refill(&self) {
        let next = {
            let mut st = self.st.lock().unwrap();
            if st.upcoming.is_empty() || st.client.is_some() { return; }
            st.upcoming.remove(0)
        };
        self.load(next, 0, true);
        self.top_up();
    }

    pub fn previous(&self) {
        let pos = self.info.lock().unwrap().pos;
        let prev = {
            let mut st = self.st.lock().unwrap();
            if pos > 3000 || st.history.is_empty() {
                None
            } else {
                let p = st.history.pop();
                if let Some(c) = st.current.take() {
                    st.upcoming.insert(0, c);
                }
                p
            }
        };
        match prev {
            Some(item) => {
                {
                    let mut st = self.st.lock().unwrap();
                    st.current = Some(item.clone());
                }
                tray::changed();
                self.emit(json!({"ev": "track", "item": item, "back": true}));
                if item.src.is_none() {
                    let _ = self.audio.send(audio::Cmd::Disarm);
                    self.resolve(item);
                } else {
                    let _ = self.audio.send(audio::Cmd::Load(item, 0));
                }
            }
            None => {
                let _ = self.audio.send(audio::Cmd::Seek(0));
            }
        }
    }

    /// Drift radio while the window is closed: when two or fewer songs are left, a helper brings more.
    fn top_up(&self) {
        let (need, cur, connected) = {
            let st = self.st.lock().unwrap();
            (st.drift && st.upcoming.len() < 3 && !st.refilling, st.current.clone(), st.client.is_some())
        };
        if !need {
            return;
        }
        if connected && self.emit(json!({"ev": "need"})) {
            return;
        }
        let req = {
            let mut st = self.st.lock().unwrap();
            st.req += 1;
            st.refilling = true;
            let r = st.req;
            st.pending.insert(r, "refill".into());
            r
        };
        self.helper("refill", req, &cur.unwrap_or_default());
    }

    pub fn toggle(&self) {
        let (playing, has, loading) = {
            let i = self.info.lock().unwrap();
            (i.playing, i.loaded, i.loading)
        };
        if loading {
            let _ = self.audio.send(audio::Cmd::Pause); // still opening: start it paused
        } else if has {
            let _ = self.audio.send(if playing { audio::Cmd::Pause } else { audio::Cmd::Play });
        } else {
            let cur = self.st.lock().unwrap().current.clone();
            match cur {
                Some(c) => self.load_again(c),
                None => self.next(false),
            }
        }
    }

    fn load_again(&self, c: Item) {
        if c.src.is_none() {
            self.resolve(c);
        } else {
            let _ = self.audio.send(audio::Cmd::Load(c, 0));
        }
    }

    fn snapshot(&self) -> Value {
        let st = self.st.lock().unwrap();
        let i = self.info.lock().unwrap();
        json!({"ev": "state", "current": st.current, "upcoming": st.upcoming, "pos": i.pos, "dur": i.dur,
               "listened": if st.current.as_ref().is_some_and(|t| t.id == i.audio_id) { i.listened } else { 0 },
               "playing": i.playing, "loaded": i.loaded, "loading": i.loading, "volume": st.volume, "repeat": st.repeat, "drift": st.drift,
               "memory_kb": memory_kb()})
    }

    /// One command from the window or a helper.
    fn command(&self, v: &Value, from_client: bool) {
        let cmd = v["cmd"].as_str().unwrap_or("");
        let item = || serde_json::from_value::<Item>(v["item"].clone()).ok();
        match cmd {
            "hello" => {
                {
                    let mut st = self.st.lock().unwrap();
                    if st.refilling {
                        // The window now owns the queue, even if it closes before this helper returns.
                        st.pending.retain(|_, op| op != "refill");
                        st.refilling = false;
                    }
                    if let Some(h) = v["helper"].as_array() {
                        st.helper = h.iter().filter_map(|x| x.as_str().map(String::from)).collect();
                    }
                    if let Some(h) = v["ui"].as_array() {
                        st.ui = h.iter().filter_map(|x| x.as_str().map(String::from)).collect();
                    }
                }
                let s = self.snapshot();
                self.emit(s);
            }
            "load" => {
                if let Some(it) = item() {
                    self.load(it, v["start"].as_u64().unwrap_or(0), false);
                    if v["paused"].as_bool().unwrap_or(false) {
                        let _ = self.audio.send(audio::Cmd::Pause);
                    }
                }
            }
            "upcoming" => {
                if let Ok(items) = serde_json::from_value::<Vec<Item>>(v["items"].clone()) {
                    self.st.lock().unwrap().upcoming = items;
                }
            }
            "toggle" => self.toggle(),
            "play" => {
                let _ = self.audio.send(audio::Cmd::Play);
            }
            "pause" => {
                let _ = self.audio.send(audio::Cmd::Pause);
            }
            "leave" => {
                if let Some(id) = v["id"].as_str() {
                    let _ = self.audio.send(audio::Cmd::Leave(id.into()));
                }
            }
            "next" => self.next(false),
            "prev" => self.previous(),
            "seek" => {
                let _ = self.audio.send(audio::Cmd::Seek(v["ms"].as_u64().unwrap_or(0)));
            }
            "card_png" => {
                let ok = card::snapshot(v["path"].as_str().unwrap_or(""));
                self.emit(json!({"ev": "card_png", "ok": ok}));
            }
            "volume" => {
                let vol = v["v"].as_f64().unwrap_or(0.6) as f32;
                self.st.lock().unwrap().volume = vol;
                let _ = self.audio.send(audio::Cmd::Volume(vol));
            }
            "mute" => {
                let _ = self.audio.send(audio::Cmd::Mute(v["on"].as_bool().unwrap_or(false)));
            }
            "sound" => {
                let (level, tone) = (v["level"].as_bool().unwrap_or(true), v["tone"].as_bool().unwrap_or(true));
                let _ = self.audio.send(audio::Cmd::Sound(level, tone));
            }
            "wave" => {
                let i = self.info.lock().unwrap();
                let w = json!({"ev": "wave", "id": i.wave_id, "wave": i.wave, "lufs": i.lufs, "gain": i.gain_db});
                drop(i);
                self.emit(w);
            }
            "repeat" => self.st.lock().unwrap().repeat = v["mode"].as_str().unwrap_or("off").into(),
            "drift" => self.st.lock().unwrap().drift = v["on"].as_bool().unwrap_or(true),
            "state" => {
                let s = self.snapshot();
                self.emit(s);
            }
            "stop" => {
                let _ = self.audio.send(audio::Cmd::Stop);
            }
            "quit" => quit(self),
            "reply" => self.reply(v),
            _ => {
                if from_client {
                    self.emit(json!({"ev": "error", "msg": format!("unknown command {cmd}")}));
                }
            }
        }
    }

    /// Answers from the window or a helper: a stream link, or more songs.
    fn reply(&self, v: &Value) {
        let req = v["req"].as_u64().unwrap_or(0);
        let what = self.st.lock().unwrap().pending.remove(&req);
        match what.as_deref() {
            Some("resolve") => {
                let id = v["id"].as_str().unwrap_or("").to_string();
                let cur = self.st.lock().unwrap().current.clone();
                if let Some(mut c) = cur.filter(|c| c.id == id) {
                    match v["src"].as_str() {
                        Some(src) => {
                            c.src = Some(src.into());
                            if let Ok(h) = serde_json::from_value::<HashMap<String, String>>(v["headers"].clone()) {
                                c.headers = h;
                            }
                            if let Some(d) = v["duration"].as_u64() {
                                c.duration = d;
                            }
                            self.st.lock().unwrap().current = Some(c.clone());
                            let _ = self.audio.send(audio::Cmd::Load(c, v["start"].as_u64().unwrap_or(0)));
                        }
                        None => {
                            self.emit(json!({"ev": "error", "id": id, "msg": v["error"].as_str().unwrap_or("cannot play")}));
                            self.next(true);
                        }
                    }
                }
            }
            Some("refill") => {
                self.st.lock().unwrap().refilling = false;
                if let Ok(items) = serde_json::from_value::<Vec<Item>>(v["items"].clone()) {
                    let mut st = self.st.lock().unwrap();
                    if !st.drift || st.client.is_some() {
                        return;
                    }
                    let repeat_current = v["repeat_current"].as_bool().unwrap_or(false) && st.upcoming.is_empty()
                        && items.len() == 1 && !items[0].online() && items[0].id == v["id"].as_str().unwrap_or("");
                    for item in items {
                        if (!repeat_current && st.current.as_ref().is_some_and(|c| c.id == item.id))
                            || st.upcoming.iter().any(|t| t.id == item.id) {
                            continue;
                        }
                        st.upcoming.push(item);
                    }
                    let ready = !st.upcoming.is_empty();
                    drop(st);
                    let i = self.info.lock().unwrap();
                    let idle = !i.loaded && !i.loading;
                    drop(i);
                    if ready && idle {
                        self.next_from_refill();
                    }
                }
            }
            _ => {}
        }
    }
}

pub fn default_ui() -> Vec<String> {
    let exe = std::env::current_exe().unwrap_or_default();
    let ui = exe.with_file_name("Drift.exe");
    if ui.is_file() {
        vec![ui.to_string_lossy().into()]
    } else {
        vec![]
    }
}

pub fn open_ui(ctx: &Ctx) {
    if ctx.emit(json!({"ev": "show"})) {
        return; // a window is there and heard it
    }
    let cmd = {
        let st = ctx.st.lock().unwrap();
        if st.ui.is_empty() { default_ui() } else { st.ui.clone() }
    };
    if !cmd.is_empty() {
        let _ = Command::new(&cmd[0]).args(&cmd[1..]).spawn();
    }
}

pub fn quit(ctx: &Ctx) {
    ctx.emit(json!({"ev": "quit"}));
    let _ = ctx.audio.send(audio::Cmd::Stop);
    tray::remove();
    let _ = std::fs::remove_file(data_dir().join("core"));
    thread::sleep(Duration::from_millis(80));
    std::process::exit(0);
}

pub fn memory_kb() -> u64 {
    use windows_sys::Win32::System::ProcessStatus::{GetProcessMemoryInfo, PROCESS_MEMORY_COUNTERS};
    use windows_sys::Win32::System::Threading::GetCurrentProcess;
    unsafe {
        let mut c: PROCESS_MEMORY_COUNTERS = std::mem::zeroed();
        c.cb = std::mem::size_of::<PROCESS_MEMORY_COUNTERS>() as u32;
        if GetProcessMemoryInfo(GetCurrentProcess(), &mut c, c.cb) != 0 {
            return (c.WorkingSetSize / 1024) as u64;
        }
    }
    0
}

fn serve(ctx: Ctx, listener: TcpListener) {
    for conn in listener.incoming().flatten() {
        let ctx = ctx.clone();
        thread::spawn(move || {
            let _ = conn.set_nodelay(true);
            let reader = BufReader::new(match conn.try_clone() {
                Ok(c) => c,
                Err(_) => return,
            });
            let mut lines = reader.lines();
            let first = lines.next().and_then(|l| l.ok()).unwrap_or_default();
            let hello: Value = serde_json::from_str(&first).unwrap_or(Value::Null);
            let tok = ctx.st.lock().unwrap().token.clone();
            if hello["token"].as_str() != Some(tok.as_str()) {
                return; // not ours
            }
            let window = hello["role"].as_str() == Some("ui");
            let mut me = 0;
            if window {
                let mut st = ctx.st.lock().unwrap();
                st.client_id += 1;
                me = st.client_id;
                st.client = conn.try_clone().ok();
            }
            ctx.command(&hello, window);
            for line in lines.map_while(Result::ok) {
                if let Ok(v) = serde_json::from_str::<Value>(&line) {
                    ctx.command(&v, window);
                }
            }
            if window {
                let mut st = ctx.st.lock().unwrap();
                if st.client_id == me {
                    st.client = None; // the window went away; the music stays
                }
            }
        });
    }
}

/// Every 100 ms: song finished -> next; tell the window the position; save a streamed song at 60% when alone.
/// The core never leaves on its own (tray icon, ~3 MB): only Quit ends it, so the tray can always reopen Drift.
fn watch(ctx: Ctx) {
    let mut last_pos = Instant::now();
    loop {
        thread::sleep(Duration::from_millis(100));
        let (ended, failed, pos, dur, playing, loading, listened, audio_id, why) = {
            let mut i = ctx.info.lock().unwrap();
            let e = std::mem::take(&mut i.ended);
            let f = std::mem::take(&mut i.failed);
            (e, f, i.pos, i.dur, i.playing, i.loading, i.listened, i.audio_id.clone(), std::mem::take(&mut i.error))
        };
        if failed {
            let id = ctx.st.lock().unwrap().current.as_ref().map(|c| c.id.clone()).unwrap_or_default();
            ctx.emit(json!({"ev": "error", "id": id, "msg": if why.is_empty() { "could not play".to_string() } else { why }}));
            ctx.next(true);
            continue;
        }
        if ended {
            ctx.emit(json!({"ev": "played"}));
            ctx.next(true);
            continue;
        }
        let wave = {
            let mut i = ctx.info.lock().unwrap();
            if std::mem::take(&mut i.wave_new) {
                Some(json!({"ev": "wave", "id": i.wave_id, "wave": i.wave, "lufs": i.lufs, "gain": i.gain_db}))
            } else {
                None
            }
        };
        if let Some(w) = wave {
            ctx.emit(w);
        }
        if last_pos.elapsed() >= Duration::from_millis(250) && ctx.connected() {
            last_pos = Instant::now();
            ctx.emit(json!({"ev": "pos", "ms": pos, "dur": dur, "listened": listened, "audio_id": audio_id, "playing": playing, "loading": loading}));
        }
        // alone: keep streamed songs you play through (the window does this itself when open)
        let save = {
            let mut st = ctx.st.lock().unwrap();
            let cur = st.current.clone();
            match cur {
                Some(c) if !st.saved && c.save && c.online() && dur > 0 && pos * 10 >= dur * 6 && st.client.is_none() => {
                    st.saved = true;
                    Some(c)
                }
                _ => None,
            }
        };
        if let Some(c) = save {
            ctx.helper("save", 0, &c);
        }
    }
}

fn main() {
    // one core at a time (per data folder: a test copy never meets the real one)
    use windows_sys::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS};
    use windows_sys::Win32::System::Threading::CreateMutexW;
    let dir = data_dir().to_string_lossy().to_lowercase();
    let h = dir.bytes().fold(0x811c_9dc5u32, |a, b| (a ^ b as u32).wrapping_mul(0x0100_0193));
    let name: Vec<u16> = format!("Local\\DriftCore-{h:08x}\0").encode_utf16().collect();
    unsafe {
        CreateMutexW(std::ptr::null(), 0, name.as_ptr());
        if GetLastError() == ERROR_ALREADY_EXISTS {
            return;
        }
    }
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().unwrap().port();
    let tok = token();
    let _ = std::fs::write(data_dir().join("core"), format!("{port} {tok} {}", std::process::id()));
    let info = Arc::new(Mutex::new(audio::Info::default()));
    let (tx, rx) = mpsc::channel();
    let ctx = Ctx {
        st: Arc::new(Mutex::new(State {
            current: None, upcoming: vec![], history: vec![], volume: 0.6, repeat: "off".into(), drift: true,
            client: None, client_id: 0, helper: vec![], ui: vec![], port, token: tok, req: 0, pending: HashMap::new(),
            refilling: false, saved: false, last_active: Instant::now(),
        })),
        audio: tx,
        info: info.clone(),
    };
    let tx2 = ctx.audio.clone();
    thread::spawn(move || audio::run(tx2, rx, info));
    let c = ctx.clone();
    thread::spawn(move || serve(c, listener));
    let c = ctx.clone();
    thread::spawn(move || watch(c));
    tray::run(ctx); // the message loop: tray icon + media keys, until quit
}
