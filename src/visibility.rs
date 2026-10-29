//! Visibility via screen-vs-document motion residual, without image semantics.
//! Returned confidence is a heuristic diagnostic, NOT a calibrated probability.
use crate::feature::Feature;

#[derive(Clone, Debug)]
pub struct Visibility {
    pub masks: Vec<Vec<u8>>,
    pub screen_votes: usize,
}
/// Detect stationary screen-layer pixels only when the two frames have meaningful
/// document displacement; ambiguous blank regions are kept unknown rather than masked.
pub fn estimate(features: &[Feature], poses: &[(i32, i32)]) -> Visibility {
    let n = features.len();
    if n == 0 {
        return Visibility {
            masks: vec![],
            screen_votes: 0,
        };
    }
    let (w, h) = (features[0].width, features[0].height);
    let mut yes = vec![vec![0u8; w * h]; n];
    let mut no = vec![vec![0u8; w * h]; n];
    let mut total = 0usize;
    for i in 0..n {
        let mut neighbors = (0..n)
            .filter(|&j| {
                i != j
                    && (poses[i].1 - poses[j].1).abs() >= 18
                    && (poses[i].1 - poses[j].1).abs() < h as i32 / 2
            })
            .collect::<Vec<_>>();
        neighbors.sort_by_key(|&j| (poses[i].1 - poses[j].1).abs());
        neighbors.truncate(6);
        for j in neighbors {
            let dx = poses[j].0 - poses[i].0;
            let dy = poses[j].1 - poses[i].1;
            for y in 0..h {
                let my = y as i32 - dy;
                if my < 0 || my >= h as i32 {
                    continue;
                }
                for x in 0..w {
                    let mx = x as i32 - dx;
                    if mx < 0 || mx >= w as i32 {
                        continue;
                    }
                    let idx = y * w + x;
                    let docidx = my as usize * w + mx as usize;
                    let scr = (features[i].gray[idx] as i32 - features[j].gray[idx] as i32).abs();
                    let doc =
                        (features[i].gray[idx] as i32 - features[j].gray[docidx] as i32).abs();
                    if features[i].edge[idx] < 13 && features[j].edge[idx] < 13 && scr < 10 {
                        continue;
                    }
                    if scr + 14 < doc && scr < 18 {
                        yes[i][idx] = yes[i][idx].saturating_add(1);
                        total += 1
                    }
                    if doc + 9 < scr {
                        no[i][idx] = no[i][idx].saturating_add(1)
                    }
                }
            }
        }
    }
    // Keep high-confidence stationary evidence only; broaden it by 1px to include anti-alias borders.
    let mut masks = vec![vec![0u8; w * h]; n];
    for i in 0..n {
        for y in 0..h {
            for x in 0..w {
                let k = y * w + x;
                if yes[i][k] >= 2 && yes[i][k] >= no[i][k].saturating_add(2) {
                    masks[i][k] = 255
                }
            }
        }
        let old = masks[i].clone();
        for y in 1..h.saturating_sub(1) {
            for x in 1..w.saturating_sub(1) {
                let idx = y * w + x;
                if old[idx] == 255 {
                    continue;
                }
                let mut cnt = 0;
                for dy in -1isize..=1 {
                    for dx in -1isize..=1 {
                        let xx = (x as isize + dx) as usize;
                        let yy = (y as isize + dy) as usize;
                        if old[yy * w + xx] == 255 {
                            cnt += 1
                        }
                    }
                }
                if cnt >= 5 {
                    masks[i][idx] = 255
                }
            }
        }
    }
    Visibility {
        masks,
        screen_votes: total,
    }
}
