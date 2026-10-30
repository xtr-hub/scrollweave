# ScrollWeave

Stitch scrolling screenshots into a clean page. Python API, Rust core, no third-party runtime dependencies.

[![PyPI](https://img.shields.io/pypi/v/scrollweave.svg)](https://pypi.org/project/scrollweave/)
[![CI](https://github.com/xtr-hub/scrollweave/actions/workflows/python-rust.yml/badge.svg)](https://github.com/xtr-hub/scrollweave/actions/workflows/python-rust.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

ScrollWeave reconstructs a long page from overlapping screenshots. It estimates the
scroll motion, detects persistent browser chrome such as fixed headers and sidebars,
and copies the original image pixels into the output. No image content is generated.

## Features

- Layered reconstruction of vertically scrolling pages
- Automatic fixed-header/sidebar detection
- Automatic small horizontal capture-drift correction
- Automatic inner-pane detection for fixed application windows
- Multi-band 2D gradient NCC with graph consensus for automatic offsets
- Optional `Viewport` for explicit content regions
- Basic 2D alignment and compositing
- PNG, BMP and PPM support
- Python standard library only at runtime; no NumPy, Pillow or SciPy
- Rust core with no third-party crates

## Install

```shell
python -m pip install scrollweave
```

Prebuilt wheels are available for Linux, Windows and macOS. On a platform without a
wheel, pip builds the source distribution and needs Rust/Cargo and a linker.

To install a checkout:

```shell
python -m pip install .
```

## Quick start

```python
from scrollweave import stitch_layered

result = stitch_layered(['top.png', 'middle.png', 'bottom.png'])
result.save('page.png')
```

Scroll positions are optional and are estimated when omitted. If the screenshots
contain lots of blank space, repeated patterns or heavy occlusion, pass the positions
explicitly:

```python
result = stitch_layered(
    ['top.png', 'middle.png', 'bottom.png'],
    scroll=[0, 640, 1280],
)
```

To crop a known content region, pass a `Viewport`:

```python
from scrollweave import Viewport

result = stitch_layered(
    ['top.png', 'bottom.png'],
    scroll=[625, 0],
    viewport=Viewport(top=75, bottom=1035, left=357, right=1910),
)
```

`Viewport` uses source-frame coordinates; `bottom` and `right` are exclusive. If a
viewport is omitted, ScrollWeave tries to detect fixed chrome and infer the content
region automatically.

## Python API

The main operations are:

```python
from scrollweave import (
    Image,
    LayeredOptions,
    PlanOptions,
    Viewport,
    compose,
    plan_views,
    stitch_basic,
    stitch_layered,
)

# Layered reconstruction
result = stitch_layered(images_or_paths, scroll=[0, 640, 1280])

# Basic 2D alignment/compositing
basic = stitch_basic(images_or_paths, positions=[0, 640])

# Layout without alignment
combined = compose(images_or_paths, layout='vertical', gap=8)

# Recommend additional views from an existing layered result
suggestions = plan_views(result, PlanOptions(count=2))
```

`stitch_layered` returns `LayeredResult` with the output image, scroll positions,
viewport, evidence maps and optional diagnostics. `Image` accepts raw RGB bytes:

```python
frame = Image(width=32, height=32, rgb=b'\xff\x00\x00' * (32 * 32))
```

See `docs/ARCHITECTURE.md` for the processing pipeline and `docs/PORT_STATUS.md` for
the current implementation status.

## CLI

```shell
# Reconstruct a page; positions are estimated when omitted
scrollweave stitch shot-*.png -o page.png

# Explicit positions and an explicit content band
scrollweave stitch shot-*.png -o page.png --positions 0,640,1280 --viewport 75:1035

# Basic matcher
scrollweave stitch shot-*.png -o page.png --algorithm basic

# Compose images without alignment
scrollweave compose a.png b.png -o combined.png --layout horizontal
```

The Python module is also runnable directly with `python -m scrollweave`.

## Errors

ScrollWeave raises specific exception types instead of passing raw OS or C error
strings through to callers:

```python
from scrollweave import Image, ImageFormatError, ImageIOError, InvalidArgumentError

try:
    Image.open('missing.png')
except ImageIOError as exc:
    print(exc)  # includes the path and a human-readable reason
```

The exception hierarchy is:

| Exception | Meaning |
|---|---|
| `InvalidArgumentError` | Invalid or inconsistent API arguments (`ValueError`) |
| `LimitError` | A size/count/allocation limit was exceeded (`ValueError`) |
| `UnsupportedFormatError` | Unsupported image or output format (`ValueError`) |
| `ImageIOError` | File could not be opened, created or written (`OSError`) |
| `ImageFormatError` | Image bytes are corrupt or malformed (`ValueError`) |
| `NativeError` | General native-engine failure (`RuntimeError`) |
| `NativeUnavailable` | Native library is missing or has an ABI mismatch (`RuntimeError`) |
| `InternalError` | Unexpected native panic or invariant failure (`RuntimeError`) |

All of these inherit from `ScrollWeaveError`.

## Limitations

- Layered reconstruction handles vertical scrolling and same-sized frames. Use
  `stitch_basic` for arbitrary 2D positions.
- Input formats are PNG, BMP and PPM; JPEG, HEIC and video are not supported.
- OCR, semantic segmentation and learned UI detection are not included.
- Automatic scroll estimation can be ambiguous on blank or periodic pages; explicit
  positions are the reliable option there.
- Output pixels are copied from input frames; missing content is not invented.

## Development

```shell
cargo test --workspace --all-targets
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
python -m unittest discover -s tests/python
python -m ruff format --check .
python -m ruff check .
```

`research/reference/` contains a frozen Python research reference used for future
parity work. It is not imported or installed by the package.

## License

MIT
