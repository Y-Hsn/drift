//! The phone door (docs/phone-sync.md): a tiny HTTP server on 127.0.0.1 that paired phones reach through a free
//! Cloudflare quick tunnel. It checks every request (device key signature, 2-minute window, no replays) before
//! anything else wakes, serves the shelf's song files from disk (with byte ranges), keeps the phone's events in an
//! inbox, and runs the shelf brain (`Drift.exe --net phone-shelf|phone-fill|phone-events`, drift/phone.py) only
//! when a phone syncs. The tunnel's address changes when it restarts: it is posted encrypted (AES-GCM with the
//! pairing's link key) to a private ntfy.sh topic, where the phone looks it up.
//!
//! DATA/phone/door.json   this PC: id, name, ntfy topic, link key, local port, address generation
//! DATA/phone/devices.json paired phones and their keys (the folder is the user's own)
//! DATA/phone/<device>/    shelf.json (from phone.py), inbox/, acked
use crate::{data_dir, default_ui, Ctx};
use aes_gcm::aead::Aead;
use aes_gcm::{Aes256Gcm, KeyInit, Nonce};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const APP: &str = "https://y-hsn.github.io/drift/phone/";
const CLOUDFLARED_URL: &str =
    "https://github.com/cloudflare/cloudflared/releases/download/2026.11.1/cloudflared-windows-amd64.exe";
const CLOUDFLARED_SHA: &str = "341deda3db5ec498a0482254d6c38c0b7cf01a30c37bb5dd95d2216d08955e35";
const INVITE_MS: u64 = 5 * 60_000;
const TICKET_MS: u64 = 24 * 3600_000;
const SKEW_MS: u64 = 120_000;
const ANNOUNCE_EVERY_MS: u64 = 6 * 3600_000; // ntfy keeps messages 12 h
const SHELF_FRESH_MS: u64 = 15 * 60_000;
const FILL_EVERY_MS: u64 = 20 * 60_000;
const MAX_CONNS: usize = 64; // the tunnel opens a few per phone; more than this is someone knocking
const MAX_LINE: u64 = 8 * 1024;
const NO_WINDOW: u32 = 0x0800_0000;
const BELOW_NORMAL: u32 = 0x0000_4000;

#[derive(Serialize, Deserialize, Clone, Default)]
struct Config {
    pc: String,
    name: String,
    topic: String,
    link: String,
    #[serde(default)]
    port: u16,
    #[serde(default)]
    gen: u64,
}

#[derive(Serialize, Deserialize, Clone)]
struct Device {
    device: String,
    name: String,
    key: String,
    created: u64,
    #[serde(default)]
    seen: u64,
    #[serde(default)]
    size: u64,
}

#[derive(Default)]
struct Door {
    cfg: Config,
    devices: Vec<Device>,
    invites: HashMap<String, u64>,
    tickets: HashMap<String, u64>,
    seen: HashMap<String, u64>,
    url: Option<String>,
    link: String, // off | fetching | starting | up | failed
    announced: u64,
    jobs: HashSet<String>,  // devices with a phone.py run going (one at a time per device)
    again: HashSet<String>, // events arrived meanwhile: apply them after that run
    saved_seen: u64,
}

static DOOR: OnceLock<Arc<Mutex<Door>>> = OnceLock::new();
static CTX: OnceLock<Ctx> = OnceLock::new();
static CONNS: AtomicUsize = AtomicUsize::new(0);

fn door() -> &'static Arc<Mutex<Door>> {
    DOOR.get().expect("door started")
}

// ---- small tools ----

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

fn random(n: usize) -> Vec<u8> {
    let mut b = vec![0u8; n];
    getrandom::getrandom(&mut b).expect("random");
    b
}

const B64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

pub fn b64(data: &[u8]) -> String {
    let mut out = String::with_capacity(data.len() * 4 / 3 + 3);
    for c in data.chunks(3) {
        let n = (c[0] as u32) << 16 | (*c.get(1).unwrap_or(&0) as u32) << 8 | *c.get(2).unwrap_or(&0) as u32;
        for i in 0..c.len() + 1 {
            out.push(B64[(n >> (18 - 6 * i) & 63) as usize] as char);
        }
    }
    out
}

pub fn unb64(s: &str) -> Option<Vec<u8>> {
    let mut out = Vec::with_capacity(s.len() * 3 / 4);
    let (mut acc, mut bits) = (0u32, 0);
    for ch in s.bytes().filter(|c| *c != b'=') {
        let v = match ch {
            b'A'..=b'Z' => ch - b'A',
            b'a'..=b'z' => ch - b'a' + 26,
            b'0'..=b'9' => ch - b'0' + 52,
            b'-' | b'+' => 62,
            b'_' | b'/' => 63,
            _ => return None,
        };
        acc = acc << 6 | v as u32;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
        }
    }
    Some(out)
}

