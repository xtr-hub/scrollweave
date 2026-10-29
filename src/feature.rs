use crate::image::Image;
#[derive(Clone, Debug)]
pub struct Feature {
    pub width: usize,
    pub height: usize,
    pub gray: Vec<u8>,
    pub edge: Vec<u8>,
    pub rows: Vec<f64>,
    pub cols: Vec<f64>,
}
impl Feature {
    pub fn extract(im: &Image) -> Self {
        let (w, h) = (im.width, im.height);
        let mut gray = vec![0u8; w * h];
        for (i, p) in im.rgb.chunks_exact(3).enumerate() {
            gray[i] = ((77 * p[0] as u32 + 150 * p[1] as u32 + 29 * p[2] as u32) >> 8) as u8
        }
        let mut edge = vec![0u8; w * h];
        let mut rows = vec![0f64; h];
        let mut cols = vec![0f64; w];
        for (y, row) in rows
            .iter_mut()
            .enumerate()
            .take(h.saturating_sub(1))
            .skip(1)
        {
            for (x, col) in cols
                .iter_mut()
                .enumerate()
                .take(w.saturating_sub(1))
                .skip(1)
            {
                let k = y * w + x;
                let gx = (gray[k + 1] as i32 - gray[k - 1] as i32).unsigned_abs() as usize;
                let gy = (gray[k + w] as i32 - gray[k - w] as i32).unsigned_abs() as usize;
                let e = (gx + gy).min(255) as u8;
                edge[k] = e;
                *row += (gy as f64).min(255.0);
                *col += (gx as f64).min(255.0)
            }
        }
        let rw = w.max(1) as f64;
        let rh = h.max(1) as f64;
        for a in &mut rows {
            *a /= rw
        }
        for a in &mut cols {
            *a /= rh
        }
        Self {
            width: w,
            height: h,
            gray,
            edge,
            rows,
            cols,
        }
    }
}
/// Average variation at the SAME screen coordinate, across multiple observations.
/// Naturally discounts stationary chrome while retaining moving document structure.
pub fn screen_activity(frames: &[Feature]) -> Vec<f64> {
    if frames.is_empty() {
        return vec![];
    }
    let (w, h) = (frames[0].width, frames[0].height);
    let mut activity = vec![1f64; h];
    if frames.len() < 3 || frames.iter().any(|f| f.width != w || f.height != h) {
        return activity;
    }
    for (y, value) in activity.iter_mut().enumerate().take(h) {
        let mut diff = 0.0;
        let mut n = 0;
        for x in (4..w.saturating_sub(4)).step_by(8) {
            let p = y * w + x;
            let a = frames[0].gray[p] as i32;
            for f in frames.iter().skip(1).step_by((frames.len() / 12).max(1)) {
                diff += (a - f.gray[p] as i32).unsigned_abs() as f64;
                n += 1
            }
        }
        let mean = if n == 0 { 0.0 } else { diff / n as f64 };
        *value = (mean / 18.0).clamp(0.07, 1.0)
    }
    // A small continuous smoothing kernel avoids treating every pixel-row as unrelated.
    let old = activity.clone();
    for y in 2..h.saturating_sub(2) {
        activity[y] =
            (old[y - 2] + 2.0 * old[y - 1] + 3.0 * old[y] + 2.0 * old[y + 1] + old[y + 2]) / 9.0
    }
    activity
}
