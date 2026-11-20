//! The tray icon (left click opens Drift, right click: play/pause, next, previous, quit), the media keys and the Pause
//! key (both bring up the player card, card.rs).
use crate::Ctx;
use std::sync::OnceLock;
use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, POINT, WPARAM};
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{RegisterHotKey, MOD_NOREPEAT};
use windows_sys::Win32::UI::Shell::{
    ExtractIconExW, Shell_NotifyIconW, NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE, NIM_MODIFY, NOTIFYICONDATAW,
};
use windows_sys::Win32::UI::WindowsAndMessaging::*;

static CTX: OnceLock<Ctx> = OnceLock::new();
static HWND_CELL: OnceLock<usize> = OnceLock::new();
const WM_TRAY: u32 = WM_APP + 1;
const WM_CHANGED: u32 = WM_APP + 2;
const KEYS: [(i32, u32); 5] = [(1, 0xB3), (2, 0xB0), (3, 0xB1), (4, 0xB2), (5, 0x13)]; // play/pause, next, previous, stop, Pause

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

pub fn ctx() -> &'static Ctx {
    CTX.get().unwrap()
}

fn hwnd() -> HWND {
    HWND_CELL.get().map(|h| *h as HWND).unwrap_or(std::ptr::null_mut())
}

/// The song changed: the tray tooltip follows (posted to the window thread).
pub fn changed() {
    let h = hwnd();
    if !h.is_null() {
        unsafe { PostMessageW(h, WM_CHANGED, 0, 0) };
    }
}

fn notify_data(tip: &str) -> NOTIFYICONDATAW {
    let mut nid: NOTIFYICONDATAW = unsafe { std::mem::zeroed() };
    nid.cbSize = std::mem::size_of::<NOTIFYICONDATAW>() as u32;
    nid.hWnd = hwnd();
    nid.uID = 1;
    nid.uFlags = NIF_MESSAGE | NIF_TIP | NIF_ICON;
    nid.uCallbackMessage = WM_TRAY;
    nid.hIcon = icon();
    for (i, c) in tip.encode_utf16().take(127).enumerate() {
        nid.szTip[i] = c;
    }
    nid
}

fn icon() -> HICON {
    static ICON: OnceLock<usize> = OnceLock::new();
    *ICON.get_or_init(|| {
        let ui = crate::default_ui();
        let mut big: HICON = std::ptr::null_mut();
        let mut small: HICON = std::ptr::null_mut();
        if let Some(path) = ui.first() {
            let w = wide(path);
            unsafe { ExtractIconExW(w.as_ptr(), 0, &mut big, &mut small, 1) };
        }
        if small.is_null() {
            small = unsafe { LoadIconW(std::ptr::null_mut(), IDI_APPLICATION) };
        }
        small as usize
    }) as HICON
}

fn tooltip() -> String {
    let ctx = CTX.get().unwrap();
    let st = ctx.st.lock().unwrap();
    match &st.current {
        Some(c) if !c.artist.is_empty() => format!("Drift · {} — {}", c.title, c.artist),
        Some(c) => format!("Drift · {}", c.title),
        None => "Drift".into(),
    }
}

pub fn remove() {
    let nid = notify_data("");
    unsafe { Shell_NotifyIconW(NIM_DELETE, &nid) };
}

fn menu(h: HWND) {
    let ctx = CTX.get().unwrap();
    let playing = ctx.info.lock().unwrap().playing;
    unsafe {
        let m = CreatePopupMenu();
        let items: [(usize, &str); 6] = [(1, if playing { "Pause" } else { "Play" }), (2, "Next"), (3, "Previous"),
                                          (0, ""), (5, "Open Drift"), (6, "Quit Drift")];
        for (id, text) in items {
            if id == 0 {
                AppendMenuW(m, MF_SEPARATOR, 0, std::ptr::null());
            } else {
                let w = wide(text);
                AppendMenuW(m, MF_STRING, id, w.as_ptr());
            }
        }
        let mut p = POINT { x: 0, y: 0 };
        GetCursorPos(&mut p);
        SetForegroundWindow(h);
        let id = TrackPopupMenu(m, TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON, p.x, p.y, 0, h, std::ptr::null());
        DestroyMenu(m);
        act(id as usize);
    }
}

pub fn act(id: usize) {
    let ctx = CTX.get().unwrap().clone();
    std::thread::spawn(move || match id {
        1 => ctx.toggle(),
        2 => ctx.next(false),
        3 => ctx.previous(),
        4 => {
            let _ = ctx.audio.send(crate::audio::Cmd::Pause);
        }
        5 => crate::open_ui(&ctx),
        6 => crate::quit(&ctx),
        _ => {}
    });
}

unsafe extern "system" fn proc_(h: HWND, msg: u32, w: WPARAM, l: LPARAM) -> LRESULT {
    match msg {
        WM_TRAY => {
            match (l & 0xFFFF) as u32 {
                WM_LBUTTONUP => act(5),
                WM_RBUTTONUP | WM_CONTEXTMENU => menu(h),
                _ => {}
            }
            0
        }
        WM_HOTKEY => {
            match w {
                1..=4 => {
                    act(w);
                    crate::card::show(true); // quiet: not over a full-screen game
                }
                5 => crate::card::pause_key(),
                _ => {}
            }
            0
        }
        WM_CHANGED => {
            let nid = notify_data(&tooltip());
            Shell_NotifyIconW(NIM_MODIFY, &nid);
            crate::card::changed();
            0
        }
        _ => {
            // Explorer restarted: put the icon back
            static RESTART: OnceLock<u32> = OnceLock::new();
            let tb = *RESTART.get_or_init(|| RegisterWindowMessageW(wide("TaskbarCreated").as_ptr()));
            if msg == tb {
                let nid = notify_data(&tooltip());
                Shell_NotifyIconW(NIM_ADD, &nid);
            }
            DefWindowProcW(h, msg, w, l)
        }
    }
}

pub fn run(ctx: Ctx) {
    let _ = CTX.set(ctx);
    unsafe {
        use windows_sys::Win32::UI::HiDpi::{SetProcessDpiAwarenessContext, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2};
        SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2); // a sharp card on scaled screens
        let class = wide("DriftCore");
        let inst = windows_sys::Win32::System::LibraryLoader::GetModuleHandleW(std::ptr::null());
        let wc = WNDCLASSW {
            lpfnWndProc: Some(proc_),
            hInstance: inst,
            lpszClassName: class.as_ptr(),
            ..std::mem::zeroed()
        };
        RegisterClassW(&wc);
        let h = CreateWindowExW(0, class.as_ptr(), wide("Drift core").as_ptr(), 0, 0, 0, 0, 0,
                                std::ptr::null_mut(), std::ptr::null_mut(), inst, std::ptr::null());
        let _ = HWND_CELL.set(h as usize);
        for (id, vk) in KEYS {
            RegisterHotKey(h, id, MOD_NOREPEAT, vk);
        }
        let nid = notify_data(&tooltip());
        Shell_NotifyIconW(NIM_ADD, &nid);
        let mut msg: MSG = std::mem::zeroed();
        while GetMessageW(&mut msg, std::ptr::null_mut(), 0, 0) > 0 {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
}
