//! Additional regression tests for edge cases that the original suite did not cover.
use scrollweave::align::{match_pair, Match};
use scrollweave::codec::{read_image, write_image};
use scrollweave::compose::{compose, ComposeOptions, Layout};
use scrollweave::evidence::{audit, AuditOptions};
use scrollweave::feature::Feature;
use scrollweave::graph::{solve, Edge};
use scrollweave::image::Image;
use scrollweave::layered::{reconstruct, LayeredOptions};
use scrollweave::planner::{plan, Goal, PlannerOptions};
use scrollweave::ErrorKind;
use std::path::PathBuf;

fn textured(w: usize, h: usize) -> Image {
    let mut image = Image::solid(w, h, [250, 250, 250]).unwrap();
    for y in 0..h {
        for x in 0..w {
            image.set(
                x,
                y,
                [
                    ((x * 37 + y * 11 + x * y % 97) % 256) as u8,
                    ((x * 13 + y * 29 + x * y % 53) % 256) as u8,
                    ((x * 7 + y * 41 + x * y % 71) % 256) as u8,
                ],
            );
        }
    }
    image
}

fn scratch(name: &str) -> PathBuf {
    std::env::temp_dir().join(format!("scrollweave_test_{}_{name}", std::process::id()))
}

#[test]
fn codec_rejects_malformed_and_unknown_files() {
    let truncated = scratch("truncated.png");
    std::fs::write(&truncated, b"\x89PNG\r\n\x1a\nnot-a-complete-png").unwrap();
    assert!(read_image(&truncated).is_err());

    let unknown = scratch("unknown.dat");
    std::fs::write(&unknown, b"not an image").unwrap();
    assert_eq!(
        read_image(&unknown).unwrap_err().kind,
        ErrorKind::Unsupported
    );

    let bad_ppm = scratch("maxval.ppm");
    std::fs::write(&bad_ppm, b"P6\n2 2\n100\n012345678901").unwrap();
    assert_eq!(
        read_image(&bad_ppm).unwrap_err().kind,
        ErrorKind::Unsupported
    );

    let bad_output = scratch("output.jpg");
    assert_eq!(
        write_image(&bad_output, &textured(2, 2)).unwrap_err().kind,
        ErrorKind::Unsupported
    );

    for path in [truncated, unknown, bad_ppm] {
        let _ = std::fs::remove_file(path);
    }
}

#[test]
fn horizontal_alignment_is_opt_in() {
    let page = textured(160, 80);
    let a = Feature::extract(&page.crop(0, 0, 120, 80).unwrap());
    let b = Feature::extract(&page.crop(37, 0, 120, 80).unwrap());

    let horizontal = match_pair(&a, &b, None, true, 0.18).unwrap();
    assert_eq!((horizontal.dx, horizontal.dy), (37, 0));

    let vertical_only = match_pair(&a, &b, None, false, 0.18).unwrap();
    assert_eq!(vertical_only.dx, 0);
}

#[test]
fn graph_splits_disconnected_components() {
    let m = |dx, dy| Match {
        dx,
        dy,
        score: 0.99,
        overlap: 0.5,
        confidence: 0.99,
    };
    let graph = solve(
        4,
        vec![Edge::new(0, 1, &m(0, 30)), Edge::new(2, 3, &m(0, 50))],
    );

    assert_eq!(graph.components, 2);
    assert_eq!(graph.positions[0], Some((0, 0)));
    assert_eq!(graph.positions[1], Some((0, 30)));
    assert_eq!(graph.positions[2], Some((0, 0)));
    assert_eq!(graph.positions[3], Some((0, 50)));
    assert!(graph.rejected.is_empty());
}

#[test]
fn compose_horizontal_grid_overlay_keep_expected_offsets() {
    let red = Image::solid(10, 12, [255, 0, 0]).unwrap();
    let green = Image::solid(9, 20, [0, 255, 0]).unwrap();

    let horizontal = compose(
        &[red.clone(), green.clone()],
        Layout::Horizontal,
        ComposeOptions {
            gap: 3,
            background: [255, 255, 255],
        },
    )
    .unwrap();
    assert_eq!((horizontal.width, horizontal.height), (22, 20));
    assert_eq!(horizontal.pixel(0, 4), [255, 0, 0]);
    assert_eq!(horizontal.pixel(0, 3), [255, 255, 255]);
    assert_eq!(horizontal.pixel(13, 0), [0, 255, 0]);

    let grid = compose(
        &[red.clone(), green.clone()],
        Layout::Grid { columns: 2 },
        ComposeOptions {
            gap: 3,
            background: [255, 255, 255],
        },
    )
    .unwrap();
    assert_eq!((grid.width, grid.height), (22, 20));
    assert_eq!(grid.pixel(13, 19), [0, 255, 0]);

    let overlay = compose(&[red, green], Layout::Overlay, ComposeOptions::default()).unwrap();
    assert_eq!((overlay.width, overlay.height), (10, 20));
    assert_eq!(overlay.pixel(0, 0), [0, 255, 0]);
    assert_eq!(overlay.pixel(9, 0), [255, 0, 0]);
}

