"""Browser smoke test for ScrollWeave using a deterministic local web corpus.

What it does
------------
1. Generates ``--count`` real HTML pages with varied fixed headers, left/right
   rails, fixed footers, themes, fonts and content blocks.
2. Renders them in headless Chrome through the DevTools Protocol.  For every
   page it captures the viewport at several scroll positions plus one full-page
   truth image with the fixed chrome hidden.  A ``capture.json`` records the
   actual DOM geometry.
3. Runs ScrollWeave twice per page:
   * ``known``  - expected content rectangle and exact scroll positions;
   * ``auto``   - the default public API: automatic viewport and positions.
4. Compares pixels against the cropped full-page truth and writes a JSON and
   Markdown report.

The corpus is intentionally local: it is reproducible, has no third-party
Python/Node packages, and does not depend on the public network.  The same
harness can still be pointed at a different Chrome binary with ``CHROME``.

Usage::

    py tools/web_smoke.py --count 120
    py tools/web_smoke.py --count 8 --no-capture --allow-small  # inspect a run
    py tools/web_smoke.py --count 120 --explicit                # also known run

The default output directory is ``output/web_smoke``.  It is generated state and
is intentionally not part of the source distribution.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _crop(image, left: int, top: int, right: int, bottom: int):
    from scrollweave import Image

    left = max(0, min(left, image.width))
    right = max(left, min(right, image.width))
    top = max(0, min(top, image.height))
    bottom = max(top, min(bottom, image.height))
    width = right - left
    height = bottom - top
    stride = image.width * 3
    data = b''.join(
        image.rgb[y * stride + left * 3 : y * stride + right * 3] for y in range(top, bottom)
    )
    return Image(width, height, data)


def _content_rect(capture: dict, expected: dict, viewport: tuple[int, int]):
    """Return the content rectangle in frame coordinates from real DOM rects."""
    vw, vh = viewport
    left = 0
    top = 0
    right = vw
    bottom = vh
    found = {}
    for rect in capture.get('rects', []):
        kind = rect.get('kind')
        found.setdefault(kind, []).append(rect)
    if found.get('left'):
        candidate = max(round(r['x'] + r['width']) for r in found['left'])
        if candidate > 0:
            left = candidate
    if found.get('top'):
        candidate = max(round(r['y'] + r['height']) for r in found['top'])
        if candidate > 0:
            top = candidate
    if found.get('right'):
        candidate = min(round(r['x']) for r in found['right'])
        if candidate < vw:
            right = candidate
    if found.get('bottom'):
        candidate = min(round(r['y']) for r in found['bottom'])
        if candidate < vh:
            bottom = candidate
    return left, top, right, bottom


def _compare(result, truth) -> dict:
    """Strict comparison with explicit penalties for missing/extra rows."""
    width = truth.width
    height = truth.height
    extra_rows = max(0, result.height - height)
    common_width = min(width, result.width)
    total = 0
    exact = 0
    large = 0
    missing_pixels = 0
    extra_pixels = extra_rows * width
    # Width mismatch is rare but should not be silently cropped away.
    width_missing = max(0, width - result.width)
    width_extra = max(0, result.width - width)
    for y in range(max(height, result.height)):
        if y >= height:
            total += 3 * 255 * width
            extra_pixels += width
            continue
        for x in range(width):
            ref = (y * width + x) * 3
            if y >= result.height or x >= result.width:
                err = 3 * 255
                missing_pixels += 1
            else:
                current = (y * result.width + x) * 3
                err = sum(abs(truth.rgb[ref + c] - result.rgb[current + c]) for c in range(3))
            total += err
            exact += int(err == 0)
            large += int(err > 75)
    pixels = width * height
    return {
        'truth_size': [width, height],
        'output_size': [result.width, result.height],
        'width_difference': result.width - width,
        'height_difference': result.height - height,
        'width_missing_pixels': width_missing * height,
        'width_extra_pixels': width_extra * height,
        'missing_pixels': missing_pixels,
        'extra_pixels': extra_pixels,
        'mae_0_255': total / (pixels * 3),
        'exact_fraction': exact / pixels,
        'large_error_fraction': large / pixels,
        'common_width': common_width,
    }


def _pass(metrics: dict, strict: bool = False) -> bool:
    if metrics['output_size'][0] != metrics['truth_size'][0]:
        return False
    tolerance = 0 if strict else 1
    if abs(metrics['height_difference']) > tolerance:
        return False
    if strict:
        # Chrome's subpixel text compositing can differ by a few hundred pixels
        # between scroll positions even with exact geometry; keep this much
        # stricter than the automatic pipeline but not literally byte-exact.
        return metrics['mae_0_255'] <= 0.1 and metrics['exact_fraction'] >= 0.998
    return (
        metrics['mae_0_255'] <= 1.0
        and metrics['exact_fraction'] >= 0.97
        and metrics['missing_pixels'] == 0
    )


def _load_info(root: Path, page: dict) -> dict:
    path = root / 'pages' / page['id'] / 'capture' / 'capture.json'
    return json.loads(path.read_text(encoding='utf-8'))


def _save_image(image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def evaluate_page(root: Path, page: dict, explicit: bool, browser_viewport: bool = False) -> dict:
    from scrollweave import Image, LayeredOptions, Viewport, stitch_layered

    capture = _load_info(root, page)
    viewport = tuple(capture.get('inner') or page['viewport'])
    left, top, right, bottom = _content_rect(capture, page['expected'], viewport)
    scrolls = [int(frame['actual']) for frame in capture['frames']]
    frame_root = root / 'pages' / page['id']
    frames = [Image.open(frame_root / frame['file']) for frame in capture['frames']]
    full = Image.open(frame_root / 'capture' / 'truth_full.png')
    max_scroll = max(scrolls)
    truth_bottom = min(full.height, max_scroll + bottom)
    truth = _crop(full, left, top, right, truth_bottom)
    if truth.width <= 0 or truth.height <= 0:
        raise RuntimeError(
            f'empty truth crop from rect {(left, top, right, bottom)} and height {truth_bottom}'
        )
    record = {
        'id': page['id'],
        'chrome': page['chrome'],
        'viewport': list(viewport),
        'content_rect': [left, top, right, bottom],
        'scrolls': scrolls,
        'truth_size': [truth.width, truth.height],
        'target_pixels': truth.width * truth.height,
    }
    _save_image(truth, frame_root / 'truth_content.png')

    if explicit:
        started = time.perf_counter()
        result = stitch_layered(
            frames,
            scroll=scrolls,
            viewport=Viewport(top=top, bottom=bottom, left=left, right=right),
            options=LayeredOptions(appearance=True, texture_strength=80.0),
        )
        record['known'] = {
            'seconds': round(time.perf_counter() - started, 3),
            'scroll': list(result.scroll),
            'metrics': _compare(result.image, truth),
        }
        record['known']['pass'] = _pass(record['known']['metrics'], strict=True)
        _save_image(result.image, frame_root / 'result_known.png')

    if browser_viewport:
        started = time.perf_counter()
        result = stitch_layered(
            frames,
            viewport=Viewport(top=top, bottom=bottom, left=left, right=right),
            options=LayeredOptions(appearance=True, texture_strength=80.0),
        )
        record['dom'] = {
            'seconds': round(time.perf_counter() - started, 3),
            'scroll': list(result.scroll),
            'viewport': [result.viewport.top, result.viewport.bottom],
            'geometry_mode': result.geometry_mode.value,
            'metrics': _compare(result.image, truth),
        }
        record['dom']['pass'] = _pass(record['dom']['metrics'])
        _save_image(result.image, frame_root / 'result_dom.png')

    started = time.perf_counter()
    result = stitch_layered(
        frames,
        options=LayeredOptions(appearance=True, texture_strength=80.0),
    )
    record['auto'] = {
        'seconds': round(time.perf_counter() - started, 3),
        'scroll': list(result.scroll),
        'viewport': [result.viewport.top, result.viewport.bottom],
        'geometry_mode': result.geometry_mode.value,
        'metrics': _compare(result.image, truth),
    }
    record['auto']['pass'] = _pass(record['auto']['metrics'])
    _save_image(result.image, frame_root / 'result_auto.png')
    (frame_root / 'metrics.json').write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    return record


def _evaluate_worker(args) -> dict:
    root, page, explicit, *rest = args
    browser_viewport = bool(rest[0]) if rest else False
    for path in (str(ROOT), str(ROOT / 'tools')):
        if path not in sys.path:
            sys.path.insert(0, path)
    try:
        return evaluate_page(Path(root), page, explicit, browser_viewport)
    except Exception as error:  # keep the rest of the corpus useful
        return {
            'id': page['id'],
            'chrome': page['chrome'],
            'viewport': page['viewport'],
            'error': str(error),
            'auto': {'pass': False, 'metrics': {}},
        }


def write_report(root: Path, summary: dict) -> None:
    primary = summary.get('primary_name', 'auto')
    primary_pass = summary.get('primary_pass', summary['auto_pass'])
    label = 'browser viewport + automatic positions' if primary == 'dom' else 'automatic pipeline'
    lines = [
        '# ScrollWeave browser smoke report',
        '',
        f'- corpus: **{summary["count"]}** pages',
        f'- {label}: **{primary_pass}/{summary["count"]}**',
    ]
    if primary != 'auto':
        lines.append(f'- pure automatic pipeline: **{summary["auto_pass"]}/{summary["count"]}**')
    if summary.get('known_evaluated'):
        lines.append(
            f'- known geometry + positions: **{summary["known_pass"]}/{summary["known_evaluated"]}**'
        )
    lines += [f'- total run time: {summary["seconds"]:.1f}s', '']
    failures = [p for p in summary['pages'] if not p.get(primary, {}).get('pass')]
    if failures:
        lines += [
            f'## {label.capitalize()} failures',
            '',
            '| page | chrome | viewport | truth | output | MAE | exact | note |',
            '|---|---|---|---|---|---|---|---|',
        ]
        for page in failures:
            if 'error' in page:
                lines.append(
                    f'| {page["id"]} | {"+".join(page["chrome"]) or "none"} | '
                    f'{page["viewport"][0]}x{page["viewport"][1]} | - | - | - | - | '
                    f'{page["error"].replace("|", "/")[:120]} |'
                )
                continue
            item = page.get(primary) or page['auto']
            metrics = item['metrics']
            notes = []
            if metrics['width_difference']:
                notes.append(f'width {metrics["width_difference"]:+d}')
            if metrics['height_difference']:
                notes.append(f'height {metrics["height_difference"]:+d}')
            if metrics['missing_pixels']:
                notes.append(f'missing {metrics["missing_pixels"]}')
            if not notes:
                notes.append('pixels')
            lines.append(
                f'| {page["id"]} | {"+".join(page["chrome"]) or "none"} | '
                f'{page["viewport"][0]}x{page["viewport"][1]} | '
                f'{metrics["truth_size"][0]}x{metrics["truth_size"][1]} | '
                f'{metrics["output_size"][0]}x{metrics["output_size"][1]} | '
                f'{metrics["mae_0_255"]:.3f} | {metrics["exact_fraction"]:.4f} | '
                f'{", ".join(notes)} |'
            )
        lines.append('')
    if summary.get('known_failures'):
        lines += ['## Known-geometry failures', '']
        for page in summary['known_failures']:
            metrics = page['known']['metrics']
            lines.append(
                f'- `{page["id"]}`: {metrics["output_size"]} vs {metrics["truth_size"]}, '
                f'MAE {metrics["mae_0_255"]:.3f}, exact {metrics["exact_fraction"]:.4f}'
            )
        lines.append('')
    lines += [
        '## Method',
        '',
        'Chrome renders each page at a real viewport.  Fixed chrome is removed',
        'from the full-page truth with `visibility:hidden` (layout is unchanged), and',
        'the truth is cropped to the content rectangle measured from the DOM.  Frames',
        'are ordinary viewport screenshots at the recorded scroll positions.  No truth',
        'pixel is used during reconstruction.',
        '',
    ]
    (root / 'report.md').write_text('\n'.join(lines), encoding='utf-8')


def run(
    root: Path,
    count: int,
    only: list[int] | None,
    capture: bool,
    explicit: bool,
    allow_small: bool,
    workers: int = 1,
) -> dict:
    from web_corpus import build_corpus

    if count < 100 and not allow_small:
        raise SystemExit(
            'refusing a smoke dataset below 100 pages (use --allow-small for development)'
        )
    root.mkdir(parents=True, exist_ok=True)
    manifest = build_corpus(root, count)
    pages = manifest['pages']
    if only:
        wanted = set(only)
        pages = [page for page in pages if page['index'] in wanted]
    if capture:
        job = {
            'root': str(root.resolve()),
            'pages': [
                {
                    'id': page['id'],
                    'html': page['html'],
                    'viewport': page['viewport'],
                    'step_fraction': page['step_fraction'],
                    'jitter': page['jitter'],
                    'index': page['index'],
                }
                for page in pages
            ],
        }
        job_path = root / 'capture_jobs.json'
        job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
        env = dict(os.environ)
        if os.environ.get('CHROME'):
            env['CHROME'] = os.environ['CHROME']
        completed = subprocess.run(
            ['node', str(ROOT / 'tools' / 'web_capture.mjs'), str(job_path)],
            cwd=ROOT,
            env=env,
        )
        if completed.returncode != 0:
            raise SystemExit('browser capture failed')
    started = time.perf_counter()
    records = []
    if workers > 1 and len(pages) > 1:
        with ProcessPoolExecutor(max_workers=min(workers, len(pages))) as pool:
            futures = {
                pool.submit(_evaluate_worker, (str(root), page, explicit)): page for page in pages
            }
            completed = {}
            for index, future in enumerate(as_completed(futures), 1):
                page = futures[future]
                record = future.result()
                completed[page['id']] = record
                status = 'ok' if record.get('auto', {}).get('pass') else 'FAIL'
                print(f'[stitch] {index}/{len(pages)} {page["id"]} {status}', flush=True)
                if 'error' in record:
                    print(
                        f'[stitch] {page["id"]} FAILED: {record["error"]}',
                        file=sys.stderr,
                        flush=True,
                    )
            records = [completed[page['id']] for page in pages]
    else:
        for index, page in enumerate(pages, 1):
            print(f'[stitch] {index}/{len(pages)} {page["id"]}', flush=True)
            try:
                records.append(evaluate_page(root, page, explicit))
            except Exception as error:  # keep the rest of the corpus useful
                print(f'[stitch] {page["id"]} FAILED: {error}', file=sys.stderr, flush=True)
                records.append(
                    {
                        'id': page['id'],
                        'chrome': page['chrome'],
                        'viewport': page['viewport'],
                        'error': str(error),
                        'auto': {'pass': False, 'metrics': {}},
                    }
                )
    elapsed = time.perf_counter() - started
    known_evaluated = sum(1 for record in records if 'known' in record)
    summary = {
        'schema': 1,
        'count': len(records),
        'corpus_count': count,
        'seconds': elapsed,
        'auto_pass': sum(1 for record in records if record.get('auto', {}).get('pass')),
        'known_evaluated': known_evaluated,
        'known_pass': sum(1 for record in records if record.get('known', {}).get('pass')),
        'known_failures': [
            record for record in records if 'known' in record and not record['known']['pass']
        ],
        'pages': records,
    }
    (root / 'summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    write_report(root, summary)
    detail = f'auto {summary["auto_pass"]}/{len(records)}'
    if known_evaluated:
        detail += f', known {summary["known_pass"]}/{known_evaluated}'
    print(f'SMOKE {"PASS" if summary["auto_pass"] == len(records) else "FAIL"}: {detail}')
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT / 'output' / 'web_smoke')
    parser.add_argument('--count', type=int, default=120)
    parser.add_argument('--only', help='comma-separated page indices for development')
    parser.add_argument('--no-capture', action='store_true', help='reuse existing captures')
    parser.add_argument('--explicit', action='store_true', help='also evaluate known geometry')
    parser.add_argument('--allow-small', action='store_true')
    parser.add_argument('--workers', type=int, default=1, help='parallel page workers')
    parser.add_argument(
        '--source', action='store_true', help='import scrollweave from this checkout'
    )
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'tools'))
    only = [int(v) for v in args.only.split(',')] if args.only else None
    summary = run(
        args.root,
        args.count,
        only,
        not args.no_capture,
        args.explicit,
        args.allow_small,
        max(1, args.workers),
    )
    return 0 if summary['auto_pass'] == summary['count'] else 1


if __name__ == '__main__':
    sys.exit(main())
