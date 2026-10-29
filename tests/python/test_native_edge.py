"""Native end-to-end tests for the layered API, planner, debug output and CLI."""

import io
import tempfile
import unittest
from array import array
from contextlib import redirect_stdout
from pathlib import Path

from scrollweave import (
    GeometryMode,
    Goal,
    Image,
    LayeredOptions,
    NativeUnavailable,
    PlanOptions,
    PriorMasks,
    Viewport,
    plan_views,
    stitch_layered,
)
from scrollweave._binding import _load

try:
    _load()
    HAS_NATIVE = True
except (NativeUnavailable, OSError):
    HAS_NATIVE = False


def page_rgb(w=20, h=80):
    return bytes(
        (x * 17 + y * 29 + ch * 41 + (x * y) % 67) % 256
        for y in range(h)
        for x in range(w)
        for ch in range(3)
    )


def page_image(w=20, h=80):
    return Image(w, h, page_rgb(w, h))


def frames_of(page, offsets, frame_height=40):
    return [
        Image(
            page.width,
            frame_height,
            page.rgb[o * page.width * 3 : (o + frame_height) * page.width * 3],
        )
        for o in offsets
    ]


@unittest.skipUnless(HAS_NATIVE, 'Rust cdylib not built in this environment')
class NativeLayeredContractTests(unittest.TestCase):
    def stitch(self, **kwargs):
        page = page_image()
        frames = frames_of(page, [0, 20, 40])
        result = stitch_layered(
            frames,
            scroll=[0, 20, 40],
            viewport=Viewport(0, 40),
            options=LayeredOptions(appearance=False, texture_strength=0.0),
            **kwargs,
        )
        return page, result

    def test_result_contract(self):
        page, result = self.stitch()
        self.assertEqual(result.image.rgb, page.rgb)
        self.assertEqual(result.scroll, (0, 20, 40))
        self.assertEqual(result.viewport, Viewport(0, 40))
        self.assertEqual(result.geometry_mode, GeometryMode.INSUFFICIENT_MOTION)

        pixels = page.width * page.height
        evidence = result.evidence
        self.assertIsInstance(evidence.source_ids, array)
        self.assertEqual(evidence.source_ids.typecode, 'i')
        self.assertEqual(len(evidence.source_ids), pixels)
        self.assertEqual(evidence.source_ids[0], 0)
        self.assertEqual(evidence.source_ids[-1], 2)
        self.assertTrue(all(v in (-1, 0, 1, 2) for v in evidence.source_ids))

        self.assertIsInstance(evidence.support, array)
        self.assertEqual(evidence.support.typecode, 'H')
        self.assertEqual(evidence.views.typecode, 'H')
        self.assertEqual(len(evidence.support), pixels)
        self.assertEqual(len(evidence.views), pixels)

        for name in ('conflict', 'unverified', 'invalid_source'):
            value = getattr(evidence, name)
            self.assertIsInstance(value, bytes, name)
            self.assertEqual(len(value), pixels, name)
        for name in ('estimated_ui', 'ui_probability'):
            value = getattr(evidence, name)
            self.assertIsInstance(value, bytes, name)
            self.assertEqual(len(value), 3 * 40 * page.width, name)
        self.assertEqual(len(evidence.rejected_prior_rows), 40)
        self.assertGreaterEqual(result.support_fraction, 0.0)
        self.assertLessEqual(result.support_fraction, 1.0)
        self.assertEqual(result.unverified_fraction, sum(evidence.unverified) / pixels)

    def test_planner_results_and_debug_files(self):
        page, result = self.stitch(diagnostics=True)
        self.assertEqual(result.image.rgb, page.rgb)
        verification = plan_views(
            result,
            PlanOptions(
                count=1,
                goal=Goal.VERIFICATION,
                candidates=(10,),
                document_height=80,
                minimum_separation=0,
                step=5,
            ),
        )
        repair = plan_views(
            result,
            PlanOptions(
                count=1,
                goal=Goal.REPAIR,
                candidates=(10,),
                document_height=80,
                minimum_separation=0,
                step=5,
            ),
        )
        self.assertEqual(len(verification), 1)
        self.assertEqual(len(repair), 1)
        for view in verification:
            self.assertEqual(view.goal, Goal.VERIFICATION)
            self.assertEqual(view.scroll_position, 10)
            self.assertGreater(view.estimated_gain, 0.0)
            self.assertGreater(view.target_pixels, 0)
        raw = [
            {'scroll_position': view.scroll_position, 'goal': view.goal.value}
            for view in verification + repair
        ]
        self.assertEqual({entry['goal'] for entry in raw}, {'verification', 'repair'})

        with tempfile.TemporaryDirectory() as directory:
            result.save_debug(directory)
            root = Path(directory)
            for name in (
                'coverage',
                'confidence',
                'provenance',
                'screen_mask',
                'conflict',
                'unverified',
                'invalid_source',
            ):
                self.assertTrue((root / f'{name}.png').is_file(), name)
            ids = (root / 'source_ids.i32le').read_bytes()
            self.assertEqual(len(ids), page.width * page.height * 4)

    def test_prior_and_plan_validation(self):
        page = page_image()
        frames = frames_of(page, [0, 20, 40])
        with self.assertRaises(ValueError):
            stitch_layered(frames, scroll=[0, 20])
        with self.assertRaises(ValueError):
            stitch_layered(
                frames, scroll=[0, 20, 40], options=LayeredOptions(training_indices=(3,))
            )
        with self.assertRaises(ValueError):
            PriorMasks(20, 40, (b'\x00',))
        with self.assertRaises(ValueError):
            PlanOptions(count=-1)
        with self.assertRaises(ValueError):
            PlanOptions(step=0)


@unittest.skipUnless(HAS_NATIVE, 'Rust cdylib not built in this environment')
class NativeCodecAndCliTests(unittest.TestCase):
    def test_bmp_and_ppm_roundtrip_through_python(self):
        image = Image(13, 11, bytes((i * 37) % 256 for i in range(13 * 11 * 3)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for suffix in ('.bmp', '.ppm'):
                path = root / f'frame{suffix}'
                image.save(path)
                self.assertEqual(Image.open(path), image)
            with self.assertRaises(RuntimeError):
                image.save(root / 'frame.jpg')

    def test_cli_stitch_and_compose(self):
        from scrollweave.__main__ import main

        page = page_image()
        frames = frames_of(page, [0, 20, 40])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = []
            for k, frame in enumerate(frames):
                path = root / f'shot_{k}.png'
                frame.save(path)
                files.append(str(path))
            stitched = root / 'stitched.png'
            debug = root / 'debug'
            with redirect_stdout(io.StringIO()):
                main(
                    [
                        'stitch',
                        *files,
                        '-o',
                        str(stitched),
                        '--positions',
                        '0,20,40',
                        '--viewport',
                        '0:40',
                        '--no-appearance',
                        '--debug-dir',
                        str(debug),
                    ]
                )
            self.assertEqual(Image.open(stitched).rgb, page.rgb)
            self.assertTrue((debug / 'coverage.png').is_file())

            combined = root / 'combined.png'
            with redirect_stdout(io.StringIO()):
                main(
                    [
                        'compose',
                        files[0],
                        files[1],
                        '-o',
                        str(combined),
                        '--layout',
                        'vertical',
                        '--gap',
                        '2',
                    ]
                )
            image = Image.open(combined)
            self.assertEqual((image.width, image.height), (page.width, 82))


if __name__ == '__main__':
    unittest.main()