fn hex(data: &[u8]) -> String {
    data.iter().map(|b| format!("{b:02x}")).collect()
}

fn unhex(s: &str) -> Option<Vec<u8>> {
    if s.len() % 2 != 0 {
        return None;
    }
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(s.get(i..i + 2)?, 16).ok()).collect()
}

fn unpercent(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' && i + 2 < b.len() {
            if let Some(v) = s.get(i + 1..i + 3).and_then(|h| u8::from_str_radix(h, 16).ok()) {
                out.push(v);
                i += 3;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn root() -> PathBuf {
    let p = data_dir().join("phone");
    let _ = std::fs::create_dir_all(&p);
    p
}

fn write_json(path: &Path, v: &impl Serialize) {
    if let Some(d) = path.parent() {
        let _ = std::fs::create_dir_all(d);
    }
    let tmp = path.with_extension("tmp");
    if std::fs::write(&tmp, serde_json::to_vec_pretty(v).unwrap_or_default()).is_ok() {
        let _ = std::fs::rename(&tmp, path);
    }
}

fn read_json<T: for<'a> Deserialize<'a>>(path: &Path) -> Option<T> {
    serde_json::from_slice(&std::fs::read(path).ok()?).ok()
}

fn app_url() -> String {
    std::env::var("DRIFT_PHONE_APP").unwrap_or_else(|_| APP.into())
}

fn app_origin() -> String {
    let u = app_url();
    let rest = u.splitn(2, "://").nth(1).unwrap_or("");
    let host = rest.split('/').next().unwrap_or("");
    format!("{}://{}", u.split("://").next().unwrap_or("https"), host)
}

fn device_ok(id: &str) -> bool {
    !id.is_empty() && id.len() <= 40 && id.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
}

fn tell_window() {
    if let Some(ctx) = CTX.get() {
        ctx.emit(phones());
    }
}

fn save_devices(d: &Door) {
    write_json(&root().join("devices.json"), &d.devices);
}

// ---- start ----

pub fn start(ctx: Ctx) {
    let _ = CTX.set(ctx);
    let dir = root();
    let mut cfg: Config = read_json(&dir.join("door.json")).unwrap_or_default();
    if cfg.pc.is_empty() {
        cfg.pc = hex(&random(8));
        cfg.topic = format!("drift-{}", b64(&random(16)));
        cfg.link = b64(&random(32));
    }
    if cfg.name.is_empty() {
        // Windows' made-up names (DESKTOP-4F2K1QX) say nothing to a person: "My PC" / "My laptop" instead
        let n = std::env::var("COMPUTERNAME").unwrap_or_default();
        let up = n.to_ascii_uppercase();
        cfg.name = if n.is_empty() || up.starts_with("DESKTOP-") || up.starts_with("WIN-") { "My PC".into() }
                   else if up.starts_with("LAPTOP-") { "My laptop".into() } else { title_case(&n) };
    }
    let listener = TcpListener::bind(("127.0.0.1", cfg.port))
        .or_else(|_| TcpListener::bind("127.0.0.1:0"))
        .expect("door bind");
    cfg.port = listener.local_addr().map(|a| a.port()).unwrap_or(0);
    write_json(&dir.join("door.json"), &cfg);
    let devices: Vec<Device> = read_json(&dir.join("devices.json")).unwrap_or_default();
    let _ = DOOR.set(Arc::new(Mutex::new(Door { cfg, devices, link: "off".into(), ..Default::default() })));
    thread::spawn(move || {
        for conn in listener.incoming().flatten() {
            if CONNS.fetch_add(1, Ordering::SeqCst) >= MAX_CONNS {
                CONNS.fetch_sub(1, Ordering::SeqCst);
                continue; // dropped: closes the connection
            }
            thread::spawn(move || {
                serve(conn);
                CONNS.fetch_sub(1, Ordering::SeqCst);
            });
        }
    });
    thread::spawn(link_loop);
}

fn title_case(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        Some(f) => f.to_uppercase().collect::<String>() + &c.as_str().to_lowercase(),
        None => String::new(),
    }
}

// ---- the window's commands ----

pub fn command(ctx: &Ctx, cmd: &str, v: &Value) {
    match cmd {
        "phone_invite" => {
            let ctx = ctx.clone();
            thread::spawn(move || invite(&ctx));
        }
        "phones" => {
            ctx.emit(phones());
        }
        "phone_unpair" => {
            let id = v["device"].as_str().unwrap_or("").to_string();
            if device_ok(&id) {
                let mut d = door().lock().unwrap();
                d.devices.retain(|x| x.device != id);
                save_devices(&d);
                drop(d);
                let _ = std::fs::remove_dir_all(root().join(&id)); // its shelf list and inbox; songs stay in the library
            }
            ctx.emit(phones());
        }
        _ => {}
    }
}

fn phones() -> Value {
    let d = door().lock().unwrap();
    let list: Vec<Value> = d.devices.iter().map(|x| json!({"device": x.device, "name": x.name, "created": x.created,
                                                             "seen": x.seen, "size": x.size})).collect();
    json!({"ev": "phones", "list": list, "link": d.link, "pc": d.cfg.name})
}

/// A one-use invite, valid 5 minutes. Waits for the link (first time: fetches cloudflared) and sends the QR link.
fn invite(ctx: &Ctx) {
    let code = b64(&random(12));
    door().lock().unwrap().invites.insert(code.clone(), now_ms() + INVITE_MS);
    let started = Instant::now();
    let mut said = String::new();
    loop {
        let (url, link, topic) = {
            let d = door().lock().unwrap();
            (d.url.clone(), d.link.clone(), d.cfg.topic.clone())
        };
        if let Some(url) = url {
            let qr = format!("{}#i={}&u={}&t={}", app_url(), code, b64(url.as_bytes()), topic);
            ctx.emit(json!({"ev": "phone_invite", "url": qr, "expires": now_ms() + INVITE_MS}));
            return;
        }
        if link != said {
            said = link.clone();
            ctx.emit(json!({"ev": "phone_invite", "state": link}));
        }
        if link == "failed" || started.elapsed() > Duration::from_secs(240) {
            ctx.emit(json!({"ev": "phone_invite", "error": "Could not open the link to your phone. Check the internet and try again."}));
            return;
        }
        thread::sleep(Duration::from_millis(250));
    }
}

// ---- the link: a free Cloudflare quick tunnel to this door ----

fn cloudflared() -> Option<PathBuf> {
    let exe = root().join("cloudflared.exe");
    if exe.is_file() && sha256_file(&exe).as_deref() == Some(CLOUDFLARED_SHA) {
        return Some(exe);
    }
    door().lock().unwrap().link = "fetching".into();
    let part = root().join("cloudflared.part");
    let ok = Command::new("curl.exe")
        .args(["-sfL", "--retry", "3", "-m", "600", "-o"])
        .arg(&part)
        .arg(CLOUDFLARED_URL)
        .creation_flags_compat()
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if ok && sha256_file(&part).as_deref() == Some(CLOUDFLARED_SHA) {
        let _ = std::fs::remove_file(&exe);
        if std::fs::rename(&part, &exe).is_ok() {
            return Some(exe);
        }
    }
    let _ = std::fs::remove_file(&part); // a wrong file never runs
    None
}

fn sha256_file(p: &Path) -> Option<String> {
    let mut f = std::fs::File::open(p).ok()?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 16];
    loop {
        let n = f.read(&mut buf).ok()?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Some(hex(&h.finalize()))
}

trait Quiet {
    fn creation_flags_compat(&mut self) -> &mut Self;
}

impl Quiet for Command {
    fn creation_flags_compat(&mut self) -> &mut Self {
        use std::os::windows::process::CommandExt;
        self.creation_flags(NO_WINDOW | BELOW_NORMAL)
    }
}

fn link_loop() {
    let mut child: Option<Child> = None;
    let mut wait_until = Instant::now();
    let mut backoff = 5u64;
    loop {
        thread::sleep(Duration::from_secs(2));
        let (need, port, up, announced) = {
            let mut d = door().lock().unwrap();
            let now = now_ms();
            d.invites.retain(|_, exp| *exp > now);
            d.tickets.retain(|_, exp| *exp > now);
            d.seen.retain(|_, at| *at + 3 * SKEW_MS > now);
            (!d.devices.is_empty() || !d.invites.is_empty() || !d.tickets.is_empty(), d.cfg.port, d.url.is_some(), d.announced)
        };
        if let Some(c) = child.as_mut() {
            if matches!(c.try_wait(), Ok(Some(_))) {
                child = None;
                let mut d = door().lock().unwrap();
                d.url = None;
                d.link = "starting".into();
                wait_until = Instant::now() + Duration::from_secs(backoff);
                backoff = (backoff * 2).min(120);
            }
        }
        if !need {
            if let Some(mut c) = child.take() {
                let _ = c.kill();
                let mut d = door().lock().unwrap();
                d.url = None;
                d.link = "off".into();
            }
            continue;
        }
        if let Ok(url) = std::env::var("DRIFT_LINK_URL") {
            // tests: phone and door on this PC, no tunnel
            let mut d = door().lock().unwrap();
            if d.url.is_none() {
                d.url = Some(url);
                d.link = "up".into();
            }
            continue;
        }
        if child.is_none() && Instant::now() >= wait_until {
            match cloudflared() {
                Some(exe) => {
                    door().lock().unwrap().link = "starting".into();
                    child = spawn_tunnel(&exe, port);
                    if child.is_some() {
                        backoff = 5;
                    }
                }
                None => {
                    door().lock().unwrap().link = "failed".into();
                    wait_until = Instant::now() + Duration::from_secs(60);
                }
            }
        }
        if up && now_ms().saturating_sub(announced) > ANNOUNCE_EVERY_MS {
            announce();
        }
    }
}

fn spawn_tunnel(exe: &Path, port: u16) -> Option<Child> {
    let mut c = Command::new(exe)
        .args(["tunnel", "--no-autoupdate", "--url", &format!("http://127.0.0.1:{port}")])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .creation_flags_compat()
        .spawn()
        .ok()?;
    tie_to_core(&c);
    let err = c.stderr.take()?;
    thread::spawn(move || {
        let mut log = std::fs::File::create(root().join("link.log")).ok();
        for line in BufReader::new(err).lines().map_while(Result::ok) {
            if let Some(l) = log.as_mut() {
                let _ = writeln!(l, "{line}");
            }
            if let Some(at) = line.find("https://") {
                let url: String = line[at..].chars().take_while(|c| !c.is_whitespace() && *c != '|').collect();
                if url.ends_with(".trycloudflare.com") {
                    let mut d = door().lock().unwrap();
                    d.cfg.gen += 1;
                    write_json(&root().join("door.json"), &d.cfg);
                    d.url = Some(url);
                    d.link = "up".into();
                    drop(d);
                    thread::spawn(|| {
                        thread::sleep(Duration::from_secs(3)); // the new address needs a moment to answer
                        announce();
                        tell_window();
                    });
                }
            }
        }
    });
    Some(c)
}

/// The tunnel ends with the core, however the core ends (quit, crash, update): a job object that kills its
/// processes when its last handle (the core's) closes.
fn tie_to_core(c: &Child) {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    static JOB: OnceLock<usize> = OnceLock::new();
    let job = *JOB.get_or_init(|| unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return 0;
        }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(job, JobObjectExtendedLimitInformation, &info as *const _ as *const _,
                                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32);
        job as usize
    });
    if job != 0 {
        unsafe {
            AssignProcessToJobObject(job as _, c.as_raw_handle() as _);
        }
    }
}

