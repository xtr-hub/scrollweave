//! Per-pixel independent position evidence. Duplicates are alternatives, never extra witnesses.
use crate::image::Image;
use std::collections::BTreeMap;
#[derive(Clone, Debug)]
pub struct Evidence {
    pub width: usize,
    pub height: usize,
    pub support: Vec<u16>,
    pub views: Vec<u16>,
    pub conflict: Vec<u8>,
    pub unverified: Vec<u8>,
    pub invalid: Vec<u8>,
}
#[derive(Clone, Copy, Debug)]
pub struct AuditOptions {
    pub width: usize,
    pub height: usize,
    pub top: usize,
    pub bottom: usize,
    pub tolerance: u8,
}
fn equal(a: [u8; 3], b: [u8; 3], t: u8) -> bool {
    (0..3).all(|j| a[j].abs_diff(b[j]) <= t)
}
pub fn audit(
    images: &[Image],
    offsets: &[i32],
    sources: &[i32],
    options: AuditOptions,
) -> Evidence {
    let AuditOptions {
        width,
        height,
        top,
        bottom,
        tolerance: tol,
    } = options;
    let mut support = vec![0u16; width * height];
    let mut views = vec![0u16; width * height];
    let mut conflict = vec![0u8; width * height];
    let mut invalid = vec![0u8; width * height];
    let zero = offsets.iter().min().copied().unwrap_or(0);
    let mut positions: BTreeMap<i32, Vec<usize>> = BTreeMap::new();
    for (k, p) in offsets.iter().enumerate() {
        positions.entry(*p - zero).or_default().push(k);
    }
    for y in 0..height {
        for x in 0..width {
            let k = y * width + x;
            let s = sources[k];
            if s < 0 || s as usize >= images.len() {
                invalid[k] = 1;
                continue;
            }
            let s = s as usize;
            let srcy = y as i64 - (offsets[s] - zero) as i64 + top as i64;
            if srcy < top as i64 || srcy >= bottom as i64 {
                invalid[k] = 1;
                continue;
            }
            let col = images[s].pixel(x, srcy as usize);
            for (&offset, members) in &positions {
                if offset == offsets[s] - zero {
                    continue;
                }
                let sy = y as i64 - offset as i64 + top as i64;
                if sy < top as i64 || sy >= bottom as i64 {
                    continue;
                }
                views[k] = views[k].saturating_add(1);
                if members
                    .iter()
                    .any(|&j| equal(col, images[j].pixel(x, sy as usize), tol))
                {
                    support[k] = support[k].saturating_add(1)
                } else {
                    conflict[k] = 1
                }
            }
        }
    }
    let unverified = (0..width * height)
        .map(|k| u8::from(support[k] == 0))
        .collect();
    Evidence {
        width,
        height,
        support,
        views,
        conflict,
        unverified,
        invalid,
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn duplicates_do_not_vote() {
        let a = Image::solid(5, 5, [3, 4, 5]).unwrap();
        let ims = vec![a.clone(), a.clone(), a];
        let ev = audit(
            &ims,
            &[0, 0, 2],
            &[0; 25],
            AuditOptions {
                width: 5,
                height: 5,
                top: 0,
                bottom: 5,
                tolerance: 3,
            },
        );
        assert_eq!(ev.support[10], 1);
        assert_eq!(ev.views[10], 1);
        assert_eq!(ev.support[0], 0);
        assert_eq!(ev.views[0], 0);
    }
}
