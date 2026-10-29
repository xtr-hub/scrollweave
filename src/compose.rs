use crate::image::{err, Image, SwResult};
#[derive(Clone, Copy, Debug)]
pub enum Layout {
    Vertical,
    Horizontal,
    Grid { columns: usize },
    Overlay,
}
#[derive(Clone, Copy, Debug)]
pub struct ComposeOptions {
    pub gap: usize,
    pub background: [u8; 3],
}
impl Default for ComposeOptions {
    fn default() -> Self {
        Self {
            gap: 0,
            background: [255, 255, 255],
        }
    }
}
pub fn compose(images: &[Image], layout: Layout, options: ComposeOptions) -> SwResult<Image> {
    if images.is_empty() {
        return err("no images");
    }
    let gap = options.gap;
    let (w, h, locs) = match layout {
        Layout::Vertical => {
            let w = images.iter().map(|i| i.width).max().unwrap();
            let h = images.iter().map(|i| i.height).sum::<usize>() + gap * (images.len() - 1);
            let mut y = 0;
            let mut loc = Vec::new();
            for i in images {
                loc.push(((w - i.width) / 2, y));
                y += i.height + gap;
            }
            (w, h, loc)
        }
        Layout::Horizontal => {
            let h = images.iter().map(|i| i.height).max().unwrap();
            let w = images.iter().map(|i| i.width).sum::<usize>() + gap * (images.len() - 1);
            let mut x = 0;
            let mut loc = Vec::new();
            for i in images {
                loc.push((x, (h - i.height) / 2));
                x += i.width + gap;
            }
            (w, h, loc)
        }
        Layout::Grid { columns } => {
            if columns == 0 {
                return err("grid columns must be positive");
            };
            let rows = images.len().div_ceil(columns);
            let mut cw = vec![0usize; columns];
            let mut rh = vec![0usize; rows];
            for (k, i) in images.iter().enumerate() {
                cw[k % columns] = cw[k % columns].max(i.width);
                rh[k / columns] = rh[k / columns].max(i.height)
            }
            let w = cw.iter().sum::<usize>() + gap * (columns - 1);
            let h = rh.iter().sum::<usize>() + gap * (rows - 1);
            let mut loc = Vec::new();
            for (k, img) in images.iter().enumerate() {
                let x = cw[..k % columns].iter().sum::<usize>()
                    + gap * (k % columns)
                    + (cw[k % columns] - img.width) / 2;
                let y = rh[..k / columns].iter().sum::<usize>()
                    + gap * (k / columns)
                    + (rh[k / columns] - img.height) / 2;
                loc.push((x, y))
            }
            (w, h, loc)
        }
        Layout::Overlay => {
            let w = images.iter().map(|i| i.width).max().unwrap();
            let h = images.iter().map(|i| i.height).max().unwrap();
            (w, h, images.iter().map(|_| (0, 0)).collect())
        }
    };
    let mut output = Image::solid(w, h, options.background)?;
    for (i, (x, y)) in images.iter().zip(locs) {
        output.blit(i, x, y)?
    }
    Ok(output)
}