/// `b64(iv).b64(AES-256-GCM(key, plain))`, what the phone's sync.js announced() opens.
fn seal(key: &[u8], plain: &str, iv: &[u8]) -> Option<String> {
    let cipher = Aes256Gcm::new_from_slice(key).ok()?;
    let sealed = cipher.encrypt(Nonce::from_slice(iv), plain.as_bytes()).ok()?;
    Some(format!("{}.{}", b64(iv), b64(&sealed)))
}

/// Post `{url, gen, exp}` to the ntfy topic, encrypted with the link key every paired phone got at pairing.
fn announce() {
    let (url, gen, key, topic) = {
        let mut d = door().lock().unwrap();
        d.announced = now_ms();
        match d.url.clone() {
            Some(u) => (u, d.cfg.gen, d.cfg.link.clone(), d.cfg.topic.clone()),
            None => return,
        }
    };
    let Some(key) = unb64(&key).filter(|k| k.len() == 32) else { return };
    let plain = json!({"url": url, "gen": gen, "exp": now_ms() + 14 * 3600_000}).to_string();
    let Some(msg) = seal(&key, &plain, &random(12)) else { return };
    if std::env::var("DRIFT_NO_NTFY").is_ok() {
        return; // tests
    }
    let _ = Command::new("curl.exe")
        .args(["-s", "-m", "20", "-o", "NUL", "-d", &msg, &format!("https://ntfy.sh/{topic}")])
        .creation_flags_compat()
        .status();
}

