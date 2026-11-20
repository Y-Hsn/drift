/// ITU-R BS.1770 Annex 2, four 12-tap polyphase branches of the 4x
/// interpolation filter. Coefficients are exactly representable as integers / 8192;
/// the third and fourth branches are the reversals of the second and first.
const A: [f32; 12] = [14., 90., -161., 272., -487., 1125., 7964., -838., 390., -218., 122., -68.];
const B: [f32; 12] = [-239., 240., -424., 730., -1364., 3810., 6388., -1641., 832., -477., 271., -155.];

#[derive(Default)]
pub struct PeakMeter {
    history: [f32; 12],
    peak: f32,
}

impl PeakMeter {
    /// Peak around the delayed reconstruction point (5.875 input frames), also
    /// including the current original sample. Values are linear full-scale units.
    #[inline]
    pub fn push(&mut self, sample: f32) -> f32 {
        self.history.copy_within(1.., 0);
        let x = if sample.is_finite() { sample } else { 0.0 };
        self.history[11] = x;
        let mut branches = [0.0f32; 4];
        for i in 0..12 {
            let s = self.history[i];
            branches[0] += s * A[i];
            branches[1] += s * B[i];
            branches[2] += s * B[11 - i];
            branches[3] += s * A[11 - i];
        }
        let peak = branches.into_iter().fold(x.abs(), |m, p| m.max(p.abs() / 8192.0));
        self.peak = self.peak.max(peak);
        peak
    }

    pub fn finish(mut self) -> f32 {
        for _ in 1..12 {
            self.push(0.0);
        }
        self.peak
    }
}

#[cfg(test)]
mod tests {
    use super::PeakMeter;

    #[test]
    fn quarter_rate_tones_reconstruct_the_analytical_peak() {
        use std::f64::consts::PI;
        for phase in [0.0, PI / 8.0, PI / 4.0] {
            let mut meter = PeakMeter::default();
            let mut sample_peak = 0.0f32;
            for i in 0..2048 {
                let taper = (i.min(2047-i) as f64 / 64.0).min(1.0);
                let x = (taper * (i as f64 * PI / 2.0 + phase).sin()) as f32;
                sample_peak = sample_peak.max(x.abs());
                meter.push(x);
            }
            let peak = meter.finish();
            assert!((0.95..=1.05).contains(&peak), "phase {phase}, peak {peak}");
            if phase == PI / 4.0 {
                assert!(peak > sample_peak * 1.33);
            }
        }
    }

    #[test]
    fn tail_is_measured_and_nonfinite_input_is_silent() {
        let mut meter = PeakMeter::default();
        assert_eq!(meter.push(f32::NAN), 0.0);
        assert_eq!(meter.push(f32::INFINITY), 0.0);
        meter.push(1.0);
        meter.push(1.0);
        assert!(meter.finish() > 1.1);
    }
}
