//! Viewport geometry: stable screen borders, activity envelope, fallback.
use crate::image::Image;
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GeometryMode {
    StableBorders,
    ActivityEnvelope,
    InsufficientMotion,
}
fn median(v: &mut [f64]) -> f64 {
    if v.is_empty() {
        return 0.0;
    }
    v.sort_by(|a, b| a.total_cmp(b));
    v[v.len() / 2]
}
fn percentile(data: &[f64], p: f64) -> f64 {
    let mut v = data.to_vec();
    if v.is_empty() {
        return 0.0;
    }
    v.sort_by(|a, b| a.total_cmp(b));
    v[((v.len() - 1) as f64 * p).round() as usize]
}
/// Variance across decoded input frames, averaged across width and RGB channels.
pub fn temporal_row_variance(images: &[Image]) -> Vec<f64> {
    let (n, h, w) = (images.len(), images[0].height, images[0].width);
    let mut temporal = vec![0.0; h];
    for (y, value) in temporal.iter_mut().enumerate().take(h) {
        let mut acc = 0f64;
        for x in 0..w {
            let k = (y * w + x) * 3;
            for ch in 0..3 {
                let mut sum = 0.;
                let mut sq = 0.;
                for f in images {
                    let v = f.rgb[k + ch] as f64;
                    sum += v;
                    sq += v * v
                }
                acc += (sq / n as f64 - (sum / n as f64).powi(2)).max(0.);
            }
        }
        *value = acc / (w * 3) as f64;
    }
    temporal
}
fn stable_borders(images: &[Image], temporal: &[f64]) -> Option<(usize, usize)> {
    let (n, h, w) = (images.len(), images[0].height, images[0].width);
    if n < 2 || w < 48 || h < 48 {
        return None;
    }
    let mut row_edge = vec![0.0; h];
    for (y, edge) in row_edge.iter_mut().enumerate().take(h).skip(1) {
        let mut frame_medians = Vec::with_capacity(n);
        for f in images {
            let mut columns = Vec::with_capacity(w);
            for x in 0..w {
                let a = (y * w + x) * 3;
                let b = ((y - 1) * w + x) * 3;
                let d = (0..3)
                    .map(|c| (f.rgb[a + c] as i32 - f.rgb[b + c] as i32).abs() as f64)
                    .sum::<f64>()
                    / 3.;
                columns.push(d)
            }
            frame_medians.push(median(&mut columns))
        }
        *edge = median(&mut frame_medians);
    }
    let q = ((h as f64 * 0.015).round() as usize).max(5);
    let mut tops = Vec::new();
    let mut bottoms = Vec::new();
    for b in q..h.saturating_sub(q) {
        if row_edge[b] < 34. {
            continue;
        }
        let mut outer = temporal[b.saturating_sub(3 * q)..b].to_vec();
        if median(&mut outer) < 8. {
            tops.push(b)
        }
        let mut outer = temporal[b..(b + 3 * q).min(h)].to_vec();
        if median(&mut outer) < 8. {
            bottoms.push(b)
        }
    }
    let mut best: Option<(f64, usize, usize)> = None;
    for &top in &tops {
        for &bottom in &bottoms {
            if bottom <= top || bottom - top < (h as f64 * 0.35) as usize {
                continue;
            }
            let moving = temporal[top..bottom].iter().filter(|&&a| a > 30.).count() as f64
                / (bottom - top) as f64;
            if moving < 0.06 {
                continue;
            }
            let score = (bottom - top) as f64 * (moving / 0.2).min(1.0);
            if best.map_or(true, |b| score > b.0) {
                best = Some((score, top, bottom))
            }
        }
    }
    best.map(|(_, mut top, mut bottom)| {
        let k = 4.min(bottom - top);
        if let Some(off) = (0..k).find(|&j| temporal[top + j] > 10.) {
            top += off
        }
        let start = bottom.saturating_sub(k).max(top);
        if let Some(off) = (start..bottom).rev().find(|&j| temporal[j] > 10.) {
            bottom = off + 1
        }
        (top, bottom)
    })
}
fn envelope(temporal: &[f64]) -> Option<(usize, usize)> {
    let h = temporal.len();
    if h == 0 {
        return None;
    }
    let q80 = percentile(temporal, 0.8);
    let threshold = 10f64.max(0.005 * q80);
    let active: Vec<bool> = temporal.iter().map(|&a| a > threshold).collect();
    let size = (h as f64 * 0.012) as usize | 1;
    let size = size.max(9);
    let r = size / 2;
    let mut first = None;
    let mut last = 0;
    for y in 0..h {
        let a = y.saturating_sub(r);
        let b = (y + r + 1).min(h);
        let count = active[a..b].iter().filter(|v| **v).count();
        if count as f64 >= (size as f64 * 0.28).max(3.) {
            if first.is_none() {
                first = Some(y)
            }
            last = y
        }
    }
    let a = first?.saturating_sub(size);
    let b = (last + size + 1).min(h);
    let start = (a..b).find(|&y| active[y])?;
    let end = (start..b).rev().find(|&y| active[y])? + 1;
    Some((start, end))
}
pub fn infer_viewport(images: &[Image]) -> ((usize, usize), GeometryMode) {
    if images.is_empty() {
        return ((0, 0), GeometryMode::InsufficientMotion);
    }
    let h = images[0].height;
    if images.len() < 2
        || images
            .iter()
            .any(|f| f.height != h || f.width != images[0].width)
    {
        return ((0, h), GeometryMode::InsufficientMotion);
    }
    let temporal = temporal_row_variance(images);
    if let Some(band) = stable_borders(images, &temporal) {
        return (band, GeometryMode::StableBorders);
    }
    if let Some(band) = envelope(&temporal) {
        return (band, GeometryMode::ActivityEnvelope);
    }
    ((0, h), GeometryMode::InsufficientMotion)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn blank_no_crash() {
        let a = Image::solid(32, 96, [255, 255, 255]).unwrap();
        assert_eq!(infer_viewport(&[a.clone(), a]).0, (0, 96));
    }
    #[test]
    fn envelope_includes_blank_gaps() {
        let mut ims = vec![Image::solid(60, 130, [3, 3, 3]).unwrap(); 3];
        for (k, im) in ims.iter_mut().enumerate() {
            for y in 18..110 {
                if y > 43 && y < 77 {
                    continue;
                }
                for x in 0..60 {
                    im.set(x, y, [((y * 17 + k * 61) % 256) as u8, 9, 10])
                }
            }
        }
        let (band, _) = infer_viewport(&ims);
        assert!(band.0 <= 20 && band.1 >= 108, "{:?}", band);
    }
}
