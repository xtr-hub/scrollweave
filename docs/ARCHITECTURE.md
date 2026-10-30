# Algorithm and completeness report

The algorithmic core is entirely Rust and compiles with a dependency-free `Cargo.toml`
on a Rust installation; the Python frontend uses only the standard library. The core
accepts `RGB8` buffers; PNG/BMP/PPM support is included.

## Workspace layout

The Cargo workspace separates target types so the library, C ABI and CLI do not
compete for platform-specific artifacts:

- root package `scrollweave` — core algorithm library (`rlib`), tests, fixtures
- `crates/scrollweave-ffi` — `scrollweave_ffi` `cdylib` exposing the stable C ABI to Python
- `crates/scrollweave-cli` — `scrollweave` binary

`pip install .` builds only `scrollweave-ffi`; Rust users depend on the core
`scrollweave` library; the CLI is an optional workspace member.

## Repository layout

```text
src/                         core Rust library (rlib)
crates/scrollweave-ffi/      Python C ABI cdylib
crates/scrollweave-cli/      optional Rust CLI package
scrollweave/                 Python package
tests/                       Rust integration tests
tests/python/                Python unittest suite
fixtures/                    shared image fixtures
docs/                        architecture and status documents
research/reference/          frozen research reference (not built)
examples/                    small usage examples
tools/                       smoke test and benchmark tools
```

## Processing

1. Build a continuous grayscale + gradient feature field, plus horizontal/vertical
   projections (not fixed non-overlapping patch grids).
2. Estimate which screen rows have variation across images. Weight permanent chrome
   lower in translation search.
3. Generate plausible offsets from row/column projections and masked multi-band 2D
   gradient NCC, verify with 2D robust gradient / color scores, refine in integer pixels.
4. Build an ordered or unordered pairwise constraint graph. Use a confidence-weighted
   spanning forest only as initialization; reject inconsistent redundant edges and
   update node positions via weighted median votes.
5. Compare each aligned frame in *screen space* and *document space* to identify
   persistent UI that is inconsistent with document motion.
6. Estimate small horizontal capture drift between overlapping frames from
   grayscale registration and translate frames to a common horizontal origin.
7. When no explicit viewport is supplied, infer a rectangular content region from
   screen-stationary full-width horizontal and full-height vertical edges. Crop
   fixed headers/sidebars before layered reconstruction; skip cropping when no
   persistent chrome is found.
8. At each document coordinate, compare available original RGB observations;
   prefer different-screen-origin agreement and penalize probable UI pixels.
9. Save original RGB pixels, not a filtered image, along with provenance, coverage,
   and uncertainty diagnostics.

## Research gaps / what is NOT claimed

* Inconsistent loop constraints cannot be fully resolved when the strongest initial
  geometry edge is itself wrong: initialization is still not a globally optimal robust
  estimator. Graph relaxation is not a formal maximum-likelihood solver.
* Multiple conflicting images with no independent validation cannot be perfectly
  disambiguated in general. No synthetic details are generated.
* UI object trajectories, temporal segmentation, self-supervised appearance models,
  full multi-label graph cuts and active observation suggestion were studied in earlier
  Python research. Those modules are **not ported** here.
* The current matching implementation has no transform invariance: the basic matcher
  supports 2D integer translation but not rotations, rescaling or perspective changes.
* PNG encoder writes uncompressed DEFLATE blocks for portability, which makes files
  larger than optimized libpng outputs. PNG decoder is pure Rust but only supports
  non-interlaced 8-bit PNGs.
* This Rust rewrite has no verified numerical parity with the research prototype, and no performance
  benchmarks against the full regression suite. Correctness tests were run on Windows
  with Rust 1.93.1; other platforms remain untested.

## Future backlog

- Optional codecs (JPEG, video), behind independent crates and feature flags; keep
  base crate dependency-free.
- True dynamic object tracklets with UI appearance and temporal correspondence.
- Minimal source-label cut paths near seams, not expensive full-frame graph cuts.
- Bound the compositing runtime by indexed frame coverage intervals.
- Whole-document loss and UI leak rate with known synthetic masks and full-height
  geometry metrics rather than only common-crop MAE.
- CI on Linux, Windows and macOS with `cargo fmt --check`, `cargo clippy`, `cargo test`.

## Python-facing architecture (0.2)

`pip install .` uses `build_backend.py`, a standard-library-only PEP 517 backend, to run `cargo build --release --lib -p scrollweave-ffi`. The resulting `libscrollweave_ffi.so` / `.dylib` / `scrollweave_ffi.dll` is included inside a platform-specific `py3-none-<platform>` wheel. PyPI 0.9.3 includes Linux, Windows and macOS wheels plus a source distribution; 0.9.4 packaging is prepared for the next trusted-publishing run.

At runtime: `scrollweave.api` (`Image`, option dataclasses, `BasicResult`/`LayeredResult`) → `scrollweave._binding` (`ctypes`) → `crates/scrollweave-ffi/src/lib.rs` (C ABI version 5, with the legacy basic symbols retained) → the core Rust codec/stitch/layered/planner modules. The Python layer owns bytes passed to the call; Rust clones them into engine-owned buffers. Result allocations are made by Rust and always freed with the matching `sw_*_free_*` function after `ctypes.string_at` copies the data to Python. The ABI is not a CPython extension ABI; it is a Rust `cdylib` loaded through the standard-library `ctypes` bridge.

The v5 protocol exposes layered reconstruction with an optional rectangular content
region, and returns image, coverage, confidence, provenance, screen mask,
document-space evidence arrays, screen-space UI masks, source IDs, the content
viewport, geometry mode, and an opaque planning handle. `plan_views()` reuses that
handle, so planning does not reconstruct the page again. Python validates arguments
and copies results; it does not perform alignment, segmentation, filtering,
compositing, image encoding or reconstruction.

## Library-interface conformance

| Area | Current status |
|---|---|
| Runtime dependencies | Zero Python packages and zero third-party Cargo crates |
| Rust API shape | 0.x: crate-root re-exports are the stable surface; modules remain public for now |
| Error model | `SwError { kind: ErrorKind, message }` with Input/Unsupported/Limit/Io/Internal/Other kinds; Python maps them to `InvalidArgumentError`, `UnsupportedError`, `LimitError`, `ImageIOError`, `NativeError` and `InternalError` with operation/file context |
| FFI safety | `repr(C)` structs, version query, `catch_unwind`, Rust-owned result buffers, explicit free functions |
| Python packaging | Standard-library PEP 517 backend; platform wheel; license file and `py.typed` included; no legacy `setup.py` |
| Python typing | PEP 561 marker included; API annotations are inline; result array fields are typed as `array.array` |
| Wheel portability | Local platform tags only (`win_amd64`, `linux_x86_64`, macOS); no manylinux/auditwheel pipeline |
| CI | Linux/Windows/macOS matrix, Python 3.10/3.13, `cargo test --workspace`, `cargo doc --workspace`, unittest, smoke test, wheel build; MSRV job on Rust 1.73 |
| Lint/docs | `cargo doc --no-deps` is part of CI; `cargo clippy` completes with style warnings that are not yet gated |
| MSRV | Declared `rust-version = "1.73"`; checked in CI |
| Platform verification | Windows Rust 1.93.1 / Python 3.12.7 verified locally; other platforms remain untested |

The workspace gives each Windows artifact a distinct base name: core library
`scrollweave` (rlib), FFI library `scrollweave_ffi` (cdylib), and CLI binary
`scrollweave` (package `scrollweave-cli`). This avoids PDB collisions while keeping
the natural CLI name; the Python console command is also `scrollweave`.
