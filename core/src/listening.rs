use serde::{Deserialize, Serialize};
use std::path::Path;
use std::sync::mpsc;
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Serialize, Deserialize)]
struct Event {
    uid: String,
    at: f64,
    kind: String,
}

// One writer keeps the journal bounded and disk writes off the sound thread.
pub fn writer() -> mpsc::Sender<(String, &'static str)> {
    let (tx, rx) = mpsc::channel::<(String, &'static str)>();
    std::thread::spawn(move || {
        let path = crate::data_dir().join("radio-listening.json");
        let mut events: Vec<Event> = std::fs::read(&path).ok()
            .and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default();
        for (uid, kind) in rx {
            events.push(Event {
                uid, kind: kind.into(),
                at: SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs_f64(),
            });
            events.drain(..events.len().saturating_sub(384));
            let _ = save(&path, &events);
        }
    });
    tx
}

fn save(path: &Path, events: &[Event]) -> std::io::Result<()> {
    let temp = path.with_extension("tmp");
    std::fs::write(&temp, serde_json::to_vec(events)?)?;
    std::fs::rename(temp, path)
}

#[derive(Default)]
pub struct Meter {
    pub heard: u64,
    pub armed: bool,
    last_pos: u64,
}

impl Meter {
    pub fn reset(&mut self) {
        *self = Self { armed: true, ..Self::default() };
    }

    pub fn baseline(&mut self, pos: u64) {
        self.last_pos = pos;
    }

    pub fn sample(&mut self, pos: u64, elapsed: u64, duration: u64) -> bool {
        let delta = pos.saturating_sub(self.last_pos);
        self.last_pos = pos;
        // A sink seek can become visible on a later audio callback.
        if delta <= elapsed.saturating_add(100) {
            self.heard += delta;
        }
        let threshold = if duration == 0 { 90_000 } else { (duration * 55 / 100).clamp(12_000, 90_000) };
        if self.armed && self.heard >= threshold {
            self.armed = false;
            return true;
        }
        false
    }

    pub fn leave(&mut self, duration: u64) -> bool {
        std::mem::take(&mut self.armed) && (1_500..=25_000).contains(&self.heard)
            && (duration == 0 || self.heard * 10 < duration * 3)
    }
}

#[cfg(test)]
mod tests {
    use super::Meter;

    #[test]
    fn atomic_snapshot_replaces_existing_file() {
        let path = std::env::temp_dir().join(format!("drift-listening-test-{}.json", crate::token()));
        super::save(&path, &[]).unwrap();
        let event = super::Event { uid: "example".into(), at: 1.0, kind: "skip".into() };
        super::save(&path, &[event]).unwrap();
        let events: Vec<super::Event> = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        assert_eq!(events[0].uid, "example");
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn seeks_skips_and_replays() {
        let mut m = Meter::default();
        m.reset();
        assert!(!m.sample(2_000, 2_000, 100_000));
        assert!(!m.sample(65_000, 50, 100_000));
        assert!(m.leave(100_000));
        assert!(!m.leave(100_000));
        m.reset();
        assert!(m.sample(55_000, 55_000, 100_000));
        assert!(!m.sample(60_000, 5_000, 100_000));
        assert!(!m.leave(100_000));
    }
}
