use scrollweave::{codec, compose, stitch_basic, BasicOptions, ComposeOptions, Image, Layout};
fn page(w: usize, h: usize) -> Image {
    let mut a = Image::solid(w, h, [255, 255, 255]).unwrap();
    for y in 0..h {
        for x in 0..w {
            let v = ((x * 17 + y * 11 + (x * y) % 173) % 256) as u8;
            a.set(
                x,
                y,
                [v, v.wrapping_add((y % 19) as u8), v ^ ((x * 3) % 256) as u8],
            );
        }
    }
    a
}
#[test]
fn raw_pixels_retained() {
    let doc = page(66, 330);
    let imgs = vec![
        doc.crop(0, 0, 66, 180).unwrap(),
        doc.crop(0, 70, 66, 180).unwrap(),
        doc.crop(0, 150, 66, 180).unwrap(),
    ];
    let options = BasicOptions {
        known_positions: Some(vec![(0, 0), (0, 70), (0, 150)]),
        auto_trim: false,
        ..BasicOptions::default()
    };
    let result = stitch_basic(&imgs, &options).unwrap();
    assert_eq!(result.image, doc)
}
#[test]
fn image_compose() {
    let a = Image::solid(10, 12, [255, 0, 0]).unwrap();
    let b = Image::solid(9, 20, [0, 255, 0]).unwrap();
    let c = compose(
        &[a.clone(), b.clone()],
        Layout::Vertical,
        ComposeOptions {
            gap: 3,
            background: [255, 255, 255],
        },
    )
    .unwrap();
    assert_eq!((c.width, c.height), (10, 35));
    assert_eq!(c.pixel(0, 17), [0, 255, 0]);
    let g = compose(
        &[a, b],
        Layout::Grid { columns: 2 },
        ComposeOptions::default(),
    )
    .unwrap();
    assert_eq!((g.width, g.height), (19, 20));
}
#[test]
fn png_decoder_roundtrip() {
    let src = page(77, 88);
    let buf = codec::encode_png(&src);
    let dir = std::env::temp_dir().join(format!("scrollweave_{}_test.png", std::process::id()));
    std::fs::write(&dir, buf).unwrap();
    let dec = codec::read_image(&dir).unwrap();
    std::fs::remove_file(dir).unwrap();
    assert_eq!(src, dec);
}
#[test]
fn background_mask_preserves_document() {
    let doc = page(60, 240);
    let origins = [0, 35, 70, 105];
    let mut frames = origins
        .iter()
        .map(|&y| doc.crop(0, y, 60, 135).unwrap())
        .collect::<Vec<_>>();
    for (k, img) in frames.iter_mut().enumerate() {
        for y in 2..20 {
            for x in 0..60 {
                img.set(x, y, [10, 10, 10])
            }
        }
        let px = if k % 2 == 0 { 42 } else { 8 };
        for y in 48..63 {
            for x in px..px + 10 {
                img.set(x, y, [250, 20, 20])
            }
        }
    }
    let options = BasicOptions {
        known_positions: Some(origins.iter().map(|&y| (0, y as i32)).collect()),
        viewport: Some((20, 135)),
        auto_trim: false,
        ..BasicOptions::default()
    };
    let res = stitch_basic(&frames, &options).unwrap();
    assert!(res.image.height >= 190);
    assert!(res.diagnostics.is_some());
}
#[test]
fn decode_real_deflate_fixture() {
    let fixture = concat!(env!("CARGO_MANIFEST_DIR"), "/fixtures/compressed_rgb.png");
    let im = codec::read_image(fixture).unwrap();
    assert_eq!((im.width, im.height), (97, 81));
    assert_eq!(im.pixel(12, 9), [201, 171, 207]);
}
