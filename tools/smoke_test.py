"""End-to-end smoke test for a built ScrollWeave wheel or source checkout.

Usage:
    python tools/smoke_test.py                 # use the installed scrollweave
    python tools/smoke_test.py --source        # import the checkout at the repo root

The test uses only the Python standard library and the shipped fixtures.
It exits non-zero on the first failed check.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_VERSION = '0.9.4'


class SmokeFailure(RuntimeError):
    pass


def check(condition, label, detail=''):
    if condition:
        print(f'[PASS] {label}')
    else:
        suffix = f' ({detail})' if detail else ''
        raise SmokeFailure(f'{label}{suffix}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--source',
        action='store_true',
        help='import scrollweave from this checkout instead of site-packages',
    )
    parser.add_argument('--fixtures', type=Path, default=ROOT / 'fixtures')
    args = parser.parse_args(argv)

    if args.source:
        sys.path.insert(0, str(ROOT))

    from scrollweave import (
        GeometryMode,
        Image,
        LayeredOptions,
        Viewport,
        __version__,
        compose,
        stitch_layered,
    )
    from scrollweave._binding import _load

    print(f'ScrollWeave smoke test: Python {sys.version.split()[0]}, package {__version__}')
    check(__version__ == EXPECTED_VERSION, 'version metadata', __version__)

    native = _load()
    abi = native.sw_api_version()
    check(abi == 5, 'native ABI is v5', str(abi))
    check(hasattr(native, 'sw_layered_reconstruct_rgbs'), 'layered FFI symbol present')
    check(hasattr(native, 'sw_layered_plan'), 'planner FFI symbol present')

    shot_paths = [args.fixtures / name for name in ('shot_01.png', 'shot_02.png', 'shot_03.png')]
    truth_path = args.fixtures / 'expected_long.png'
    for path in [*shot_paths, truth_path]:
        check(path.is_file(), f'fixture exists: {path.name}', str(path))

    frames = [Image.open(path) for path in shot_paths]
    truth = Image.open(truth_path)
    check(
        all((f.width, f.height) == (120, 150) for f in frames),
        'fixture frame geometry',
        str([(f.width, f.height) for f in frames]),
    )

    result = stitch_layered(
        frames,
        scroll=[0, 90, 180],
        viewport=Viewport(0, 150),
        options=LayeredOptions(appearance=False, texture_strength=0.0),
        diagnostics=True,
    )
    check(
        (result.image.width, result.image.height) == (120, 330),
        'stitched page geometry',
        f'{result.image.width}x{result.image.height}',
    )
    check(result.image.rgb == truth.rgb, 'stitched page is pixel-exact')
    check(result.scroll == (0, 90, 180), 'known scroll positions are returned', str(result.scroll))
    check(
        result.geometry_mode == GeometryMode.INSUFFICIENT_MOTION,
        'explicit viewport geometry mode',
        str(result.geometry_mode),
    )

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        page = root / 'page.png'
        result.save(page)
        check(Image.open(page).rgb == truth.rgb, 'PNG save/reload roundtrip')

        debug = root / 'debug'
        result.save_debug(debug)
        for name in ('coverage', 'confidence', 'provenance', 'screen_mask'):
            check((debug / f'{name}.png').is_file(), f'debug output: {name}.png')

        composed = compose(frames, layout='vertical', gap=2)
        check(
            (composed.width, composed.height) == (120, 454),
            'compose geometry',
            f'{composed.width}x{composed.height}',
        )
        combined = root / 'combined.png'
        composed.save(combined)
        check(Image.open(combined).rgb == composed.rgb, 'compose save/reload roundtrip')

        cli_output = root / 'cli.png'
        version_command = [sys.executable, '-m', 'scrollweave', '--version']
        command = [
            sys.executable,
            '-m',
            'scrollweave',
            'stitch',
            *map(str, shot_paths),
            '--positions',
            '0,90,180',
            '--viewport',
            '0:150',
            '--no-appearance',
            '-o',
            str(cli_output),
        ]
        env = dict(os.environ)
        if args.source:
            current = env.get('PYTHONPATH')
            env['PYTHONPATH'] = str(ROOT) + (os.pathsep + current if current else '')
        else:
            env.pop('PYTHONPATH', None)
        version_result = subprocess.run(
            version_command, cwd=root, env=env, capture_output=True, text=True, encoding='utf-8'
        )
        check(
            version_result.returncode == 0
            and version_result.stdout.strip() == f'scrollweave {EXPECTED_VERSION}',
            'CLI version command',
            (version_result.stdout or version_result.stderr).strip()[:300],
        )
        completed = subprocess.run(
            command, cwd=root, env=env, capture_output=True, text=True, encoding='utf-8'
        )
        check(
            completed.returncode == 0,
            'CLI stitch exit code',
            (completed.stderr or completed.stdout).strip()[:300],
        )
        check(Image.open(cli_output).rgb == truth.rgb, 'CLI stitch output is pixel-exact')

    try:
        stitch_layered(frames, scroll=[0, 90], viewport=Viewport(0, 150))
    except ValueError:
        check(True, 'invalid scroll count is rejected')
    else:
        check(False, 'invalid scroll count is rejected')

    print('SMOKE PASS')


if __name__ == '__main__':
    try:
        main()
    except SmokeFailure as error:
        print(f'SMOKE FAIL: {error}', file=sys.stderr)
        sys.exit(1)
