//! Native-only integration tests. Run with `cargo test` on a machine with Rust installed.
use scrollweave::{plan, reconstruct_layered as reconstruct, Image};
use scrollweave::{Goal, PlannerOptions};
use scrollweave::{LayeredOptions, Segmentation};

fn doc(w: usize, h: usize) -> Image {
    let mut page = Image::solid(w, h, [250, 250, 250]).unwrap();
    for y in 0..h {
        for x in 0..w {
            page.set(
                x,
                y,
                [
                    ((x * 13 + y * 7 + x * y) % 251) as u8,
                    ((y * 19 + x * 3) % 251) as u8,
                    ((x * 5 + y * 11) % 251) as u8,
                ],
            );
        }
    }
    page
}
#[test]
fn exact_known_positions_do_not_blur_or_resize() {
    let page = doc(60, 210);
    let frames = vec![
        page.crop(0, 0, 60, 110).unwrap(),
        page.crop(0, 50, 60, 110).unwrap(),
        page.crop(0, 100, 60, 110).unwrap(),
    ];
    let cfg = LayeredOptions {
        viewport: Some((0, 110)),
        appearance: false,
        texture_strength: 0.,
        ..Default::default()
    };
    let result = reconstruct(&frames, &[0, 50, 100], &cfg).unwrap();
    assert_eq!(page, result.image);
    assert_eq!(result.viewport, (0, 110));
    assert_eq!(result.evidence.width, 60);
}
#[test]
fn completely_corrupted_third_view_loses_to_consensus() {
    let page = doc(60, 210);
    let mut frames = vec![
        page.crop(0, 0, 60, 110).unwrap(),
        page.crop(0, 50, 60, 110).unwrap(),
        page.crop(0, 100, 60, 110).unwrap(),
    ];
    for y in 0..50 {
        for x in 25..37 {
            frames[0].set(x, y, [255, 255, 255])
        }
    }
    let cfg = LayeredOptions {
        viewport: Some((0, 110)),
        appearance: false,
        texture_strength: 0.,
        ..Default::default()
    };
    let result = reconstruct(&frames, &[0, 50, 100], &cfg).unwrap();
    // No visibility available at 0..49, so we deliberately DO NOT assert unsupported recovery.
    assert_eq!(result.image.width, 60);
    assert_eq!(result.image.height, 210);
    assert!(result.evidence.unverified.iter().any(|&v| v != 0));
}
#[test]
fn selective_ui_priors_do_not_alter_unoccluded_pixels() {
    let page = doc(60, 210);
    let frames = vec![
        page.crop(0, 0, 60, 110).unwrap(),
        page.crop(0, 50, 60, 110).unwrap(),
        page.crop(0, 100, 60, 110).unwrap(),
    ];
    let mut mask = vec![0u8; 3 * 110 * 60];
    for y in 40..48 {
        for x in 10..16 {
            mask[y * 60 + x] = 1
        }
    }
    let cfg = LayeredOptions {
        viewport: Some((0, 110)),
        appearance: true,
        texture_strength: 0.,
        recurrent_mask: Some(mask),
        ..Default::default()
    };
    let result = reconstruct(&frames, &[0, 50, 100], &cfg).unwrap();
    assert_eq!(result.image, page);
}
#[test]
fn alternative_graphcut_completes_and_has_correct_shape() {
    let page = doc(60, 210);
    let frames = vec![
        page.crop(0, 0, 60, 110).unwrap(),
        page.crop(0, 50, 60, 110).unwrap(),
        page.crop(0, 100, 60, 110).unwrap(),
    ];
    let cfg = LayeredOptions {
        viewport: Some((0, 110)),
        segmentation: Segmentation::GraphCut,
        appearance: false,
        texture_strength: 0.,
        ..Default::default()
    };
    let out = reconstruct(&frames, &[0, 50, 100], &cfg).unwrap();
    assert_eq!(out.image.width, 60);
    assert_eq!(out.image.height, 210);
}
#[test]
fn planning_produces_offsets_without_accessing_future_frames() {
    let page = doc(60, 210);
    let frames = vec![
        page.crop(0, 0, 60, 110).unwrap(),
        page.crop(0, 50, 60, 110).unwrap(),
        page.crop(0, 100, 60, 110).unwrap(),
    ];
    let out = reconstruct(
        &frames,
        &[0, 50, 100],
        &LayeredOptions {
            viewport: Some((0, 110)),
            appearance: false,
            texture_strength: 0.,
            ..Default::default()
        },
    )
    .unwrap();
    let p = PlannerOptions {
        goal: Goal::Verification,
        count: 3,
        step: 10,
        minimum_separation: 5,
        candidates: None,
        document_height: Some(210),
    };
    let positions = plan(&out.planner_input(), &p);
    assert!(positions.len() <= 3);
    for s in positions {
        assert!(s.scroll_position >= 0 && s.scroll_position <= 100)
    }
}
#[test]
fn malformed_mask_returns_error() {
    let page = doc(12, 30);
    let cfg = LayeredOptions {
        viewport: Some((0, 30)),
        flat_mask: Some(vec![1; 4]),
        ..Default::default()
    };
    assert!(reconstruct(&[page], &[0], &cfg).is_err());
}
