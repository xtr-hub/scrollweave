//! Layered reconstruction: document/screen evidence, adaptive UI
//! appearance, rails, source selection, and uncertainty. Integer-aligned original RGB only.
use crate::evidence::{self, AuditOptions, Evidence};
use crate::geometry::{self, GeometryMode};
use crate::image::{err, Image, SwResult};
use std::collections::BTreeSet;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Segmentation {
    Temporal,
    GraphCut,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ContentRect {
    pub left: usize,
    pub top: usize,
    pub right: usize,
    pub bottom: usize,
}
impl ContentRect {
    pub fn width(self) -> usize {
        self.right - self.left
    }
    pub fn height(self) -> usize {
        self.bottom - self.top
    }
}
#[derive(Clone, Debug)]
pub struct LayeredOptions {
    pub viewport: Option<(usize, usize)>,
    pub content: Option<ContentRect>,
    pub appearance: bool,
    pub appearance_threshold: f32,
    pub texture_strength: f32,
    pub include_transients: bool,
    pub segmentation: Segmentation,
    pub calibrate_prior: bool,
    pub training_indices: Option<Vec<usize>>,
    pub recurrent_mask: Option<Vec<u8>>,
    pub flat_mask: Option<Vec<u8>>,
}
impl Default for LayeredOptions {
    fn default() -> Self {
        Self {
            viewport: None,
            content: None,
            appearance: true,
            appearance_threshold: 0.60,
            texture_strength: 80.,
            include_transients: false,
            segmentation: Segmentation::Temporal,
            calibrate_prior: true,
            training_indices: None,
            recurrent_mask: None,
            flat_mask: None,
        }
    }
}
#[derive(Clone, Debug)]
pub struct LayeredResult {
    pub image: Image,
    pub positions: Vec<i32>,
    pub viewport: (usize, usize),
    pub geometry_mode: GeometryMode,
    pub source_ids: Vec<i32>,
    pub coverage: Image,
    pub confidence: Image,
    pub provenance: Image,
    pub screen_mask: Image,
    pub evidence: Evidence,
    pub estimated_ui: Vec<u8>,
    pub ui_probability: Vec<u8>,
    pub rejected_prior_rows: Vec<u8>,
    pub scroll_offset: i32,
}
fn diff(a: [u8; 3], b: [u8; 3]) -> u8 {
    (0..3).map(|c| a[c].abs_diff(b[c])).max().unwrap_or(0)
}
fn rgb_at(f: &Image, x: usize, y: usize) -> [u8; 3] {
    f.pixel(x, y)
}
fn integral_sum(mask: &[u32], w: usize, h: usize, r: usize) -> Vec<u32> {
    let stride = w + 1;
    let mut prefix = vec![0u64; (w + 1) * (h + 1)];
    for y in 0..h {
        let mut acc = 0u64;
        for x in 0..w {
            acc += mask[y * w + x] as u64;
            prefix[(y + 1) * stride + x + 1] = prefix[y * stride + x + 1] + acc
        }
    }
    let mut result = vec![0u32; w * h];
    for y in 0..h {
        let y0 = y.saturating_sub(r);
        let y1 = (y + r + 1).min(h);
        for x in 0..w {
            let x0 = x.saturating_sub(r);
            let x1 = (x + r + 1).min(w);
            let count = prefix[y1 * stride + x1] + prefix[y0 * stride + x0]
                - prefix[y1 * stride + x0]
                - prefix[y0 * stride + x1];
            result[y * w + x] = count.min(u32::MAX as u64) as u32
        }
    }
    result
}
fn dilate(src: &[u8], w: usize, h: usize, r: usize) -> Vec<u8> {
    let nums: Vec<u32> = src.iter().map(|&a| u32::from(a > 0)).collect();
    integral_sum(&nums, w, h, r)
        .iter()
        .map(|&n| u8::from(n > 0))
        .collect()
}
fn erode(src: &[u8], w: usize, h: usize, r: usize) -> Vec<u8> {
    let nums: Vec<u32> = src.iter().map(|&a| u32::from(a > 0)).collect();
    integral_sum(&nums, w, h, r)
        .iter()
        .enumerate()
        .map(|(k, &n)| {
            let x = k % w;
            let y = k / w;
            let count = (x + r + 1).min(w) - x.saturating_sub(r);
            let rows = (y + r + 1).min(h) - y.saturating_sub(r);
            u8::from(n as usize == count * rows)
        })
        .collect()
}
fn closing(src: &[u8], w: usize, h: usize, r: usize) -> Vec<u8> {
    erode(&dilate(src, w, h, r), w, h, r)
}
fn median_f64(data: &[f64]) -> f64 {
    if data.is_empty() {
        return 0.;
    }
    let mut v = data.to_vec();
    v.sort_by(|a, b| a.total_cmp(b));
    v[v.len() / 2]
}
fn color_hist_bin(c: [u8; 3], bins: usize) -> usize {
    let s = 256 / bins;
    ((c[0] as usize / s) * bins + c[1] as usize / s) * bins + c[2] as usize / s
}
fn appearance_probability(
    images: &[Image],
    suspect: &[u8],
    confirmed: &[u8],
    w: usize,
    h: usize,
    training_indices: Option<&[usize]>,
) -> Vec<u8> {
    let n = images.len();
    let bins = 16usize;
    let m = bins * bins * bins;
    let mut pos = vec![0f64; m];
    let mut neg = vec![0f64; m];
    let mut p = 0;
    let mut q = 0;
    for i in 0..n {
        if training_indices.is_some_and(|v| !v.contains(&i)) {
            continue;
        }
        for k in 0..w * h {
            let label = suspect[i * w * h + k] != 0;
            let witness = confirmed[i * w * h + k] != 0;
            if label == witness {
                continue;
            }
            let c = images[i].pixel(k % w, k / w);
            let id = color_hist_bin(c, bins);
            if label {
                pos[id] += 1.;
                p += 1
            } else {
                neg[id] += 1.;
                q += 1
            }
        }
    }
    if p < 32 || q < 32 {
        return vec![0u8; n * w * h];
    }
    // 3D Gaussian kernel approximation using normalized separable 3-point [1,2,1]
    fn blur3(src: &[f64], b: usize) -> Vec<f64> {
        let mut current = src.to_vec();
        for axis in 0..3 {
            let mut out = vec![0.; src.len()];
            for r in 0..b {
                for g in 0..b {
                    for z in 0..b {
                        let id = (r * b + g) * b + z;
                        let arr = [r, g, z];
                        let mut s = 2. * current[id];
                        let mut d = 2.;
                        for delta in [-1i32, 1] {
                            let p = arr[axis] as i32 + delta;
                            if p >= 0 && p < b as i32 {
                                let mut v = arr;
                                v[axis] = p as usize;
                                s += current[(v[0] * b + v[1]) * b + v[2]];
                                d += 1.
                            }
                        }
                        out[id] = s / d;
                    }
                }
            }
            current = out;
        }
        current
    }
    let pos = blur3(&pos, bins);
    let neg = blur3(&neg, bins);
    let mut post = vec![0f32; m];
    for id in 0..m {
        post[id] = ((pos[id] + 2.) / (pos[id] + neg[id] + 40.)) as f32
    }
    let mut out = vec![0u8; n * w * h];
    for i in 0..n {
        let mut field = vec![0u32; w * h];
        for k in 0..w * h {
            field[k] =
                (post[color_hist_bin(images[i].pixel(k % w, k / w), bins)] * 65535.).round() as u32
        }
        let averages = integral_sum(&field, w, h, 3);
        for k in 0..w * h {
            let x = k % w;
            let y = k / w;
            let cols = (x + 4).min(w) - x.saturating_sub(3);
            let rows = (y + 4).min(h) - y.saturating_sub(3);
            out[i * w * h + k] = ((averages[k] as f64 / (rows * cols) as f64 / 65535. * 255.)
                .round()
                .clamp(0., 255.)) as u8
        }
    }
    out
}
#[derive(Debug)]
struct Layers {
    screen: Vec<u8>,
    dynamic: Vec<u8>,
    rate: Vec<f32>,
    prior: Vec<u8>,
    rejected_rows: Vec<u8>,
    ui_prob: Vec<u8>,
}
fn infer_layers(
    images: &[Image],
    p: &[i32],
    top: usize,
    bottom: usize,
    options: &LayeredOptions,
) -> Layers {
    let (n, h, w) = (images.len(), images[0].height, images[0].width);
    let wh = w * h;
    let mut suspect = vec![0u8; n * wh];
    let mut confirmed = vec![0u8; n * wh];
    let mut tested = vec![0u32; wh];
    let mut bad = vec![0u32; wh];
    let minp = *p.iter().min().unwrap_or(&0);
    let relative: Vec<i32> = p.iter().map(|v| v - minp).collect();
    let doc_h = relative.iter().copied().max().unwrap_or(0).max(0) as usize + bottom - top;
    for docy in 0..doc_h {
        let observations: Vec<(usize, usize)> = relative
            .iter()
            .enumerate()
            .filter_map(|(k, &v)| {
                let sy = docy as i64 - v as i64 + top as i64;
                if sy >= top as i64 && sy < bottom as i64 {
                    Some((k, sy as usize))
                } else {
                    None
                }
            })
            .collect();
        if observations.len() < 2 {
            continue;
        }
        for x in 0..w {
            let mut good = vec![0u8; observations.len()];
            for a in 0..observations.len() {
                for b in a + 1..observations.len() {
                    if relative[observations[a].0] == relative[observations[b].0] {
                        continue;
                    }
                    let ca = rgb_at(&images[observations[a].0], x, observations[a].1);
                    let cb = rgb_at(&images[observations[b].0], x, observations[b].1);
                    if diff(ca, cb) <= 4 {
                        good[a] = good[a].saturating_add(1);
                        good[b] = good[b].saturating_add(1)
                    }
                }
            }
            if !good.iter().any(|v| *v > 0) {
                continue;
            }
            for (a, &(frame, sy)) in observations.iter().enumerate() {
                let loc = frame * wh + sy * w + x;
                tested[sy * w + x] += 1;
                if good[a] > 0 {
                    confirmed[loc] = 1
                } else {
                    suspect[loc] = 1;
                    bad[sy * w + x] += 1
                }
            }
        }
    }
    // Spatial support of a stationary screen-layer UI (not arbitrary differences).
    let bad_smooth = integral_sum(&bad, w, h, 5);
    let test_smooth = integral_sum(&tested, w, h, 5);
    let mut screen = vec![0u8; wh];
    let mut rate = vec![0f32; wh];
    for k in 0..wh {
        rate[k] = if test_smooth[k] > 0 {
            bad_smooth[k] as f32 / test_smooth[k] as f32
        } else {
            0.
        };
        let area = ((k % w + 6).min(w) - (k % w).saturating_sub(5))
            * ((k / w + 6).min(h) - (k / w).saturating_sub(5));
        if test_smooth[k] as f32 / (area as f32) >= 2. && rate[k] >= 0.45 {
            screen[k] = 1
        }
    }
    screen = dilate(&closing(&screen, w, h, 2), w, h, 4);
    if options.segmentation == Segmentation::GraphCut {
        let graph = crate::graphcut::segment(&rate, &tested, &images[0], 3);
        for k in 0..wh {
            screen[k] |= graph[k]
        }
    }
    // Persistent screen-space edges, one frame for each independent scroll position.
    let mut unique = BTreeSet::new();
    let mut indices = Vec::new();
    for (i, &pos) in relative.iter().enumerate() {
        if unique.insert(pos) {
            indices.push(i)
        }
    }
    if indices.len() >= 2 {
        let mut stationary = vec![0u8; wh];
        let mut edge = vec![0u8; wh];
        for y in top..bottom {
            for x in 0..w {
                let k = y * w + x;
                let mut channels = [0u8; 3];
                for (c, ch) in channels.iter_mut().enumerate() {
                    let mut vals: Vec<u8> =
                        indices.iter().map(|&i| images[i].rgb[k * 3 + c]).collect();
                    vals.sort_unstable();
                    *ch = vals[vals.len() / 2]
                }
                let num = indices
                    .iter()
                    .filter(|&&i| diff(images[i].pixel(x, y), channels) <= 4)
                    .count();
                stationary[k] = u8::from(num as f32 / indices.len() as f32 >= 0.82);
                if x > 0 && x + 1 < w {
                    let left = images[indices[0]].pixel(x - 1, y);
                    let right = images[indices[0]].pixel(x + 1, y);
                    if diff(left, right) >= 16 {
                        edge[k] = 1
                    }
                }
            }
        }
        let nearby = dilate(&edge, w, h, 13);
        let mut stable = vec![0u8; wh];
        for k in 0..wh {
            stable[k] = stationary[k] & nearby[k]
        }
        let stable = closing(&stable, w, h, 1);
        for k in 0..wh {
            screen[k] |= stable[k]
        }
    }
    // Dynamic coherent outliers: connected-area filter, false positives can be harmful.
    let mut dynamic = vec![0u8; n * wh];
    for i in 0..n {
        let v = &suspect[i * wh..(i + 1) * wh];
        let v = erode(&closing(v, w, h, 3), w, h, 1);
        let v = dilate(&v, w, h, 1);
        let mut seen = vec![false; wh];
        let mut filtered = vec![0u8; wh];
        for start in 0..wh {
            if v[start] == 0 || seen[start] {
                continue;
            }
            let mut comp = Vec::new();
            let mut stack = vec![start];
            seen[start] = true;
            while let Some(k) = stack.pop() {
                comp.push(k);
                let x = k % w;
                let y = k / w;
                for (nx, ny) in [
                    (x.wrapping_sub(1), y),
                    (x + 1, y),
                    (x, y.wrapping_sub(1)),
                    (x, y + 1),
                ] {
                    if nx < w && ny < h {
                        let idx = ny * w + nx;
                        if v[idx] != 0 && !seen[idx] {
                            seen[idx] = true;
                            stack.push(idx)
                        }
                    }
                }
            }
            if comp.len() >= 32 {
                for k in comp {
                    filtered[k] = 1
                }
            }
        }
        let filtered = dilate(&filtered, w, h, 3);
        dynamic[i * wh..(i + 1) * wh].copy_from_slice(&filtered);
    }
    let ui_prob = if options.appearance {
        appearance_probability(
            images,
            &suspect,
            &confirmed,
            w,
            h,
            options.training_indices.as_deref(),
        )
    } else {
        vec![0u8; n * wh]
    };
    let mut prior = vec![0u8; n * wh];
    if let Some(ref r) = options.recurrent_mask {
        for (k, &v) in r.iter().enumerate() {
            prior[k] |= u8::from(v > 0)
        }
    }
    let mut flat = options
        .flat_mask
        .clone()
        .unwrap_or_else(|| vec![0u8; n * wh]);
    let mut rejected_rows = vec![0u8; h];
    if options.calibrate_prior && options.flat_mask.is_some() {
        let temporal = geometry::temporal_row_variance(images);
        let mut v = temporal[top..bottom].to_vec();
        v.sort_by(|a, b| a.total_cmp(b));
        let q90 = v[(v.len() * 9 / 10).min(v.len() - 1)];
        let margin = ((bottom - top) as f64 * 0.04).round() as usize;
        let margin = margin.clamp(12, 36);
        for y in top..bottom {
            if y >= top + margin && y + margin < bottom {
                continue;
            }
            let broad = (0..n)
                .filter(|&i| {
                    (0..w).filter(|&x| flat[i * wh + y * w + x] > 0).count() as f64 / w as f64
                        >= 0.97
                })
                .count() as f64
                / n as f64
                >= 0.85;
            if !broad || temporal[y] <= 20f64.max(0.02 * q90) {
                continue;
            }
            let recurrence = (0..n)
                .map(|i| {
                    (0..w)
                        .filter(|&x| {
                            options
                                .recurrent_mask
                                .as_ref()
                                .is_some_and(|m| m[i * wh + y * w + x] > 0)
                        })
                        .count()
                })
                .sum::<usize>() as f64
                / (n * w) as f64;
            if recurrence >= 0.65 {
                continue;
            }
            rejected_rows[y] = 1;
            for i in 0..n {
                flat[i * wh + y * w..i * wh + (y + 1) * w].fill(0)
            }
        }
    }
    for (value, &flat_value) in prior.iter_mut().zip(flat.iter()) {
        *value |= u8::from(flat_value > 0)
    }
    let rails = side_rails(images, top, bottom);
    for i in 0..n {
        for (k, &screen_value) in screen.iter().enumerate().take(wh) {
            let id = i * wh + k;
            if screen_value != 0
                || rails[id] != 0
                || (options.include_transients && dynamic[id] != 0)
                || (options.appearance
                    && (ui_prob[id] as f32) >= options.appearance_threshold * 255.)
            {
                prior[id] = 1
            }
        }
    }
    Layers {
        screen,
        dynamic,
        rate,
        prior,
        rejected_rows,
        ui_prob,
    }
}
fn side_rails(images: &[Image], top: usize, bottom: usize) -> Vec<u8> {
    let (n, h, w) = (images.len(), images[0].height, images[0].width);
    let mut masks = vec![0u8; n * h * w];
    if w < 40 || bottom <= top {
        return masks;
    }
    for (i, f) in images.iter().enumerate() {
        let mut strength = vec![0f64; w - 1];
        let mut persistent = vec![0usize; w - 1];
        for y in top..bottom {
            for x in 0..w - 1 {
                let a = f.pixel(x, y);
                let b = f.pixel(x + 1, y);
                let v = ((0..3)
                    .map(|ch| a[ch].abs_diff(b[ch]) as usize)
                    .sum::<usize>() as f64)
                    / 3.;
                strength[x] += v;
                if v > 10. {
                    persistent[x] += 1
                }
            }
        }
        let rows = (bottom - top) as f64;
        for v in &mut strength {
            *v /= rows
        }
        for right in [false, true] {
            let lo = if right {
                ((w as f64 * 0.94) as usize).max(2)
            } else {
                2
            };
            let hi = if right {
                w.saturating_sub(2)
            } else {
                ((w as f64 * 0.06) as usize).max(3)
            };
            let mut best: Option<(f64, usize)> = None;
            for x in lo..hi {
                let peak = strength[x - 1];
                let p = persistent[x - 1] as f64 / rows;
                if p < 0.66 {
                    continue;
                }
                let ref_lo = if right {
                    x.saturating_sub(((w as f64 * 0.04) as usize).max(12))
                } else {
                    (x + 4).min(w - 1)
                };
                let ref_hi = if right {
                    x.saturating_sub(4).max(ref_lo + 1)
                } else {
                    (x + ((w as f64 * 0.04) as usize).max(12)).min(w - 1)
                };
                let local = median_f64(if ref_lo < ref_hi {
                    &strength[ref_lo..ref_hi]
                } else {
                    &strength[..]
                });
                if peak < 15f64.max(1.65 * local) {
                    continue;
                }
                let score = p * peak / (local + 1e-6);
                if best.map_or(true, |v| score > v.0) {
                    best = Some((score, x))
                }
            }
            if let Some((_, x)) = best {
                for y in top..bottom {
                    if right {
                        for xx in x..w {
                            masks[i * h * w + y * w + xx] = 1
                        }
                    } else {
                        for xx in 0..x {
                            masks[i * h * w + y * w + xx] = 1
                        }
                    }
                }
            }
        }
    }
    masks
}
fn texture_maps(images: &[Image]) -> Vec<Vec<u8>> {
    let mut maps = Vec::new();
    for f in images {
        let (w, h) = (f.width, f.height);
        let mut raw = vec![0u32; w * h];
        for y in 1..h.saturating_sub(1) {
            for x in 1..w.saturating_sub(1) {
                let g = |xx, yy| {
                    let c = f.pixel(xx, yy);
                    (c[0] as u32 * 299 + c[1] as u32 * 587 + c[2] as u32 * 114) / 1000
                };
                let dx = g(x + 1, y).abs_diff(g(x - 1, y));
                let dy = g(x, y + 1).abs_diff(g(x, y - 1));
                raw[y * w + x] = (dx + dy).min(255)
            }
        }
        let summed = integral_sum(&raw, w, h, 6);
        let mut out = vec![0u8; w * h];
        for k in 0..w * h {
            let x = k % w;
            let y = k / w;
            let cols = (x + 7).min(w) - x.saturating_sub(6);
            let rows = (y + 7).min(h) - y.saturating_sub(6);
            out[k] = (summed[k] / (cols * rows) as u32).min(255) as u8;
        }
        maps.push(out);
    }
    maps
}
fn crop_mask(mask: &[u8], n: usize, width: usize, height: usize, rect: ContentRect) -> Vec<u8> {
    let out_width = rect.width();
    let out_height = rect.height();
    let mut out = vec![0u8; n * out_width * out_height];
    for frame in 0..n {
        for y in 0..out_height {
            let source = (frame * height + rect.top + y) * width + rect.left;
            let target = (frame * out_height + y) * out_width;
            out[target..target + out_width].copy_from_slice(&mask[source..source + out_width]);
        }
    }
    out
}

fn longest_run(flags: impl Iterator<Item = bool>) -> usize {
    let mut best = 0;
    let mut current = 0;
    for flag in flags {
        if flag {
            current += 1;
            best = best.max(current);
        } else {
            current = 0;
        }
    }
    best
}

/// Find fixed page chrome from persistent full-width/height edges.
///
/// A fixed header usually leaves a full-width horizontal border, and a fixed
/// sidebar a full-height vertical border. Content edges move with scrolling and
/// therefore do not persist at the same screen coordinate.
pub fn infer_content_rect(images: &[Image]) -> Option<ContentRect> {
    if images.len() < 2 {
        return None;
    }
    let width = images[0].width;
    let height = images[0].height;
    if images
        .iter()
        .any(|image| image.width != width || image.height != height)
    {
        return None;
    }
    let mut persistent = vec![true; width * height];
    let mut stable = vec![true; width * height];
    for (frame_index, image) in images.iter().enumerate() {
        let mut edges = vec![false; width * height];
        for y in 1..height.saturating_sub(1) {
            for x in 1..width.saturating_sub(1) {
                let gray = |xx: usize, yy: usize| {
                    let pixel = image.pixel(xx, yy);
                    (pixel[0] as i32 * 77 + pixel[1] as i32 * 150 + pixel[2] as i32 * 29) >> 8
                };
                let gx = (gray(x + 1, y) - gray(x - 1, y)).abs();
                let gy = (gray(x, y + 1) - gray(x, y - 1)).abs();
                edges[y * width + x] = gx + gy > 24;
            }
        }
        for index in 0..persistent.len() {
            persistent[index] &= edges[index];
            if frame_index > 0 {
                stable[index] &= images[0].rgb[index * 3..index * 3 + 3]
                    .iter()
                    .zip(&image.rgb[index * 3..index * 3 + 3])
                    .all(|(a, b)| a.abs_diff(*b) <= 1);
            }
        }
    }
    let mut stable_columns = vec![0usize; width];
    let mut stable_rows = vec![0usize; height];
    for y in 0..height {
        for x in 0..width {
            if stable[y * width + x] {
                stable_columns[x] += 1;
                stable_rows[y] += 1;
            }
        }
    }
    let mut column_prefix = vec![0usize; width + 1];
    let mut row_prefix = vec![0usize; height + 1];
    for x in 0..width {
        column_prefix[x + 1] = column_prefix[x] + stable_columns[x];
    }
    for y in 0..height {
        row_prefix[y + 1] = row_prefix[y] + stable_rows[y];
    }

    let mut top = None;
    for y in 10..height / 3 {
        let run = longest_run((0..width).map(|x| persistent[y * width + x]));
        let stable_above = row_prefix[y] as f64 / (y * width).max(1) as f64;
        if run * 100 >= width * 55 && stable_above >= 0.9 && top.is_none() && y + 2 < height {
            top = Some(y + 2);
        }
    }
    let mut left = None;
    for x in 40..width / 2 {
        let run = longest_run((0..height).map(|y| persistent[y * width + x]));
        let stable_left = column_prefix[x] as f64 / (x * height).max(1) as f64;
        if run * 100 >= height * 55 && stable_left >= 0.75 && x + 1 >= 80 {
            left = Some(x + 1);
        }
    }
    if top.is_none() && left.is_none() {
        None
    } else {
        Some(ContentRect {
            left: left.unwrap_or(0),
            top: top.unwrap_or(0),
            right: width,
            bottom: height,
        })
    }
}

pub fn reconstruct(
    images: &[Image],
    positions: &[i32],
    opts: &LayeredOptions,
) -> SwResult<LayeredResult> {
    if images.is_empty() || images.len() != positions.len() {
        return err("image/position count mismatch");
    }
    let (w, h) = (images[0].width, images[0].height);
    if images.iter().any(|im| im.width != w || im.height != h) {
        return err("layered reconstruction requires identical frame geometry");
    }
    if opts.content.is_none() && opts.viewport.is_none() {
        if let Some(content) = infer_content_rect(images) {
            let mut inner = opts.clone();
            inner.content = Some(content);
            return reconstruct(images, positions, &inner);
        }
    }
    for mask in [&opts.flat_mask, &opts.recurrent_mask]
        .into_iter()
        .flatten()
    {
        if mask.len() != images.len() * w * h {
            return err("prior mask must be N*H*W bytes");
        }
    }
    if let Some(rect) = opts.content {
        if rect.left >= rect.right || rect.top >= rect.bottom || rect.right > w || rect.bottom > h {
            return err("content region is outside frame bounds");
        }
        let cropped = images
            .iter()
            .map(|image| image.crop(rect.left, rect.top, rect.width(), rect.height()))
            .collect::<SwResult<Vec<_>>>()?;
        let adjusted = positions
            .iter()
            .map(|&position| {
                position
                    .checked_add(rect.top as i32)
                    .ok_or_else(|| crate::image::SwError::limit("scroll position overflow"))
            })
            .collect::<SwResult<Vec<_>>>()?;
        let mut inner = opts.clone();
        inner.content = None;
        inner.viewport = Some((0, rect.height()));
        if let Some(mask) = &opts.recurrent_mask {
            inner.recurrent_mask = Some(crop_mask(mask, images.len(), w, h, rect));
        }
        if let Some(mask) = &opts.flat_mask {
            inner.flat_mask = Some(crop_mask(mask, images.len(), w, h, rect));
        }
        return reconstruct(&cropped, &adjusted, &inner);
    }
    let n = images.len();
    let wh = w * h;
    if opts
        .training_indices
        .as_ref()
        .is_some_and(|v| v.is_empty() || v.iter().any(|&x| x >= n))
    {
        return err("training indices must refer to existing frames");
    }
    for mask in [&opts.flat_mask, &opts.recurrent_mask]
        .into_iter()
        .flatten()
    {
        if mask.len() != n * wh {
            return err("prior mask must be N*H*W bytes");
        }
    }
    let (band, mode) = if let Some(b) = opts.viewport {
        (b, GeometryMode::InsufficientMotion)
    } else {
        geometry::infer_viewport(images)
    };
    let (top, bottom) = band;
    if top >= bottom || bottom > h {
        return err("invalid content viewport");
    }
    let origin = *positions.iter().min().unwrap();
    let rel: Vec<i32> = positions.iter().map(|x| *x - origin).collect();
    if rel.iter().any(|&p| !(0..=20_000_000).contains(&p)) {
        return err("invalid relative scroll coordinates");
    }
    let dh = (rel.iter().copied().max().unwrap() as usize)
        .checked_add(bottom - top)
        .ok_or_else(|| crate::image::SwError::limit("page height overflow"))?;
    if dh.saturating_mul(w) > 180_000_000 {
        return err("output canvas too large");
    }
    let layers = infer_layers(images, positions, top, bottom, opts);
    let texture = if opts.texture_strength > 0. {
        Some(texture_maps(images))
    } else {
        None
    };
    let mut image = Image::solid(w, dh, [255, 255, 255])?;
    let mut coverage = Image::solid(w, dh, [0, 0, 0])?;
    let mut conf = Image::solid(w, dh, [0, 0, 0])?;
    let mut prov = Image::solid(w, dh, [0, 0, 0])?;
    let mut sources = vec![-1i32; w * dh];
    let mut obs = Vec::<(usize, usize)>::new();
    let mut rows = Vec::<Vec<u8>>::new();
    for docy in 0..dh {
        obs.clear();
        for (i, &p) in rel.iter().enumerate() {
            let y = docy as i64 - p as i64 + top as i64;
            if y >= top as i64 && y < bottom as i64 {
                obs.push((i, y as usize))
            }
        }
        if obs.is_empty() {
            continue;
        }
        rows.clear();
        for &(i, y) in &obs {
            rows.push(vec![0u8; w]);
            for x in 0..w {
                let pix = images[i].pixel(x, y);
                for &(j, z) in &obs {
                    if i != j && diff(pix, images[j].pixel(x, z)) <= 3 {
                        let row = rows.last_mut().unwrap();
                        row[x] = row[x].saturating_add(1)
                    }
                }
            }
        }
        // Smooth vote evidence horizontally, never smooth output pixels.
        let mut averages = vec![vec![0f32; w]; obs.len()];
        for k in 0..obs.len() {
            let row: Vec<u32> = rows[k].iter().map(|&a| a as u32).collect();
            let mut prefix = vec![0u32; w + 1];
            for x in 0..w {
                prefix[x + 1] = prefix[x] + row[x]
            }
            for (x, average) in averages[k].iter_mut().enumerate().take(w) {
                let a = x.saturating_sub(5);
                let b = (x + 6).min(w);
                *average = (prefix[b] - prefix[a]) as f32 / (b - a) as f32
            }
        }
        for x in 0..w {
            let mut best = 0usize;
            let mut score = f32::NEG_INFINITY;
            for (k, &(i, sy)) in obs.iter().enumerate() {
                let idx = i * wh + sy * w + x;
                let v = rows[k][x] as f32 * 80.
                    + averages[k][x] * 17.
                    + (0.008 * sy.min(h - 1 - sy) as f32);
                let t = texture.as_ref().map_or(0., |maps| {
                    opts.texture_strength * maps[i][sy * w + x] as f32 / 255.
                });
                let penalty = if layers.prior[idx] != 0 { 240. } else { 0. }
                    + if layers.screen[sy * w + x] != 0 {
                        40.
                    } else {
                        0.
                    }
                    + if layers.dynamic[idx] != 0 {
                        if opts.include_transients {
                            280.
                        } else {
                            0.
                        }
                    } else {
                        0.
                    };
                let s = v + t - penalty;
                if s > score {
                    best = k;
                    score = s
                }
            }
            let (i, sy) = obs[best];
            let k = docy * w + x;
            let pix = images[i].pixel(x, sy);
            image.set(x, docy, pix);
            sources[k] = i as i32;
            let count = obs.len();
            let val = (count * 35).min(255) as u8;
            coverage.set(x, docy, [val, val, val]);
            let v = if rows[best][x] >= 2 {
                230
            } else if rows[best][x] == 1 {
                165
            } else if layers.prior[i * wh + sy * w + x] != 0 {
                20
            } else {
                70
            };
            conf.set(x, docy, [v, v, v]);
            let palette = [
                [68, 139, 225],
                [244, 132, 65],
                [73, 194, 132],
                [185, 90, 196],
                [220, 185, 64],
                [84, 197, 204],
                [231, 102, 136],
                [147, 163, 75],
            ];
            prov.set(x, docy, palette[i % palette.len()]);
        }
    }
    let ev = evidence::audit(
        images,
        positions,
        &sources,
        AuditOptions {
            width: w,
            height: dh,
            top,
            bottom,
            tolerance: 3,
        },
    );
    let mut sm = Image::solid(w, h, [0, 0, 0])?;
    for k in 0..wh {
        let v = if layers.screen[k] > 0 {
            255
        } else {
            (layers.rate[k] * 255.).clamp(0., 255.) as u8
        };
        sm.rgb[k * 3..k * 3 + 3].fill(v)
    }
    Ok(LayeredResult {
        image,
        positions: positions.to_vec(),
        viewport: (top, bottom),
        geometry_mode: mode,
        source_ids: sources,
        coverage,
        confidence: conf,
        provenance: prov,
        screen_mask: sm,
        evidence: ev,
        estimated_ui: layers.prior,
        ui_probability: layers.ui_prob,
        rejected_prior_rows: layers.rejected_rows,
        scroll_offset: origin,
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn no_blending_known_positions() {
        let mut doc = Image::solid(27, 100, [255, 255, 255]).unwrap();
        for y in 0..100 {
            for x in 0..27 {
                doc.set(x, y, [(y * 7 + x * 21) as u8, (y + x * 3) as u8, 5])
            }
        }
        let frames = vec![
            doc.crop(0, 0, 27, 60).unwrap(),
            doc.crop(0, 40, 27, 60).unwrap(),
        ];
        let opts = LayeredOptions {
            viewport: Some((0, 60)),
            appearance: false,
            texture_strength: 0.,
            ..Default::default()
        };
        let result = reconstruct(&frames, &[0, 40], &opts).unwrap();
        assert_eq!(result.image, doc);
    }
    #[test]
    fn masks_validate() {
        let a = Image::solid(8, 30, [0, 0, 0]).unwrap();
        let o = LayeredOptions {
            flat_mask: Some(vec![0; 8]),
            viewport: Some((0, 30)),
            ..Default::default()
        };
        assert!(reconstruct(&[a], &[0], &o).is_err());
    }
}
