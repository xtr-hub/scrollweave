# ScrollWeave — Python library backed by Rust (zero third-party runtime dependencies)

A **Python library** with a **Rust-only algorithmic core**. Import with `import scrollweave`. Both Cargo dependencies and Python runtime dependencies are empty. Native build requires `rustc`/`cargo` and a working linker; Python 3.10+.

> **Verification status (2026-10-30, Windows, Rust 1.93.1, Python 3.12.7)**: `cargo test --workspace --all-targets` passes (31 tests), `python -m unittest discover -s tests/python` passes (26 tests), `pip install .` builds and loads the native wheel, `tools/smoke_test.py` passes against both the source tree and the installed wheel, and `fixtures/shot_01..03.png` reconstruct `fixtures/expected_long.png` byte-exactly with positions `0,90,180` (MAE 0, height difference 0). The 15-frame regression suite also runs: truth 512x3306, output 512x3307, missing 0; MAE 0.1267 / exact 99.819% with masks and MAE 0.1822 / exact 99.728% without masks. A separate six-scenario headless-browser regression (fixed headers/sidebars, 2-3 scrolling frames) auto-detected the content region and produced clean content with MAE 0.01-0.03 after page padding. A 3px horizontal capture drift between real web frames is now estimated and removed before reconstruction, including when an explicit `Viewport` is supplied, eliminating doubled text in the overlap. Byte-exact parity with the research Python prototype, 30/UI/Japanese suites and other platforms remain unverified. Treat this as a tested release for the shipped and 15-frame fixtures, not a production-certified binary.

## Quick start

```shell
python -m pip install scrollweave
python -c "import scrollweave; print(scrollweave.__version__)"
```

The PyPI wheel bundles the platform-specific native dynamic library, so there is no
`pip install numpy pillow scipy ...` step. When no wheel exists for your platform,
`pip` builds the source distribution locally and therefore needs Rust/Cargo and a
working linker.

From a checkout, install and validate the native algorithms with:

```shell
python -m pip install .
cargo test --workspace
python -m unittest discover -s tests/python -v
```

## Main Python API

The public API has one function per domain: reconstruction, planning and layout.

```python
from scrollweave import (
    Goal,
    Image,
    LayeredOptions,
    PlanOptions,
    PriorMasks,
    Viewport,
    compose,
    plan_views,
    stitch_basic,
    stitch_layered,
)

# Layered reconstruction requires vertical scroll positions.
r = stitch_layered(
    ['001.png', '002.png', '003.png'],
    scroll=[0, 195, 390],
    viewport=Viewport(87, 681),
    options=LayeredOptions(segmentation='temporal'),
    diagnostics=True,
)
r.save('merged.png')
r.save_debug('diagnostics')
print(r.scroll, r.viewport, r.geometry_mode)
print(r.support_fraction, r.unverified_fraction)

# Omit viewport when the screenshots contain a fixed web header/sidebar.
# The native planner detects persistent full-width/full-height chrome and crops
# it before reconstruction:
auto = stitch_layered(['001.png', '002.png', '003.png'], scroll=[0, 195, 390])
auto.save('merged_content.png')

# Planning is a separate domain operation.
repair = plan_views(r, PlanOptions(count=3, goal=Goal.REPAIR))
verification = plan_views(r, PlanOptions(count=2, goal=Goal.VERIFICATION))

# Basic 2D alignment/compositing remains available as a separate entry point.
basic = stitch_basic(
    ['001.png', '002.png', '003.png'],
    positions=[0, 195, 390],
    viewport=Viewport(0, 720),
)

# Prior masks are validated domain objects, not bare N*H*W bytes.
masks = PriorMasks(
    width=760,
    height=512,
    frames=(bytes(760 * 512),) * 3,
)
layered = stitch_layered(
    ['001.png', '002.png', '003.png'],
    scroll=[0, 195, 390],
    viewport=Viewport(87, 681),
    options=LayeredOptions(recurrent_mask=masks, flat_mask=masks),
)

# Compose is independent of stitching.
compose(['001.png', '002.png'], layout='vertical').save('vertical.png')

# In-memory RGB frames (only Python built-ins, no Pillow / NumPy)
frame = Image(width=32, height=32, rgb=b'\xff\x00\x00' * (32 * 32))
```

`Viewport(top=..., bottom=..., left=..., right=...)` uses source-frame coordinates;
`bottom` and `right` are exclusive. Passing a viewport disables automatic fixed-chrome
detection and uses exactly the supplied band/region. Omitting it lets the native
layer detect a persistent header/sidebar and infer the scroll viewport automatically.

## Result model

`BasicResult` contains `image`, `positions`, `viewport`, `graph`,
`support_fraction`, `unverified_fraction` and optional `diagnostics`.

`LayeredResult` contains `image`, `scroll`, `viewport`, `geometry_mode`,
`support_fraction`, `unverified_fraction`, and nested objects:

- `evidence`: `source_ids`, `support`, `views`, `conflict`, `unverified`,
  `invalid_source`, `estimated_ui`, `ui_probability`, `rejected_prior_rows`
- `diagnostics` (opt-in): `coverage`, `confidence`, `provenance`, `screen_mask`

