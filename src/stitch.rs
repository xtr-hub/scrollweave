//! Basic 2D document reconstruction. No fabricated pixels: original RGB sources
//! are copied into output at integer coordinates.
use crate::align::{match_pair, Match};
use crate::codec::write_image;
use crate::feature::{screen_activity, Feature};
use crate::graph::{self, Edge};
use crate::image::{err, Image, SwResult};
use crate::visibility::{self, Visibility};
use std::path::Path;

#[derive(Clone, Debug)]
pub struct BasicOptions {
    /// If supplied, exact per-frame document origins; skips geometric estimation.
    pub known_positions: Option<Vec<(i32, i32)>>,
    /// Top and exclusive bottom of content in source viewport; none means automatic conservative trim.
    pub viewport: Option<(usize, usize)>,
    pub min_overlap: f64,
    pub min_match_score: f64,
    pub auto_trim: bool,
    pub diagnostics: bool,
}
impl Default for BasicOptions {
    fn default() -> Self {
        Self {
            known_positions: None,
            viewport: None,
            min_overlap: 0.18,
            min_match_score: 0.54,
            auto_trim: true,
            diagnostics: true,
        }
    }
}
#[derive(Clone, Debug)]
pub struct StitchDiagnostics {
    pub coverage: Image,
    pub confidence: Image,
    pub provenance: Image,
    pub screen_mask: Image,
    pub independent_support_ratio: f64,
    pub unknown_ratio: f64,
}
#[derive(Clone, Debug)]
pub struct StitchResult {
    pub image: Image,
    pub positions: Vec<Option<(i32, i32)>>,
    pub viewport: (usize, usize),
    pub graph_edges: usize,
    pub graph_rejected: usize,
    pub graph_residual: f64,
    pub warnings: Vec<String>,
    pub diagnostics: Option<StitchDiagnostics>,
}
impl StitchResult {
    pub fn save(&self, path: impl AsRef<Path>) -> SwResult<()> {
        write_image(path, &self.image)
    }
    pub fn save_debug(&self, directory: impl AsRef<Path>) -> SwResult<()> {
        std::fs::create_dir_all(&directory)?;
        if let Some(ref d) = self.diagnostics {
            let p = directory.as_ref();
            write_image(p.join("coverage.png"), &d.coverage)?;
            write_image(p.join("confidence.png"), &d.confidence)?;
            write_image(p.join("provenance.png"), &d.provenance)?;
            write_image(p.join("screen_mask.png"), &d.screen_mask)?
        }
        Ok(())
    }
}
/// Run the basic 2D pipeline with a fixed robust matching mode.
pub fn stitch_basic(images: &[Image], options: &BasicOptions) -> SwResult<StitchResult> {
    reconstruct_basic(images, options)
}
fn automatic_viewport(images: &[Image], feats: &[Feature], auto_trim: bool) -> (usize, usize) {
    let h = images[0].height;
    if !auto_trim || images.len() < 3 {
        return (0, h);
    }
    let a = screen_activity(feats);
    let minrun = (h / 50).max(8);
    let maxtrim = (h / 3).max(15);
    let active = |y: usize| a[y] >= 0.26;
    let mut top = 0;
    let mut candidate = 0;
    for y in 0..maxtrim.min(h) {
        if active(y) {
            candidate += 1;
            if candidate >= minrun {
                top = y + 1 - minrun;
                break;
            }
        } else {
            candidate = 0
        }
    }
    let mut bottom = h;
    candidate = 0;
    for y in (h - maxtrim.min(h)..h).rev() {
        if active(y) {
            candidate += 1;
            if candidate >= minrun {
                bottom = y + minrun;
                break;
            }
        } else {
            candidate = 0
        }
    }
    // Do not trim any border unless it provides substantial stationary evidence.
    if top < minrun {
        top = 0
    }
    if h - bottom < minrun {
        bottom = h
    }
    if bottom <= top + (h / 4) {
        (0, h)
    } else {
        (top, bottom)
    }
}
fn palette(i: usize) -> [u8; 3] {
    let colors = [
        [68, 139, 225],
        [244, 132, 65],
        [73, 194, 132],
        [185, 90, 196],
        [220, 185, 64],
        [84, 197, 204],
        [231, 102, 136],
        [147, 163, 75],
    ];
    colors[i % colors.len()]
}
fn difference(a: [u8; 3], b: [u8; 3]) -> i32 {
    (a[0] as i32 - b[0] as i32)
        .abs()
        .max((a[1] as i32 - b[1] as i32).abs())
        .max((a[2] as i32 - b[2] as i32).abs())
}
#[derive(Clone, Copy)]
struct Candidate {
    frame: usize,
    src_idx: usize,
    sx: usize,
    sy: usize,
    color: [u8; 3],
}
fn source_selection(
    images: &[Image],
    poses: &[Option<(i32, i32)>],
    keep: &[bool],
    viewport: (usize, usize),
    ui: &Visibility,
    include_debug: bool,
) -> SwResult<(Image, Option<StitchDiagnostics>, f64)> {
    let (top, bottom) = viewport;
    let mut left = i32::MAX;
    let mut upper = i32::MAX;
    let mut right = i32::MIN;
    let mut lower = i32::MIN;
    for (i, p) in poses.iter().enumerate() {
        if !keep[i] {
            continue;
        }
        if let Some((x, y)) = p {
            left = left.min(*x);
            upper = upper.min(*y + top as i32);
            right = right.max(*x + images[i].width as i32);
            lower = lower.max(*y + bottom as i32)
        }
    }
    if left >= right || upper >= lower {
        return err("no connected content geometry");
    }
    let width = (right - left) as usize;
    let height = (lower - upper) as usize;
    let mut output = Image::solid(width, height, [255, 255, 255])?;
    let mut coverage = if include_debug {
        Some(Image::solid(width, height, [0, 0, 0])?)
    } else {
        None
    };
    let mut conf = if include_debug {
        Some(Image::solid(width, height, [0, 0, 0])?)
    } else {
        None
    };
    let mut provenance = if include_debug {
        Some(Image::solid(width, height, [0, 0, 0])?)
    } else {
        None
    };
    let mut verified = 0usize;
    let mut unknown = 0usize;
    let mut covered = 0usize;
    let mut samples = Vec::<Candidate>::new();
    for y in 0..height {
        for x in 0..width {
            let gx = x as i32 + left;
            let gy = y as i32 + upper;
            samples.clear();
            for (i, pose) in poses.iter().enumerate() {
                if !keep[i] {
                    continue;
                }
                if let Some((px, py)) = pose {
                    let sx = gx - *px;
                    let sy = gy - *py;
                    if sx >= 0
                        && sx < images[i].width as i32
                        && sy >= top as i32
                        && sy < bottom as i32
                    {
                        let sx = sx as usize;
                        let sy = sy as usize;
                        let src_idx = sy * images[i].width + sx;
                        samples.push(Candidate {
                            frame: i,
                            src_idx,
                            sx,
                            sy,
                            color: images[i].pixel(sx, sy),
                        })
                    }
                }
            }
            if samples.is_empty() {
                unknown += 1;
                continue;
            }
            covered += 1;
            let mut best = 0usize;
            let mut best_score = f64::NEG_INFINITY;
            let mut best_support = 0usize;
            for (k, c) in samples.iter().enumerate() {
                let mut matches = 0usize;
                let mut distinct = 0usize;
                let mut distance = 0f64;
                let mut count = 0usize;
                for other in samples.iter() {
                    if c.frame == other.frame {
                        continue;
                    }
                    let dd = (c.sx as i32 - other.sx as i32).abs()
                        + (c.sy as i32 - other.sy as i32).abs();
                    if dd >= 8 {
                        distinct += 1;
                        if difference(c.color, other.color) <= 11 {
                            matches += 1
                        }
                    }
                    let d = difference(c.color, other.color) as f64;
                    distance += d.min(65.);
                    count += 1
                }
                let mask = ui.masks[c.frame][c.src_idx] != 0;
                let margin =
                    c.sx.min(images[c.frame].width - 1 - c.sx)
                        .min(c.sy - top)
                        .min(bottom - 1 - c.sy) as f64;
                let score = matches as f64 * 22. + (if distinct >= 2 { 5. } else { 0. })
                    - distance / (count.max(1) as f64) * 0.35
                    + (margin / 45.).min(3.)
                    - (if mask { 48. } else { 0. });
                if score > best_score {
                    best = k;
                    best_score = score;
                    best_support = matches
                }
            }
            let c = samples[best];
            let invalid = ui.masks[c.frame][c.src_idx] != 0;
            // We never manufacture pixels. If every source is likely UI, expose uncertainty.
            output.set(x, y, c.color);
            if best_support > 0 {
                verified += 1
            } else {
                unknown += 1
            }
            if let Some(ref mut cov) = coverage {
                let val = (samples.len() * 35).min(255) as u8;
                cov.set(x, y, [val, val, val]);
            }
            if let Some(ref mut cf) = conf {
                let val = if best_support >= 2 {
                    230
                } else if best_support == 1 {
                    165
                } else if invalid {
                    20
                } else {
                    70
                };
                cf.set(x, y, [val, val, val]);
            }
            if let Some(ref mut pr) = provenance {
                pr.set(x, y, palette(c.frame));
            }
        }
    }
    let ratio = verified as f64 / (width * height).max(1) as f64;
    let missing = (width * height - covered) as f64 / (width * height).max(1) as f64;
    let debug = if include_debug {
        let mut mask = Image::solid(images[0].width, images[0].height, [0, 0, 0])?;
        for (i, m) in ui.masks.iter().enumerate() {
            if !keep[i] {
                continue;
            }
            for (k, &v) in m.iter().enumerate() {
                if v > 0 {
                    mask.rgb[3 * k] = mask.rgb[3 * k].saturating_add(85);
                    mask.rgb[3 * k + 1] = 20
                }
            }
        }
        Some(StitchDiagnostics {
            coverage: coverage.unwrap(),
            confidence: conf.unwrap(),
            provenance: provenance.unwrap(),
            screen_mask: mask,
            independent_support_ratio: ratio,
            unknown_ratio: unknown as f64 / (width * height).max(1) as f64,
        })
    } else {
        None
    };
    Ok((output, debug, missing))
}
fn reconstruct_basic(images: &[Image], opts: &BasicOptions) -> SwResult<StitchResult> {
    if images.is_empty() {
        return err("no images");
    }
    let w = images[0].width;
    let h = images[0].height;
    if images.iter().any(|i| i.width != w || i.height != h) {
        return err(
            "stitch currently requires identical source dimensions; use compose for mixed sizes",
        );
    }
    if images.len() > 128 {
        return err("limit of 128 input frames; batch or subsample before reconstruction");
    }
    if let Some(p) = &opts.known_positions {
        if p.len() != images.len() {
            return err("known_positions must match input length");
        }
    }
    let feats = images.iter().map(Feature::extract).collect::<Vec<_>>();
    let activity = screen_activity(&feats);
    let viewport = opts
        .viewport
        .unwrap_or_else(|| automatic_viewport(images, &feats, opts.auto_trim));
    if viewport.1 > h || viewport.0 >= viewport.1 {
        return err("invalid viewport");
    }
    let mut warnings = Vec::new();
    let mut edges = Vec::<Edge>::new();
    let mut rejected = 0usize;
    let mut graph_residual = 0f64;
    let mut poses = vec![None; images.len()];
    let mut keep = vec![true; images.len()];
    if let Some(p) = &opts.known_positions {
        for (i, &xy) in p.iter().enumerate() {
            poses[i] = Some(xy)
        }
    } else {
        let count = images.len();
        let pairs = (0..count.saturating_sub(1))
            .map(|i| (i, i + 1))
            .chain((0..count.saturating_sub(2)).map(|i| (i, i + 2)))
            .collect::<Vec<_>>();
        let allow_horizontal = false;
        for (a, b) in pairs {
            let m: Option<Match> = match_pair(
                &feats[a],
                &feats[b],
                Some(&activity),
                allow_horizontal,
                opts.min_overlap,
            );
            if let Some(m) = m {
                if m.score >= opts.min_match_score && m.confidence >= 0.10 {
                    edges.push(Edge::new(a, b, &m))
                }
            }
        }
        if edges.is_empty() {
            if count == 1 {
                poses[0] = Some((0, 0))
            } else {
                return err("no reliable overlap matches; no forced guessing");
            }
        } else {
            let solution = graph::solve(count, edges.clone());
            rejected = solution.rejected.len();
            graph_residual = solution.residual;
            let mut labels = vec![usize::MAX; count];
            let mut adj = vec![Vec::new(); count];
            for e in &solution.accepted {
                adj[e.a].push(e.b);
                adj[e.b].push(e.a)
            }
            let mut sizes = Vec::new();
            let mut comp = 0;
            for i in 0..count {
                if labels[i] != usize::MAX {
                    continue;
                }
                labels[i] = comp;
                let mut stack = vec![i];
                let mut size = 0;
                while let Some(a) = stack.pop() {
                    size += 1;
                    for &b in &adj[a] {
                        if labels[b] == usize::MAX {
                            labels[b] = comp;
                            stack.push(b)
                        }
                    }
                }
                sizes.push(size);
                comp += 1
            }
            let largest = sizes
                .iter()
                .enumerate()
                .max_by_key(|(_, v)| *v)
                .map(|(i, _)| i)
                .unwrap_or(0);
            for i in 0..count {
                keep[i] = labels[i] == largest;
                if keep[i] {
                    poses[i] = solution.positions[i]
                }
            }
            if sizes.len() > 1 {
                warnings.push(format!(
                    "{} disconnected components; only largest component ({}/{}) was rendered",
                    sizes.len(),
                    sizes[largest],
                    count
                ))
            }
            if rejected > 0 {
                warnings.push(format!("{} inconsistent geometry edges excluded", rejected))
            }
        }
    }
    let valid_feats = feats
        .iter()
        .enumerate()
        .filter(|(i, _)| keep[*i])
        .map(|(_, f)| f.clone())
        .collect::<Vec<_>>();
    let valid_pos = poses
        .iter()
        .enumerate()
        .filter(|(i, _)| keep[*i])
        .map(|(_, p)| p.unwrap_or((0, 0)))
        .collect::<Vec<_>>();
    let vis_subset = visibility::estimate(&valid_feats, &valid_pos);
    let mut masks = vec![vec![0u8; w * h]; images.len()];
    let mut at = 0;
    for i in 0..images.len() {
        if keep[i] {
            masks[i] = vis_subset.masks[at].clone();
            at += 1
        }
    }
    // Crop rows outside viewport are always screen chrome (when known / detected).
    for m in &mut masks {
        for y in 0..viewport.0 {
            m[y * w..(y + 1) * w].fill(255)
        }
        for y in viewport.1..h {
            m[y * w..(y + 1) * w].fill(255)
        }
    }
    let vis = Visibility {
        masks,
        screen_votes: vis_subset.screen_votes,
    };
    let (image, diagnostics, missing) =
        source_selection(images, &poses, &keep, viewport, &vis, opts.diagnostics)?;
    if missing > 0. {
        warnings.push(format!(
            "unobserved area: {:.2}% (white, not synthesized)",
            missing * 100.
        ))
    }
    if let Some(ref d) = diagnostics {
        if d.independent_support_ratio < 0.5 {
            warnings.push("less than half the output has independent pixel agreement; check UI and missing observations".into())
        }
    }
    Ok(StitchResult {
        image,
        positions: poses,
        viewport,
        graph_edges: edges.len(),
        graph_rejected: rejected,
        graph_residual,
        warnings,
        diagnostics,
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn known_position_recover() {
        let mut doc = Image::solid(35, 180, [255, 255, 255]).unwrap();
        for y in 0..180 {
            for x in 0..35 {
                doc.set(
                    x,
                    y,
                    [
                        ((y * 13 + x * 37) % 256) as u8,
                        ((x * 11 + y * 19) % 256) as u8,
                        ((x * y + 123) % 256) as u8,
                    ],
                )
            }
        }
        let images = vec![
            doc.crop(0, 0, 35, 90).unwrap(),
            doc.crop(0, 40, 35, 90).unwrap(),
            doc.crop(0, 90, 35, 90).unwrap(),
        ];
        let options = BasicOptions {
            known_positions: Some(vec![(0, 0), (0, 40), (0, 90)]),
            auto_trim: false,
            ..BasicOptions::default()
        };
        let res = stitch_basic(&images, &options).unwrap();
        assert_eq!(res.image, doc);
    }
}
