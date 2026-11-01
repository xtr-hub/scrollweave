# Browser smoke corpus

ScrollWeave's browser regression renders ordinary HTML in headless Chrome, scrolls
real viewports, and compares the reconstructed page with full-page truth. It is
separate from the small PNG fixtures: it exercises text rendering, fixed chrome,
responsive layout, gradients, images, tables and real scroll positions.

## Dataset

`tools/web_corpus.py` deterministically generates the pages. The default corpus
has 120 pages and varies:

- viewport: 390x844, 430x932, 768x1024, 900x700, 1024x768, 1280x800, 1440x900,
  1600x900;
- chrome: none, top header, bottom footer, left rail, right rail and all
  combinations;
- header styles: solid, gradient, translucent/backdrop-blur, dark, bordered,
  inset-shadow; header height 56-112 px;
- footer height 40-72 px, left rail 112-264 px, right rail 96-224 px;
- light, dark, warm and high-contrast themes; system serif, sans and monospace
  fonts; English and Chinese text;
- content: hero blocks, cards, code, tables, SVG figures, lists, quotes and
  disclosure blocks; 2-7 viewport heights;
- capture steps with deterministic jitter, always leaving useful overlap;
- pages with no fixed chrome as controls.

The generator and captures are development artifacts and are not committed.
`output/web_smoke/` contains the generated HTML, viewport frames, full-page
truth, per-page metrics and the aggregate report after a run.

## Capture protocol

`tools/web_capture.mjs` uses only Node built-ins and the Chrome DevTools
Protocol. It does not use Playwright, Puppeteer or npm packages.

1. Open the local page and set the requested device metrics.
2. Scroll to each position and capture the viewport.
3. Hide `[data-chrome]` elements with `visibility:hidden` (layout is unchanged)
   and capture the complete document as truth.
4. Record the measured DOM rectangle of every fixed element and the actual
   scroll positions.

Truth is cropped to the content rectangle measured from the DOM. No truth pixel
is used by ScrollWeave during reconstruction.

## Evaluation

`tools/web_smoke.py` runs the default public API with no explicit positions and
no viewport (`auto`), then optionally runs a reference with the measured content
rectangle and exact scroll positions (`--explicit`).

An automatic page passes when:

- output width equals truth width;
- height differs by at most one pixel;
- MAE is at most 1.0 on the 0-255 scale;
- at least 97% of pixels are byte-exact;
- no truth pixel is missing.

The known-geometry reference is stricter: exact width and height, MAE at most
0.1 and at least 99.8% byte-exact pixels. Chrome can still change a handful of
subpixel text pixels between scroll positions, so literal byte equality is not
used as the pass gate.

The report records both automatic and known-geometry results, per-page sizes,
scroll estimates, MAE, exact-pixel fraction and failure reasons.

## Reproduce

```shell
cargo build --release -p scrollweave-ffi
python tools/web_smoke.py --count 120
```

Requirements: Python 3.10+, Node 22+ (global `WebSocket`), Chrome or Chromium.
Set `CHROME=/path/to/chrome` if it is not in a standard location.

Useful development options:

```shell
# Reuse an existing capture and evaluate only selected pages
python tools/web_smoke.py --count 120 --no-capture --only 0,1,2

# Small corpus while editing the algorithm
python tools/web_smoke.py --count 8 --allow-small

# Add the known-geometry reference run
python tools/web_smoke.py --count 120 --no-capture --explicit
```

## Arbitrary URLs and same-origin discovery

The deterministic corpus is the regression gate.  For real pages, use the same
capture/evaluation pipeline with `tools/web_crawl.py`:

```shell
# Explicit URLs
python tools/web_crawl.py https://example.com https://example.org \
    --root output/web_crawl --viewport 1280x800 --explicit

# Same-origin BFS discovery from one or more seed pages
python tools/web_crawl.py https://docs.python.org/3/ --discover-depth 2 \
    --max-pages 25 --root output/web_crawl
```

For HTTP(S) pages the capture helper warms up lazy content, identifies
fixed/sticky elements attached to a viewport edge from computed CSS, hides those
edge elements in the full-page truth, and records their rectangles.  Local
`file://` pages and the generated corpus still use explicit `data-chrome`
markers.  The default URL viewport is 1280x800; use `--viewport`, `--wait-ms`,
`--step-fraction` and `--jitter` to tune captures.

Real sites may animate, rotate ads, use consent dialogs or load content after the
last warm-up pass.  Treat a failure as a diagnostic and inspect the captured
truth, frames and `metrics.json`; do not treat this mode as a guarantee for every
production site.  `--discover-depth` uses a standard-library HTML link scan, so
JavaScript-only navigation menus may need an explicit URL list.

## Verified result (2026-11-01)

Windows 11, Chrome 156, Python 3.12.7, Rust 1.93.1, release FFI build:

| Mode | Pass | Pages | Max MAE | Median MAE | Min exact | Median exact |
|---|---|---|---|---|---|---|
| automatic viewport + positions | 120/120 | 120 | 0.069 | 0.000 | 99.848% | 100% |
| known viewport + positions | 120/120 | 120 | 0.069 | 0.000 | 99.848% | 100% |

All 120 outputs have exactly the truth width and height (height difference 0) and
zero missing pixels. The comparison covers 446,559,168 truth pixels. Automatic
scroll estimation and four-side chrome inference recover the same content
rectangle as the DOM oracle on every page; the residual MAE is limited to a few
subpixel text-compositing differences.

Re-run the same capture (no Chrome needed for the evaluation) with:

```shell
python tools/web_smoke.py --count 120 --root output/web_smoke --no-capture --explicit --workers 4
```

The earlier 32-page development corpus also passes 32/32 in both modes, and the
pre-existing six-scenario and 15-frame regressions remain in the native suite.

