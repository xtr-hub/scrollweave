# ScrollWeave Rust/Python port — implementation and verification audit

Date: 2026-10-29. Goal: a genuine Python package executing image and UI reconstruction algorithms in Rust with *no third-party Rust or Python runtime libraries*.

## Coverage matrix

| Python research capability | Rust module | Status |
|---|---|---|
| Basic PNG/BMP/PPM + raw RGB, integer source-pixel copying | `codec`, `image`, `compose` | Compiled / tested on Windows |
| Image-only pair motion and global graph | `feature`, `align`, `graph`, `stitch` | Compiled / tested on Windows |
| Stable full-width screen borders and activity envelope | `geometry` | Implemented with slightly different aggregation |
| Temporal screen/document witness labels | `layered` | Implemented with evidence smoothing |
| Screen-stationary edges | `layered` | Implemented with neighborhood dilation; median edges approximate |
| Transient local masks / morphology | `layered` | Implemented as opt-in; not enabled by default |
| Adaptive 16³-bin UI RGB histogram | `layered` | Implemented; separable 3-tap smoothing instead of SciPy Gaussian |
| Edge-attached persistent narrow rail | `layered` | Implemented with candidate ranking |
| Broad flat-prior calibration near viewport | `layered` | Implemented with image-derived activity |
| Duplicate-safe independent witness audit | `evidence` | Implemented |
| Active view verification planning | `planner` | Implemented, no future pixels used |
| Expected repair selection | `planner` | Implemented, default heuristic weights |
| Optional binary screen-layer graph cut | `graphcut` | Implemented with Rust Dinic; not default |
| Image provenance and uncertainty maps | `ffi` + `layered` | Implemented and available as Python fields |
| Existing object segmentation ablation | n/a | **Not integrated**: original experiment produced no measurable improvement |
| `spatial_strength` auxiliary non-default prior | n/a | **Not ported**: default layered path did not expose it |
| Exact numerical/pixel parity with research baseline | n/a | **Not established** |
| Rust binary successfully compiled | n/a | **Verified on Windows**: Rust 1.93.1, `cargo test --workspace --all-targets` passes |
| Real Japanese video fully restored with zero UI | n/a | **Not demonstrated** |

## Testing actually completed

- Python 3.10+ API syntax validation (`compileall`): PASS.
- Python `unittest` including API dispatch + PEP 517 packaging: **23 passed, 0 skipped** with the native library built.
- Rust source bracket / structural audit: PASS (superseded by a real compiler run below).
- Python→C ABI v4 round-trip was smoke-tested against a temporary compiled **C stub**: PASS. This only validates wrapper structure, not the Rust code.
- `cargo test --workspace --all-targets`, `cargo build --release --workspace`, native FFI round trip: **PASS** on Windows (Rust 1.93.1). 12 library + 5 integration + 6 layered + 8 edge-case tests pass.
- Quality gates: `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo doc --workspace --no-deps`, `ruff format --check`, `ruff check`: **PASS**.
- `pip install . --no-build-isolation` in a clean venv: **PASS**. The wheel builds, the console script runs, and the Python API loads native ABI v4.
- `python tools/smoke_test.py`: **PASS** against both the source tree and the installed wheel (23 checks: ABI, fixtures, pixel-exact stitch, codec roundtrips, debug output, compose, CLI, error path).
- Shipped 3-frame fixture vs `fixtures/expected_long.png`, positions `0,90,180`: **exact 120x330 page, MAE 0, height difference 0**.
- 15-frame full regression ZIP: **PASS** on Windows. Truth 512x3306; output 512x3307; missing pixels 0. With masks: MAE 0.1267, exact 99.819%. Without masks: MAE 0.1822, exact 99.728%. Research-prototype numerical parity is still not established.
- Automatic position estimation on the bundled periodic fixture selects a content-equivalent alias: **limitation observed**, use explicit offsets for that fixture.

## A strict next acceptance test for native users

1. `cargo test --workspace` must succeed including `tests/layered.rs`. **PASS on Windows.**
2. `python -m pip install . --no-build-isolation` must produce an importable `scrollweave` with native v2 ABI. **PASS in a clean venv.**
3. Python integration tests must run with no skips. **PASS: 23/23.**
4. Compare 15/30/UI & Japanese datasets against the *same full-page truth*, same positions/viewport, same coverage and masks. Track 1px geometry differences as a separate failure. **15-frame suite PASS on Windows; 30/UI/Japanese suites not available here.**
5. Do not claim better MAE than the original research Python baseline unless measured. **No improvement claimed.**

## Build/tooling policy

`pyproject.toml`: no Python build requirements; `Cargo.toml`: no third-party crates. Host must supply Python 3.10+, Rust/Cargo and an OS linker. `.tar.gz` is a **source distribution** and requires a native build. No compiled wheel is shipped from this environment.
