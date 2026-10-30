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
fn downsample_edge(feature: &Feature, factor: usize) -> (usize, usize, Vec<f32>) {
    let width = (feature.width / factor).max(1);
    let height = (feature.height / factor).max(1);
    let mut out = vec![0f32; width * height];
    for y in 0..height {
        for x in 0..width {
            let mut sum = 0u64;
            let mut count = 0usize;
            for sy in y * factor..(y * factor + factor).min(feature.height) {
                for sx in x * factor..(x * factor + factor).min(feature.width) {
                    sum += feature.edge[sy * feature.width + sx] as u64;
                    count += 1;
                }
            }
            out[y * width + x] = sum as f32 / count.max(1) as f32;
        }
    }
    (width, height, out)
}

/// Generate offsets from masked multi-band 2-D gradient NCC.
///
/// Row projections are fast but can alias on repeated layouts. This coarse stage
/// compares several horizontal bands independently and keeps offsets supported by
/// more than one band. The full-resolution dense score still decides the winner.
fn coarse_ncc_candidates(
    a: &Feature,
    b: &Feature,
    activity: Option<&[f64]>,
    allow_horizontal: bool,
    min_overlap: f64,
) -> Vec<(i32, i32)> {
    if a.width != b.width || a.height != b.height {
        return vec![];
    }
    let factor = 4usize;
    if a.width < factor * 8 || a.height < factor * 8 {
        return vec![];
    }
    let (aw, ah, ea) = downsample_edge(a, factor);
    let (bw, bh, eb) = downsample_edge(b, factor);
    if aw != bw || ah != bh {
        return vec![];
    }
    let max_dy = ah as i32 - 1;
    let max_dx = if allow_horizontal { 3 } else { 0 };
    let bands = [
        (0.12 * aw as f64, 0.48 * aw as f64),
        (0.32 * aw as f64, 0.68 * aw as f64),
        (0.55 * aw as f64, 0.92 * aw as f64),
    ];
    let mut votes = Vec::<(i32, i32, f64, usize)>::new();
    for (band, (x0, x1)) in bands.into_iter().enumerate() {
        let x0 = x0 as i32;
        let x1 = x1 as i32;
        let mut best = Vec::<(f64, i32, i32)>::new();
        for dy in -max_dy..=max_dy {
            let start = 0i32.max(dy);
            let end = (ah as i32).min(ah as i32 + dy);
            if end <= start {
                continue;
            }
            let overlap = (end - start) as f64 / ah as f64;
            if overlap < min_overlap {
                continue;
            }
            // Ignore the top part of the overlap: that is usually fixed chrome.
            let skip = ((end - start) as f64 * 0.12) as i32;
            let start = start + skip;
            if end <= start + 2 {
                continue;
            }
            for dx in -max_dx..=max_dx {
                let sx = 0i32.max(dx);
                let ex = (aw as i32).min(aw as i32 + dx);
                let x_start = (x0.max(sx)) as usize;
                let x_end = (x1.min(ex)) as usize;
                if x_end <= x_start + 2 {
                    continue;
                }
                let mut sum_a = 0f64;
                let mut sum_b = 0f64;
                let mut sum_aa = 0f64;
                let mut sum_bb = 0f64;
                let mut sum_ab = 0f64;
                let mut weight_sum = 0f64;
                for y in start as usize..end as usize {
                    let by = (y as i32 - dy) as usize;
                    let row_weight = activity.map_or(1.0, |values| {
                        let index = (y * factor).min(values.len().saturating_sub(1));
                        values[index].max(0.06)
                    });
                    for x in x_start..x_end {
                        let bx = (x as i32 - dx) as usize;
                        let va = ea[y * aw + x] as f64;
                        let vb = eb[by * bw + bx] as f64;
                        let weight = row_weight;
                        sum_a += weight * va;
                        sum_b += weight * vb;
                        sum_aa += weight * va * va;
                        sum_bb += weight * vb * vb;
                        sum_ab += weight * va * vb;
                        weight_sum += weight;
                    }
                }
                if weight_sum < 1.0 {
                    continue;
                }
                let mean_a = sum_a / weight_sum;
                let mean_b = sum_b / weight_sum;
                let var_a = (sum_aa - sum_a * mean_a).max(0.0);
                let var_b = (sum_bb - sum_b * mean_b).max(0.0);
                let den = (var_a * var_b).sqrt();
                if den < 1e-5 {
                    continue;
                }
                let score = (sum_ab - sum_a * mean_b) / den;
                if score > 0.35 {
                    best.push((score, dx, dy));
                }
            }
        }
        best.sort_by(|a, b| b.0.total_cmp(&a.0));
        // Keep spatially separated peaks per band.
        let mut kept = 0usize;
        for (score, dx, dy) in best {
            if votes
                .iter()
                .filter(|v| v.3 == band)
                .all(|v| (v.0 - dx).abs() > 2 || (v.1 - dy).abs() > 2)
            {
                votes.push((dx, dy, score, band));
                kept += 1;
                if kept >= 5 {
                    break;
                }
            }
        }
    }
    let mut clusters: Vec<(i32, i32, f64, u32)> = Vec::new();
    for &(dx, dy, score, band) in &votes {
        if let Some(cluster) = clusters
            .iter_mut()
            .find(|c| (c.0 - dx).abs() <= 2 && (c.1 - dy).abs() <= 2)
        {
            if score > cluster.2 {
                cluster.2 = score;
            }
            cluster.3 |= 1 << band;
        } else {
            clusters.push((dx, dy, score, 1 << band));
        }
    }
    clusters.sort_by(|a, b| {
        b.3.count_ones()
            .cmp(&a.3.count_ones())
            .then(b.2.total_cmp(&a.2))
    });
    clusters
        .into_iter()
        .filter(|c| c.3.count_ones() >= 1)
        .take(10)
        .map(|(dx, dy, _, _)| (dx * factor as i32, dy * factor as i32))
        .collect()
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
    for (xx, yy) in coarse_ncc_candidates(a, b, activity, allow_horizontal, min_overlap) {
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

    #[test]
    fn coarse_ncc_uses_two_dimensional_content() {
        let w = 240;
        let h = 200;
        let mut page = Image::solid(w, 400, [20, 20, 20]).unwrap();
        for y in 0..400 {
            for x in 0..w {
                let band = if (y / 20) % 2 == 0 { 40 } else { 120 };
                page.set(x, y, [band, band, band]);
            }
        }
        for y in 150..190 {
            for x in 100..140 {
                page.set(x, y, [250, 20, 20]);
            }
        }
        let a = Feature::extract(&page.crop(0, 0, w, h).unwrap());
        let b = Feature::extract(&page.crop(0, 100, w, h).unwrap());
        let candidates = coarse_ncc_candidates(&a, &b, None, false, 0.2);
        assert!(
            candidates.iter().any(|&(_, dy)| (dy - 100).abs() <= 8),
            "candidates: {candidates:?}"
        );
    }
}