// ---- HTTP ----

struct Req {
    method: String,
    target: String,
    path: String,
    query: HashMap<String, String>,
    headers: HashMap<String, String>,
    body: Vec<u8>,
}

/// One line, at most MAX_LINE bytes (a line that never ends cannot fill the memory).
fn read_line(r: &mut BufReader<TcpStream>, line: &mut String) -> Option<usize> {
    let n = r.by_ref().take(MAX_LINE).read_line(line).ok()?;
    (n as u64 != MAX_LINE || line.ends_with('\n')).then_some(n)
}

fn read_req(r: &mut BufReader<TcpStream>) -> Option<Req> {
    let mut line = String::new();
    if read_line(r, &mut line)? == 0 {
        return None;
    }
    let mut parts = line.split_whitespace();
    let method = parts.next()?.to_string();
    let target = parts.next()?.to_string();
    let mut headers = HashMap::new();
    let mut size = 0;
    loop {
        let mut h = String::new();
        let n = read_line(r, &mut h)?;
        size += n;
        if n == 0 || size > 32 * 1024 {
            return None;
        }
        let h = h.trim_end();
        if h.is_empty() {
            break;
        }
        if let Some((k, v)) = h.split_once(':') {
            headers.insert(k.trim().to_ascii_lowercase(), v.trim().to_string());
        }
    }
    let len: usize = headers.get("content-length").and_then(|v| v.parse().ok()).unwrap_or(0);
    if len > 4 << 20 {
        return None;
    }
    let mut body = vec![0u8; len];
    r.read_exact(&mut body).ok()?;
    let (path, q) = target.split_once('?').unwrap_or((target.as_str(), ""));
    let query = q.split('&').filter_map(|kv| kv.split_once('=')).map(|(k, v)| (unpercent(k), unpercent(v))).collect();
    Some(Req { method, path: path.to_string(), target: target.clone(), query, headers, body })
}

