"""Strict FULL-PAGE evaluation of native Rust output, with no scientific Python dependency.

Does not use truth to reconstruct. The truth PNG is read ONLY after the result exists.
Missing rows count as errors and height mismatch is separately reported.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scrollweave import Image, LayeredOptions, PriorMasks, Viewport, stitch_layered


def evaluate(result: Image, truth: Image):
    w, h = truth.width, truth.height
    if result.width != w:
        raise ValueError('width mismatch; compare equal document-coordinate widths')
    accum = 0
    large = 0
    exact = 0
    missing = 0
    for y in range(h):
        for x in range(w):
            a = (y * w + x) * 3
            ref = truth.rgb[a : a + 3]
            if y < result.height:
                c = result.rgb[a : a + 3]
                err = sum(abs(ref[i] - c[i]) for i in range(3))
            else:
                # Explicit penalty for missing image content, not cropping to overlap.
                err = 3 * 255
                missing += 1
            accum += err
            large += int(err > 3 * 25)
            exact += int(err == 0)
    return {
        'truth_size': [w, h],
        'output_size': [result.width, result.height],
        'height_difference': result.height - h,
        'missing_pixels': missing,
        'mae_0_255': accum / (w * h * 3),
        'large_error_fraction': large / (w * h),
        'exact_fraction': exact / (w * h),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', nargs='*')
    parser.add_argument(
        '--suite', help='directory with screenshots, oracle_ground_truth.json, ground_truth.png'
    )
    parser.add_argument('--truth', type=Path)
    parser.add_argument('--positions', help='comma-separated input scroll offsets')
    parser.add_argument('--viewport', help='top:bottom, optional')
    parser.add_argument('--recurrent-mask', type=Path)
    parser.add_argument('--flat-mask', type=Path)
    parser.add_argument('--output', default='native_result.png')
    parser.add_argument('--metrics', default='native_metrics.json')
    args = parser.parse_args()
    files = args.images
    truth = args.truth
    positions = [int(x) for x in args.positions.split(',')] if args.positions else None
    viewport = tuple(int(v) for v in args.viewport.split(':')) if args.viewport else None
    if args.suite:
        root = Path(args.suite)
        info = json.loads((root / 'oracle_ground_truth.json').read_text(encoding='utf-8'))
        files = [root / 'screenshots' / v['file'] for v in info['positions']]
        positions = [int(v['scroll']) for v in info['positions']]
        truth = root / 'ground_truth.png'
        viewport = viewport or (
            90,
            678,
        )  # Published fixture reference; override explicitly if desired.
    if not files or truth is None:
        parser.error('supply --suite or files plus --truth')
    if positions is None:
        parser.error('--positions or --suite is required for layered reconstruction')
    first = Image.open(files[0])
    width, height = first.width, first.height

    def prior(path):
        if path is None:
            return None
        raw = path.read_bytes()
        size = width * height
        frames = tuple(raw[index * size : (index + 1) * size] for index in range(len(files)))
        return PriorMasks(width, height, frames)

    options = LayeredOptions(
        recurrent_mask=prior(args.recurrent_mask),
        flat_mask=prior(args.flat_mask),
    )
    band = Viewport(*viewport) if viewport is not None else None
    result = stitch_layered(files, scroll=positions, viewport=band, options=options)
    result.save(args.output)
    metrics = evaluate(result.image, Image.open(truth))
    metrics.update(
        {
            'support_fraction': result.support_fraction,
            'unverified_fraction': result.unverified_fraction,
            'geometry_mode': result.geometry_mode.value,
            'viewport': list(result.viewport.pair()),
            'scroll': list(result.scroll),
        }
    )
    Path(args.metrics).write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
