use crate::feature::Feature;
#[derive(Clone, Debug)]
pub struct Match {
    pub dx: i32,
    pub dy: i32,
    pub score: f64,
    pub overlap: f64,
    pub confidence: f64,
}
fn axis_candidates(
    a: &[f64],
    b: &[f64],
    activity: Option<&[f64]>,
    min_overlap: usize,
    k: usize,
) -> Vec<i32> {
    let n = a.len().min(b.len());
    if n == 0 {
        return vec![];
    }
    let max_shift = n.saturating_sub(min_overlap.max(3)) as i32;
    let mut options = Vec::new();
    for d in -max_shift..=max_shift {
        let start = (0i32).max(d) as usize;
        let end = (n as i32).min(n as i32 + d) as usize;
        if end <= start + 2 {
            continue;
        }
        let mut sw = 0f64;
        let mut sa = 0f64;
        let mut sb = 0f64;
        let mut saa = 0f64;
        let mut sbb = 0f64;
        let mut sab = 0f64;
        for y in start..end {
            let yy = (y as i32 - d) as usize;
            let w = activity.map_or(1.0, |v| v[y].max(0.06));
            let aa = a[y];
            let bb = b[yy];
            sw += w;
            sa += w * aa;
            sb += w * bb;
            saa += w * aa * aa;
            sbb += w * bb * bb;
            sab += w * aa * bb
        }
        let den = ((saa - sa * sa / sw) * (sbb - sb * sb / sw))
            .max(0.0)
            .sqrt();
        if den < 1e-5 {
            continue;
        }
        let corr = (sab - sa * sb / sw) / den;
        let overlap = (end - start) as f64 / n as f64;
        let merit = corr - 0.13 * (1.0 - overlap);
        options.push((merit, d));
    }
    options.sort_by(|a, b| b.0.total_cmp(&a.0));
    let mut out: Vec<i32> = Vec::new();
    for (_, d) in options {
        if out.iter().all(|&old| (old - d).abs() > 5) {
            out.push(d);
            if out.len() >= k {
                break;
            }
        }
    }
    if !out.contains(&0) {
        out.push(0)
    }
    out
}
fn dense_score(
    a: &Feature,
    b: &Feature,
    dx: i32,
    dy: i32,
    activity: Option<&[f64]>,
    sample_step: usize,
    min_overlap: f64,
) -> Option<(f64, f64)> {
    let w = a.width as i32;
    let h = a.height as i32;
    let startx = dx.max(0);
    let endx = w.min(w + dx);
    let starty = dy.max(0);
    let endy = h.min(h + dy);
    if startx >= endx || starty >= endy {
        return None;
    }
    let overlap = ((endx - startx) as f64 * (endy - starty) as f64) / (a.width * a.height) as f64;
    if overlap < min_overlap {
        return None;
    }
    let st = sample_step.max(1);
    let (mut sim, mut cnt, mut reliable) = (0f64, 0f64, 0f64);
    for y in (starty..endy).step_by(st) {
        let yy = (y - dy) as usize;
        let y = y as usize;
        let rowweight = activity.map_or(1., |v| v[y].max(0.06));
        for x in (startx..endx).step_by(st) {
            let xx = (x - dx) as usize;
            let x = x as usize;
            let pa = y * a.width + x;
            let pb = yy * b.width + xx;
            let ea = a.edge[pa] as f64;
            let eb = b.edge[pb] as f64;
            let activity_weight = (0.2 + ((ea + eb) / 130.).min(1.0)) * rowweight;
            // absolute differences on gradients are light tolerant. The capped gray residual
            // suppresses giant popup edges rather than allowing them to dominate NCC.
            let ed = (ea - eb).abs();
            let gd = (a.gray[pa] as f64 - b.gray[pb] as f64).abs();
            let cost = 0.72 * (ed / 115.).min(1.0) + 0.28 * (gd / 100.).min(1.0);
            sim += activity_weight * (1. - cost);
            cnt += activity_weight;
            if ed < 17. && gd < 24. {
                reliable += activity_weight
            }
        }
    }
    if cnt < 10. {
        return None;
    }
    let similarity = sim / cnt;
    let inlier = reliable / cnt;
    let score = 0.65 * similarity + 0.35 * inlier - 0.10 * (1. - overlap);
    Some((score, overlap))
}
/// B's top-left coordinate relative to A; same-size, unrotated images.
pub fn match_pair(
    a: &Feature,
    b: &Feature,
    activity: Option<&[f64]>,
    allow_horizontal: bool,
    min_overlap: f64,
) -> Option<Match> {
    if a.width != b.width || a.height != b.height {
        return None;
    }
    let h = a.height;
    let w = a.width;
    let ys = axis_candidates(
        &a.rows,
        &b.rows,
        activity,
        ((h as f64) * min_overlap.max(0.12)).ceil() as usize,
        8,
    );
    let xs = if allow_horizontal {
        axis_candidates(
            &a.cols,
            &b.cols,
            None,
            ((w as f64) * min_overlap.max(0.12)).ceil() as usize,
            5,
        )
    } else {
        vec![0]
    };
    // Candidate generation is 1-D; verify at most a few 2-D hypotheses.
    // Refining every shift at full resolution would be O(pairs * shifts * pixels).
    let mut coarse = Vec::<Match>::new();
    for yy in ys {
        for &xx in &xs {
            if let Some((score, overlap)) = dense_score(a, b, xx, yy, activity, 12, min_overlap) {
                coarse.push(Match {
                    dx: xx,
                    dy: yy,
                    score,
                    overlap,
                    confidence: 0.,
                })
            }
        }
    }
    coarse.sort_by(|a, b| b.score.total_cmp(&a.score));
    if coarse.is_empty() {
        return None;
    }
    let mut best: Option<Match> = None;
    for coarse_match in coarse.iter().take(3) {
        for dy in coarse_match.dy - 4..=coarse_match.dy + 4 {
            for dx in coarse_match.dx - 4..=coarse_match.dx + 4 {
                if !allow_horizontal && dx != 0 {
                    continue;
                }
                if let Some((score, overlap)) = dense_score(a, b, dx, dy, activity, 5, min_overlap)
                {
                    if best.as_ref().map_or(true, |m| score > m.score) {
                        best = Some(Match {
                            dx,
                            dy,
                            score,
                            overlap,
                            confidence: 0.,
                        })
                    }
                }
            }
        }
    }
    let result = best?;
    // Pixel-level integer refinement around best. Output never needs interpolation.
    let mut refined = result.clone();
    for dy in result.dy - 2..=result.dy + 2 {
        for dx in result.dx - 2..=result.dx + 2 {
            if !allow_horizontal && dx != 0 {
                continue;
            }
            if let Some((score, overlap)) = dense_score(a, b, dx, dy, activity, 2, min_overlap) {
                if score > refined.score {
                    refined = Match {
                        dx,
                        dy,
                        score,
                        overlap,
                        confidence: 0.,
                    }
                }
            }
        }
    }
    refined.confidence = ((refined.score - 0.43) / 0.5).clamp(0.0, 1.0) * refined.overlap.sqrt();
    Some(refined)
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::image::Image;
    #[test]
    fn match_translation() {
        let w = 100;
        let h = 160;
        let mut long = Image::solid(w, 260, [0, 0, 0]).unwrap();
        for y in 0..260 {
            for x in 0..w {
                long.set(
                    x,
                    y,
                    [
                        ((x * 17 + y * 9 + x * y % 251) % 256) as u8,
                        ((x * 5 + y * 3) % 256) as u8,
                        ((x * 7 + y * 11) % 256) as u8,
                    ],
                )
            }
        }
        let a = Feature::extract(&long.crop(0, 0, w, h).unwrap());
        let b = Feature::extract(&long.crop(0, 59, w, h).unwrap());
        let m = match_pair(&a, &b, None, false, 0.2).unwrap();
        assert!((m.dy - 59).abs() <= 1, "shift: {:?}", m)
    }
}