struct Out<'a> {
    w: &'a mut TcpStream,
    cors: bool,
}

impl Out<'_> {
    fn head(&mut self, code: u16, kind: &str, len: u64, extra: &[(&str, String)]) -> std::io::Result<()> {
        let reason = match code {
            200 => "OK",
            204 => "No Content",
            206 => "Partial Content",
            400 => "Bad Request",
            401 => "Unauthorized",
            403 => "Forbidden",
            404 => "Not Found",
            416 => "Range Not Satisfiable",
            503 => "Service Unavailable",
            _ => "Error",
        };
        let mut h = format!("HTTP/1.1 {code} {reason}\r\nContent-Type: {kind}\r\nContent-Length: {len}\r\nCache-Control: no-store\r\n");
        if self.cors {
            h += &format!("Access-Control-Allow-Origin: {}\r\nVary: Origin\r\n", app_origin());
        }
        for (k, v) in extra {
            h += &format!("{k}: {v}\r\n");
        }
        h += "\r\n";
        self.w.write_all(h.as_bytes())
    }

    fn send(&mut self, code: u16, kind: &str, body: &[u8]) -> std::io::Result<()> {
        self.head(code, kind, body.len() as u64, &[])?;
        self.w.write_all(body)
    }

    fn json(&mut self, code: u16, v: &Value) -> std::io::Result<()> {
        self.send(code, "application/json", v.to_string().as_bytes())
    }

    fn text(&mut self, code: u16, s: &str) -> std::io::Result<()> {
        self.send(code, "text/plain; charset=utf-8", s.as_bytes())
    }

    /// A file, whole (200) or the byte range asked for (206): phones resume half-done songs this way.
    fn file(&mut self, path: &Path, range: Option<&String>, kind: &str) -> std::io::Result<()> {
        let mut f = match std::fs::File::open(path) {
            Ok(f) => f,
            Err(_) => return self.text(404, "gone"),
        };
        let len = f.metadata()?.len();
        let mut from = 0;
        let mut to = len.saturating_sub(1);
        let mut code = 200;
        if let Some(r) = range.and_then(|r| r.strip_prefix("bytes=")) {
            let (a, b) = r.split_once('-').unwrap_or((r, ""));
            match (a.parse::<u64>().ok(), b.parse::<u64>().ok()) {
                (Some(a), Some(b)) => (from, to) = (a, b.min(to)),
                (Some(a), None) => from = a,
                (None, Some(n)) => from = len.saturating_sub(n),
                _ => {}
            }
            if from > to || from >= len {
                return self.head(416, kind, 0, &[("Content-Range", format!("bytes */{len}"))]);
            }
            code = 206;
        }
        let n = to + 1 - from;
        let mut extra = vec![("Accept-Ranges", "bytes".to_string())];
        if code == 206 {
            extra.push(("Content-Range", format!("bytes {from}-{to}/{len}")));
        }
        self.head(code, kind, n, &extra)?;
        f.seek(SeekFrom::Start(from))?;
        std::io::copy(&mut f.take(n), self.w)?;
        Ok(())
    }
}

