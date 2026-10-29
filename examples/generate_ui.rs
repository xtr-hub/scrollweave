//! Deterministic UI occlusion stress set, using only ScrollWeave's Rust core.
//! cargo run --example generate_ui
use scrollweave::{write_image, Image};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let w = 320usize;
    let h = 952usize;
    let mut truth = Image::solid(w, h, [255, 255, 255])?;
    for y in 0..h {
        for x in 0..w {
            let color = [
                ((x * 13 + y * 7 + x * y % 113) % 256) as u8,
                ((x * 7 + y * 11) % 256) as u8,
                ((x * y / 7 + y * 19) % 256) as u8,
            ];
            truth.set(x, y, color)
        }
    }
    std::fs::create_dir_all("output/stress")?;
    write_image("output/stress/truth.png", &truth)?;
    let scrolls = [0usize, 96, 192, 288, 384, 480, 576, 672];
    for (n, scroll) in scrolls.iter().enumerate() {
        let mut shot = Image::solid(w, 355, [247, 247, 247])?;
        // Persistent black header + dark bottom bar, with a screen-space progress mark.
        for y in 0..40 {
            for x in 0..w {
                shot.set(x, y, [12, 22, 42])
            }
        }
        for y in 320..355 {
            for x in 0..w {
                shot.set(x, y, [25, 31, 45])
            }
        }
        for y in 40..320 {
            for x in 0..w {
                shot.set(x, y, truth.pixel(x, *scroll + y - 40))
            }
        }
        // Moving floating button: different x/y locations every other frame.
        let bx = if n % 2 == 0 { 240 } else { 18 };
        let by = if n % 3 == 0 { 152 } else { 209 };
        for y in by..(by + 43) {
            for x in bx..(bx + 43) {
                let cx = bx + 21;
                let cy = by + 21;
                let d = (x as isize - cx as isize).pow(2) + (y as isize - cy as isize).pow(2);
                if d < 21 * 21 {
                    shot.set(x, y, [249, 55, 100])
                }
            }
        }
        // Short-lived rectangular dialog; same screen location only on a subset.
        if n == 3 || n == 6 {
            for y in 102..166 {
                for x in 63..257 {
                    shot.set(x, y, [245, 247, 249])
                }
            }
            for y in 104..108 {
                for x in 75..247 {
                    shot.set(x, y, [70, 90, 120])
                }
            }
        }
        // A scrollbar that appears only during some frames.
        if n % 3 != 1 {
            for y in 76..155 {
                for x in 309..316 {
                    shot.set(x, y, [110, 116, 132])
                }
            }
        }
        write_image(format!("output/stress/shot_{:02}.png", n), &shot)?;
    }
    println!("Generated 8 occluded screenshots and clean truth at output/stress/\nKnown document positions (for isolating visibility logic): -40,56,152,248,344,440,536,632; viewport 40..320");
    Ok(())
}