#[test]
fn layered_handles_single_frame_and_rejects_bad_viewport() {
    let page = textured(12, 30);
    let cfg = LayeredOptions {
        viewport: Some((0, 30)),
        appearance: false,
        texture_strength: 0.,
        ..Default::default()
    };
    let result = reconstruct(std::slice::from_ref(&page), &[0], &cfg).unwrap();
    assert_eq!(result.image, page);
    assert_eq!(result.source_ids, vec![0; 12 * 30]);
    assert!(result.evidence.support.iter().all(|&v| v == 0));
    assert!(result.evidence.views.iter().all(|&v| v == 0));
    assert!(result.evidence.unverified.iter().all(|&v| v == 1));

    let bad_viewport = LayeredOptions {
        viewport: Some((20, 20)),
        ..Default::default()
    };
    assert!(reconstruct(std::slice::from_ref(&page), &[0], &bad_viewport).is_err());
    assert!(reconstruct(&[], &[], &LayeredOptions::default()).is_err());
}

#[test]
fn evidence_reports_conflict_and_invalid_source() {
    let page = textured(4, 8);
    let mut first = page.crop(0, 0, 4, 6).unwrap();
    let mut second = page.crop(0, 2, 4, 6).unwrap();
    second.set(1, 0, [255, 255, 255]);
    first.set(3, 5, [1, 2, 3]);

    let mut sources = vec![0; 4 * 6];
    sources[0] = 9;
    let evidence = audit(
        &[first, second],
        &[0, 2],
        &sources,
        AuditOptions {
            width: 4,
            height: 6,
            top: 0,
            bottom: 6,
            tolerance: 3,
        },
    );

    assert_eq!(evidence.invalid[0], 1);
    let k = 2 * 4 + 1;
    assert_eq!(evidence.views[k], 1);
    assert_eq!(evidence.support[k], 0);
    assert_eq!(evidence.conflict[k], 1);
    assert_eq!(evidence.unverified[k], 1);
}

#[test]
fn planner_respects_candidates_separation_and_bounds() {
    let page = textured(16, 80);
    let frames = vec![
        page.crop(0, 0, 16, 40).unwrap(),
        page.crop(0, 40, 16, 40).unwrap(),
    ];
    let result = reconstruct(
        &frames,
        &[0, 40],
        &LayeredOptions {
            viewport: Some((0, 40)),
            appearance: false,
            texture_strength: 0.,
            ..Default::default()
        },
    )
    .unwrap();

    let options = PlannerOptions {
        goal: Goal::Verification,
        count: 1,
        step: 5,
        minimum_separation: 0,
        candidates: Some(vec![20]),
        document_height: Some(80),
    };
    let suggestions = plan(&result.planner_input(), &options);
    assert_eq!(suggestions.len(), 1);
    assert_eq!(suggestions[0].scroll_position, 20);
    assert!(suggestions[0].estimated_gain > 0.);
    assert!(suggestions[0].target_pixels > 0);

    assert!(plan(
        &result.planner_input(),
        &PlannerOptions {
            count: 0,
            ..options.clone()
        }
    )
    .is_empty());
    assert!(plan(
        &result.planner_input(),
        &PlannerOptions {
            candidates: Some(vec![100]),
            ..options.clone()
        }
    )
    .is_empty());
    assert!(plan(
        &result.planner_input(),
        &PlannerOptions {
            document_height: Some(79),
            ..options
        }
    )
    .is_empty());
}

#[test]
fn image_bounds_and_overflow_are_checked() {
    assert_eq!(
        Image::new(2, 2, b"abc".to_vec()).unwrap_err().kind,
        ErrorKind::Input
    );
    assert_eq!(
        Image::new(usize::MAX, 2, Vec::new()).unwrap_err().kind,
        ErrorKind::Limit
    );
    assert!(Image::solid(0, 1, [0, 0, 0]).is_err());

    let image = textured(6, 7);
    assert!(image.crop(5, 0, 2, 1).is_err());
    let mut canvas = Image::solid(4, 4, [0, 0, 0]).unwrap();
    assert!(canvas.blit(&image, 0, 0).is_err());
}