fn serve(conn: TcpStream) {
    let _ = conn.set_read_timeout(Some(Duration::from_secs(30)));
    let _ = conn.set_write_timeout(Some(Duration::from_secs(60)));
    let _ = conn.set_nodelay(true);
    let Ok(mut w) = conn.try_clone() else { return };
    let mut r = BufReader::new(conn);
    while let Some(req) = read_req(&mut r) {
        let cors = req.headers.get("origin").is_some_and(|o| *o == app_origin());
        let close = req.headers.get("connection").is_some_and(|c| c.eq_ignore_ascii_case("close"));
        let mut out = Out { w: &mut w, cors };
        if route(&req, &mut out).is_err() || close {
            break;
        }
        let _ = out.w.flush();
    }
}

fn route(req: &Req, out: &mut Out) -> std::io::Result<()> {
    if req.method == "OPTIONS" {
        return out.head(204, "text/plain", 0, &[
            ("Access-Control-Allow-Headers", "Content-Type, X-Drift-Device, X-Drift-Time, X-Drift-Sign, Range".into()),
            ("Access-Control-Allow-Methods", "GET, POST".into()),
            ("Access-Control-Max-Age", "600".into()),
        ]);
    }
    match (req.method.as_str(), req.path.as_str()) {
        ("POST", "/pair") => return pair(req, out),
        ("POST", "/pair/finish") => return pair_finish(req, out),
        _ => {}
    }
    let Some(dev) = signed(req) else { return out.text(401, "not paired") };
    match (req.method.as_str(), req.path.as_str()) {
        ("GET", "/hello") => {
            let (pc, name, filling) = {
                let d = door().lock().unwrap();
                (d.cfg.pc.clone(), d.cfg.name.clone(), d.jobs.contains(&dev.device))
            };
            out.json(200, &json!({"pc": pc, "name": name, "version": env!("CARGO_PKG_VERSION"), "filling": filling}))
        }
        ("GET", "/shelf") => shelf(req, out, &dev),
        ("POST", "/events") => events(req, out, &dev),
        ("GET", p) if p.starts_with("/song/") => song(req, out, &dev, &unpercent(&p[6..]), "files"),
        ("GET", p) if p.starts_with("/cover/") => song(req, out, &dev, &unpercent(&p[7..]), "covers"),
        _ => out.text(404, "no such thing"),
    }
}

// ---- pairing ----

fn body_json(req: &Req) -> Value {
    serde_json::from_slice(&req.body).unwrap_or(Value::Null)
}

fn pair(req: &Req, out: &mut Out) -> std::io::Result<()> {
    let v = body_json(req);
    let code = v["invite"].as_str().unwrap_or("");
    let mut d = door().lock().unwrap();
    if d.invites.remove(code).is_none_or_expired() {
        drop(d);
        return out.text(403, "This code has expired. Show a new one on your PC.");
    }
    let ticket = b64(&random(18));
    d.tickets.insert(ticket.clone(), now_ms() + TICKET_MS);
    let (pc, name) = (d.cfg.pc.clone(), d.cfg.name.clone());
    drop(d);
    out.json(200, &json!({"ticket": ticket, "pc": pc, "pcName": name}))
}

trait Expiry {
    fn is_none_or_expired(self) -> bool;
}

impl Expiry for Option<u64> {
    fn is_none_or_expired(self) -> bool {
        self.map_or(true, |exp| exp < now_ms())
    }
}

fn pair_finish(req: &Req, out: &mut Out) -> std::io::Result<()> {
    let v = body_json(req);
    let key = v["key"].as_str().and_then(unb64).filter(|k| k.len() == 32);
    let mut d = door().lock().unwrap();
    if d.tickets.remove(v["ticket"].as_str().unwrap_or("")).is_none_or_expired() || key.is_none() {
        drop(d);
        return out.text(403, "This pairing has expired. Scan a new code on your PC.");
    }
    let id = format!("d{}", hex(&random(6)));
    let name: String = v["name"].as_str().unwrap_or("Phone").chars().filter(|c| !c.is_control()).take(40).collect();
    d.devices.push(Device { device: id.clone(), name, key: b64(&key.unwrap()), created: now_ms(), seen: now_ms(), size: 0 });
    save_devices(&d);
    let answer = json!({"device": id, "link": d.cfg.link, "pcName": d.cfg.name, "topic": d.cfg.topic, "pc": d.cfg.pc});
    drop(d);
    tell_window();
    out.json(200, &answer)
}