Planner output is returned by `plan_views()` as `ViewSuggestion` values; it is not
embedded in the reconstruction result. Evidence flags do **not** mean pixels are
correct or incorrect; missing content is never invented, and all output pixels at
observed locations are copied from original input RGB buffers.

## CLI

```shell
python -m scrollweave stitch 001.png 002.png 003.png -o merged.png \
    --positions 0,195,390 --viewport 87:681 --debug-dir debug \
    --recommend-views 2 --recommend-repair-views 2
python -m scrollweave stitch 001.png 002.png 003.png -o merged.png \
    --algorithm basic --positions 0,195,390
python -m scrollweave compose a.png b.png -o combined.png --layout horizontal
```

The Rust package `scrollweave-cli` builds a `scrollweave` binary; the native FFI
cdylib is named `scrollweave_ffi`. The Python console command is also `scrollweave`.

## Source layout / modules

| Rust module | Research capability it addresses |
|---|---|
| `align.rs`, `graph.rs` | Image-derived integer scroll offsets, overlap graph, robust pose optimization |
| `geometry.rs` | Persistent screen-border detection + activity-envelope fallback |
| `evidence.rs` | Independent position support, conflict and unverified maps; duplicate-view disambiguation |
| `layered.rs` | Automatic horizontal capture-drift registration, fixed-header/sidebar content-region detection, rectangular frame cropping, document/screen evidence, transient candidates, morphology, appearance histogram, side rails, prior calibration, original pixel source selection |
| `graphcut.rs` | Optional binary grid Potts mincut via pure Rust Dinic (experimental) |
| `planner.rs` | Verification suggestions and expected repair suggestions |
| `crates/scrollweave-ffi/src/lib.rs` | C ABI v5 to Python; buffers allocated and freed in Rust |
| `codec.rs` | PNG (8-bit, non-interlaced), BMP24, PPM P6; raw RGB via C ABI |

### Important scope and mathematical limits

- This is a **Rust reimplementation of the Python research algorithms**, *not* the Python research functions executed behind a thin Rust shell. Core operations are fully Rust; Python manages paths, arguments and copying result buffers.
- It follows the layered **default behaviors / major research branches**, not line-for-line numerical reproduction. The Gaussian appearance filter, morphology, graphcut solver, edge statistics and source scoring have approximations/differences. Exact pixel parity is not yet verified. Refer to `docs/PORT_STATUS.md`.
- The default layered algorithm is a layered RGB reconstruction method, **not** verified high-accuracy removal of all dynamic UI. Motion-tracked arbitrary moving object segmentation and OCR are not present in the original *working* default path, and are not asserted here.
- The layered compositor handles vertically scrolling same-width/same-height frames and estimates small horizontal capture drift before reconstruction. Arbitrary 2D translation remains the basic matcher's job; the layered path explicitly rejects user-supplied horizontal offsets.
- When scroll positions are not supplied, the older Rust feature/graph matcher estimates them. It is not guaranteed on heavy occlusion or near-blank screenshots and may decline input.
- JPEG, HEIC and MP4 decoders are **not** included. Convert externally to PNG or supply raw RGB `Image` objects. No OCR or trained AI model.
- Native behavior is verified by the tests and shipped fixtures above. No improvement over the research Python prototype is claimed, and automatic matcher results on periodic content can be ambiguous.

## Test and independent evidence

```shell
cargo test --workspace          # Must run on a Rust-equipped machine
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
python -m ruff format --check .
python -m ruff check .
python -m unittest discover -s tests/python -v
python tools/smoke_test.py     # installed-package end-to-end smoke test
python tools/benchmark_native.py --truth fixtures/expected_long.png \
      --positions 0,90,180 fixtures/shot_01.png fixtures/shot_02.png fixtures/shot_03.png
```

`research/reference/` stores a **development-only frozen Python research reference** for future parity work; it is not imported or installed by ScrollWeave, and its scientific packages are neither runtime nor build dependencies of this Rust-backed library.

### Release publishing

Publishing a GitHub Release runs `.github/workflows/publish.yml`. The workflow verifies
that the release tag matches the package version, builds the source distribution and
platform wheels for Linux/Windows/macOS, runs `twine check`, and uploads through PyPI
Trusted Publishing. Configure the PyPI trusted publisher once for owner `xtr-hub`,
repository `scrollweave`, workflow `publish.yml` (no environment). Subsequent releases
publish automatically.

MIT license.

### Reproduce the original 15-frame UI pressure test (full regression ZIP)

After installing the package and extracting the full regression ZIP:

```shell
python tools/benchmark_native.py --suite benchmark_data/starry \
  --recurrent-mask benchmark_data/starry/recurrent_mask.bin \
  --flat-mask benchmark_data/starry/flat_mask.bin \
  --viewport 90:678 --output reconstructed.png --metrics measured.json
```

The mask files are development fixtures derived from the *original observed test screenshots*, **not automatically learned from input on this call**. For a mask-free experiment omit both flags. The benchmark checks **the entire ground-truth page** and reports height error explicitly; it does not silently crop to the overlap. The shipped fixture was run on Windows on 2026-10-29: positions `0,90,180` produced an exact 120x330 page (MAE 0).
