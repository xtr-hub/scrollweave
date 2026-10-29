use scrollweave::{
    compose, read_image, stitch_basic, write_image, BasicOptions, ComposeOptions, Image, Layout,
};
use std::process;

fn usage() {
    eprintln!(
        r#"ScrollWeave RustCore {} - no third-party Cargo dependencies
Usage:
  scrollweave stitch [--crop TOP BOTTOM] [--positions "x,y;x,y;..."] [--debug DIR] [-o OUT.png] INPUT.png...
  scrollweave compose [--layout vertical|horizontal|grid|overlay] [--columns N] [--gap N] [-o OUT.png] INPUT.png...
  scrollweave inspect INPUT.png
  scrollweave --version
Formats: 8-bit non-interlaced PNG / BMP24 / PPM P6.
Video and JPEG: decode to RGB/PNG with the host application; optional external FFmpeg is not bundled.
Examples:
  scrollweave stitch --debug diagnostic -o long.png 1.png 2.png 3.png
  scrollweave compose --layout grid --columns 3 --gap 8 -o sheet.png *.png
"#,
        env!("CARGO_PKG_VERSION")
    )
}

fn parse_usize(s: &str) -> Result<usize, String> {
    s.parse().map_err(|_| format!("invalid number: {s}"))
}

fn run() -> Result<(), String> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if args.is_empty() {
        usage();
        return Ok(());
    }
    let command = &args[0];
    if matches!(command.as_str(), "--help" | "-h" | "help") {
        usage();
        return Ok(());
    }
    if matches!(command.as_str(), "--version" | "-V") {
        println!("scrollweave {}", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }
    if command == "inspect" {
        for path in &args[1..] {
            let image = read_image(path).map_err(|e| e.to_string())?;
            println!("{}: {}x{} RGB8", path, image.width, image.height);
        }
        return Ok(());
    }
    if command != "stitch" && command != "compose" {
        usage();
        return Err(format!("unknown subcommand: {command}"));
    }

    let mut output = String::from("result.png");
    let mut layout = String::from("vertical");
    let mut columns = 3usize;
    let mut gap = 0usize;
    let mut crop = None;
    let mut debug = None;
    let mut known_positions: Option<Vec<(i32, i32)>> = None;
    let mut paths = Vec::new();
    let mut i = 1;

    while i < args.len() {
        match args[i].as_str() {
            "-o" | "--output" => {
                i += 1;
                output = args.get(i).ok_or("--output requires value")?.clone();
            }
            "--layout" => {
                i += 1;
                layout = args.get(i).ok_or("--layout requires value")?.clone();
            }
            "--columns" => {
                i += 1;
                columns = parse_usize(args.get(i).ok_or("--columns requires value")?)?;
            }
            "--gap" => {
                i += 1;
                gap = parse_usize(args.get(i).ok_or("--gap requires value")?)?;
            }
            "--positions" => {
                i += 1;
                let text = args.get(i).ok_or("--positions requires x,y;x,y;...")?;
                let mut values = Vec::new();
                for pair in text.split(';') {
                    let (x, y) = pair
                        .split_once(',')
                        .ok_or("--positions requires x,y pairs")?;
                    values.push((
                        x.trim().parse::<i32>().map_err(|_| "invalid position x")?,
                        y.trim().parse::<i32>().map_err(|_| "invalid position y")?,
                    ));
                }
                known_positions = Some(values);
            }
            "--crop" => {
                let top = parse_usize(args.get(i + 1).ok_or("--crop needs TOP BOTTOM")?)?;
                let bottom = parse_usize(args.get(i + 2).ok_or("--crop needs TOP BOTTOM")?)?;
                crop = Some((top, bottom));
                i += 2;
            }
            "--debug" => {
                i += 1;
                debug = Some(args.get(i).ok_or("--debug requires directory")?.clone());
            }
            "--help" | "-h" => {
                usage();
                return Ok(());
            }
            other if other.starts_with('-') => return Err(format!("unrecognized option: {other}")),
            _ => paths.push(args[i].clone()),
        }
        i += 1;
    }

    if paths.is_empty() {
        return Err("no input images".into());
    }
    let images = paths
        .iter()
        .map(|path| read_image(path).map_err(|e| format!("{path}: {e}")))
        .collect::<Result<Vec<Image>, _>>()?;

    if command == "compose" {
        let layout = match layout.as_str() {
            "vertical" => Layout::Vertical,
            "horizontal" => Layout::Horizontal,
            "grid" => Layout::Grid { columns },
            "overlay" => Layout::Overlay,
            _ => return Err(format!("invalid layout: {layout}")),
        };
        let result = compose(
            &images,
            layout,
            ComposeOptions {
                gap,
                background: [255, 255, 255],
            },
        )
        .map_err(|e| e.to_string())?;
        write_image(&output, &result).map_err(|e| e.to_string())?;
        println!("saved {} ({}x{})", output, result.width, result.height);
    } else {
        let options = BasicOptions {
            viewport: crop,
            known_positions,
            ..BasicOptions::default()
        };
        let result = stitch_basic(&images, &options).map_err(|e| e.to_string())?;
        result.save(&output).map_err(|e| e.to_string())?;
        println!(
            "saved {} ({}x{})",
            output, result.image.width, result.image.height
        );
        println!("positions: {:?}", result.positions);
        println!(
            "viewport: {:?}, accepted edges: {}, rejected: {}",
            result.viewport, result.graph_edges, result.graph_rejected
        );
        if let Some(ref diagnostics) = result.diagnostics {
            println!(
                "independent support: {:.1}%, unverified: {:.1}%",
                diagnostics.independent_support_ratio * 100.0,
                diagnostics.unknown_ratio * 100.0
            );
        }
        if let Some(directory) = debug {
            result.save_debug(&directory).map_err(|e| e.to_string())?;
            println!("diagnostics: {}", directory);
        }
        for warning in &result.warnings {
            eprintln!("warning: {warning}");
        }
    }
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("error: {error}");
        process::exit(2);
    }
}