/// The device whose key signed `method|target|time|sha256(body)`, within 2 minutes, never the same signature twice.
fn signed(req: &Req) -> Option<Device> {
    let id = req.headers.get("x-drift-device")?;
    let t: u64 = req.headers.get("x-drift-time")?.parse().ok()?;
    let sig = req.headers.get("x-drift-sign")?.to_ascii_lowercase();
    let now = now_ms();
    if now.abs_diff(t) > SKEW_MS {
        return None;
    }
    let mut d = door().lock().unwrap();
    let dev = d.devices.iter().find(|x| &x.device == id)?.clone();
    if d.seen.contains_key(&sig) {
        return None;
    }
    let key = unb64(&dev.key)?;
    let msg = format!("{}|{}|{}|{}", req.method, req.target, t, hex(&Sha256::digest(&req.body)));
    let mut mac = <Hmac<Sha256> as Mac>::new_from_slice(&key).ok()?;
    mac.update(msg.as_bytes());
    mac.verify_slice(&unhex(&sig)?).ok()?;
    d.seen.insert(sig, now);
    if let Some(x) = d.devices.iter_mut().find(|x| &x.device == id) {
        x.seen = now;
    }
    if now - d.saved_seen > 60_000 {
        d.saved_seen = now;
        save_devices(&d);
    }
    Some(dev)
}

// ---- the shelf ----

fn age_ms(p: &Path) -> Option<u64> {
    let m = std::fs::metadata(p).ok()?.modified().ok()?;
    Some(SystemTime::now().duration_since(m).map(|d| d.as_millis() as u64).unwrap_or(0))
}

fn has_inbox(dev: &str) -> bool {
    std::fs::read_dir(root().join(dev).join("inbox")).map(|mut r| r.next().is_some()).unwrap_or(false)
}

fn shelf(req: &Req, out: &mut Out, dev: &Device) -> std::io::Result<()> {
    let size: u64 = req.query.get("size").and_then(|s| s.parse().ok()).unwrap_or(2 << 30).clamp(256 << 20, 64 << 30);
    let have = req.query.get("have").cloned().unwrap_or_default();
    let dir = root().join(&dev.device);
    let file = dir.join("shelf.json");
    if dev.size != size {
        let mut d = door().lock().unwrap();
        if let Some(x) = d.devices.iter_mut().find(|x| x.device == dev.device) {
            x.size = size;
        }
        save_devices(&d);
    }
    let busy = door().lock().unwrap().jobs.contains(&dev.device);
    let stale = age_ms(&file).map_or(true, |a| a > SHELF_FRESH_MS) || dev.size != size || has_inbox(&dev.device);
    if !file.is_file() || (stale && !busy) {
        run(&dev.device, "phone-shelf", size, true);
    }
    let Some(mut v) = read_json::<Value>(&file) else {
        return out.json(503, &json!({"error": "Your PC is getting your songs ready. Try again in a minute."}));
    };
    // radio picks and wishes are fetched in the background; the phone collects them at its next sync
    let due = age_ms(&dir.join("filled")).map_or(true, |a| a > FILL_EVERY_MS)
        || read_json::<Vec<Value>>(&dir.join("wishes.json")).is_some_and(|w| !w.is_empty());
    if due && !door().lock().unwrap().jobs.contains(&dev.device) {
        let _ = std::fs::write(dir.join("filled"), now_ms().to_string());
        run(&dev.device, "phone-fill", size, false);
    }
    let filling = door().lock().unwrap().jobs.contains(&dev.device);
    let rev = v["rev"].as_str().unwrap_or("").to_string();
    if !have.is_empty() && have == rev {
        return out.json(200, &json!({"same": true, "rev": rev, "filling": filling}));
    }
    if let Some(o) = v.as_object_mut() {
        o.remove("files");
        o.remove("covers");
        o.remove("made");
        o.insert("filling".into(), json!(filling));
    }
    out.json(200, &v)
}

fn song(req: &Req, out: &mut Out, dev: &Device, id: &str, map: &str) -> std::io::Result<()> {
    let shelf: Value = read_json(&root().join(&dev.device).join("shelf.json")).unwrap_or(Value::Null);
    let Some(path) = shelf[map][id].as_str().map(PathBuf::from) else { return out.text(404, "not on this shelf") };
    let kind = match path.extension().and_then(|e| e.to_str()).unwrap_or("").to_ascii_lowercase().as_str() {
        "mp3" => "audio/mpeg",
        "flac" => "audio/flac",
        "wav" => "audio/wav",
        "aac" => "audio/aac",
        "jpg" | "jpeg" | "img" => "image/jpeg",
        "png" => "image/png",
        "webp" => "image/webp",
        _ => "audio/mp4",
    };
    out.file(&path, req.headers.get("range"), kind)
}

// ---- events ----

/// Events are acked once they are safely on disk (inbox); phone.py applies them to drift.db, each once.
fn events(req: &Req, out: &mut Out, dev: &Device) -> std::io::Result<()> {
    let v = body_json(req);
    let dir = root().join(&dev.device);
    let acked_file = dir.join("acked");
    let mut acked: u64 = std::fs::read_to_string(&acked_file).ok().and_then(|s| s.trim().parse().ok()).unwrap_or(0);
    let mut list: Vec<Value> = v["events"].as_array().cloned().unwrap_or_default();
    list.sort_by_key(|e| e["seq"].as_u64().unwrap_or(0));
    let mut fresh = vec![];
    for e in list {
        let seq = e["seq"].as_u64().unwrap_or(0);
        if seq == acked + 1 {
            acked = seq;
            fresh.push(e);
        } else if seq > acked + 1 {
            break; // a gap: the phone sends the rest again
        }
    }
    if !fresh.is_empty() {
        let first = fresh[0]["seq"].as_u64().unwrap_or(0);
        write_json(&dir.join("inbox").join(format!("{:013}-{first:08}.json", now_ms())), &json!({"events": fresh}));
        let _ = std::fs::write(&acked_file, acked.to_string());
        run(&dev.device, "phone-events", dev.size, false);
    }
    out.json(200, &json!({"ack": acked}))
}

// ---- the shelf brain (drift/phone.py through the helper) ----

/// Run one phone.py job for `dev` (one at a time per device). `wait`: until it ends (at most 80 s).
fn run(dev: &str, op: &str, size: u64, wait: bool) {
    {
        let mut d = door().lock().unwrap();
        if d.jobs.contains(dev) {
            if op == "phone-events" {
                d.again.insert(dev.to_string());
                return;
            }
            drop(d);
            if wait {
                let t = Instant::now();
                while door().lock().unwrap().jobs.contains(dev) && t.elapsed() < Duration::from_secs(80) {
                    thread::sleep(Duration::from_millis(200));
                }
            }
            return;
        }
        d.jobs.insert(dev.to_string());
    }
    let cmd = {
        let st = CTX.get().map(|c| c.st.lock().unwrap().helper.clone()).unwrap_or_default();
        if st.is_empty() { default_ui() } else { st }
    };
    let child = if cmd.is_empty() {
        None
    } else {
        Command::new(&cmd[0])
            .args(&cmd[1..])
            .args(["--net", op, "--device", dev, "--size", &size.to_string()])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags_compat()
            .spawn()
            .ok()
    };
    let dev = dev.to_string();
    let finish = move |mut c: Option<Child>| {
        if let Some(c) = c.as_mut() {
            let t = Instant::now();
            loop {
                match c.try_wait() {
                    Ok(Some(_)) | Err(_) => break,
                    Ok(None) if t.elapsed() > Duration::from_secs(1800) => {
                        let _ = c.kill();
                        break;
                    }
                    Ok(None) => thread::sleep(Duration::from_millis(150)),
                }
            }
        }
        let again = {
            let mut d = door().lock().unwrap();
            d.jobs.remove(&dev);
            d.again.remove(&dev)
        };
        if again {
            run(&dev, "phone-events", 0, false);
        }
    };
    if wait {
        let t = Instant::now();
        let mut c = child;
        if let Some(ch) = c.as_mut() {
            // Cloudflare gives up on an answer after 100 s: past 80 s the phone hears "getting ready" and asks again
            while matches!(ch.try_wait(), Ok(None)) && t.elapsed() < Duration::from_secs(80) {
                thread::sleep(Duration::from_millis(100));
            }
        }
        thread::spawn(move || finish(c)); // still going after 3 min: it finishes in the background
    } else {
        thread::spawn(move || finish(child));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base64_round_trip() {
        for n in 0..40 {
            let data: Vec<u8> = (0..n).map(|i| (i * 37 + 11) as u8).collect();
            assert_eq!(unb64(&b64(&data)).unwrap(), data);
        }
        assert_eq!(b64(b"hi?"), "aGk_");
    }

    #[test]
    fn sealed_for_the_phone() {
        let key: Vec<u8> = (0..32).collect();
        let msg = seal(&key, r#"{"url":"https://a.trycloudflare.com","gen":3,"exp":99}"#, &[7u8; 12]).unwrap();
        println!("SEALED {msg}");
        assert!(msg.starts_with("BwcHBwcHBwcHBwcH."));
    }

    #[test]
    fn percent() {
        assert_eq!(unpercent("yt%3Aabc"), "yt:abc");
        assert_eq!(unpercent("a%2"), "a%2");
    }
}
