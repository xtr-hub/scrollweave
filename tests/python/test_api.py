import tempfile
import unittest
from pathlib import Path

from scrollweave import (
    AlignOptions,
    Image,
    ImageFormatError,
    ImageIOError,
    InvalidArgumentError,
    LayeredOptions,
    NativeError,
    NativeUnavailable,
    PlanOptions,
    PriorMasks,
    UnsupportedFormatError,
    Viewport,
    compose,
    stitch_basic,
    stitch_layered,
)
from scrollweave._binding import _load

ROOT = Path(__file__).resolve().parents[2]
try:
    _load()
    HAS_NATIVE = True
except (NativeUnavailable, OSError):
    HAS_NATIVE = False


class PythonInterfaceTests(unittest.TestCase):
    def test_image_rgb_validation(self):
        frame = Image(2, 1, b'\xff\x00\x00' * 2)
        self.assertEqual(len(frame), 6)
        with self.assertRaises(ValueError):
            Image(2, 2, b'abc')
        with self.assertRaises(ValueError):
            Image(0, 1, b'')

    def test_option_validation(self):
        with self.assertRaises(ValueError):
            AlignOptions(min_overlap=1.2)
        with self.assertRaises(ValueError):
            AlignOptions(min_match_score=-1)
        with self.assertRaises(ValueError):
            LayeredOptions(appearance_threshold=2)
        with self.assertRaises(ValueError):
            PlanOptions(count=-1)
        with self.assertRaises(ValueError):
            Viewport(22, 10)
        with self.assertRaises(ValueError):
            PriorMasks(2, 2, (b'abc',))

    def test_stitch_pre_native_validation(self):
        image = Image(1, 1, b'abc')
        with self.assertRaises(ValueError):
            stitch_layered([image], scroll=[0, 2])
        with self.assertRaises(ValueError):
            stitch_layered([image], scroll=[(0, 0)])
        with self.assertRaises(ValueError):
            stitch_basic([image], positions=[0, 2])
        with self.assertRaises(TypeError):
            stitch_basic([object()])

    def test_compose_validation(self):
        image = Image(1, 1, b'abc')
        with self.assertRaises(ValueError):
            compose([image], layout='what')
        with self.assertRaises(ValueError):
            compose([image], background=(1, 2, 300))
        with self.assertRaises(ValueError):
            compose([image], gap=-1)

    def test_exception_hierarchy_and_context(self):
        self.assertTrue(issubclass(InvalidArgumentError, ValueError))
        self.assertTrue(issubclass(ImageIOError, OSError))
        self.assertTrue(issubclass(UnsupportedFormatError, ValueError))
        self.assertTrue(issubclass(ImageFormatError, ValueError))
        self.assertTrue(issubclass(NativeError, RuntimeError))
        image = Image(1, 1, b'abc')
        with self.assertRaises(InvalidArgumentError) as caught:
            stitch_layered([image], scroll=[0], viewport=Viewport(0, 2))
        message = str(caught.exception)
        self.assertIn('viewport bottom 2', message)
        self.assertIn('frame height 1', message)

    def test_auto_scroll_positions(self):
        width, height, frame_height = 100, 240, 120
        page = Image(
            width,
            height,
            bytes(
                (x * 17 + y * 29 + ch * 41 + (x * y) % 67) % 256
                for y in range(height)
                for x in range(width)
                for ch in range(3)
            ),
        )
        frames = [
            Image(
                width,
                frame_height,
                page.rgb[offset * width * 3 : (offset + frame_height) * width * 3],
            )
            for offset in (0, frame_height // 2, frame_height)
        ]
        result = stitch_layered(frames)
        self.assertEqual((result.image.width, result.image.height), (width, height))
        self.assertEqual(result.image.rgb, page.rgb)

    def test_auto_detects_inner_scrolling_pane(self):
        from scrollweave.api import _detect_inner_scroll_region

        width, height = 240, 200
        header, left, right, bottom = 20, 40, 40, 20
        page = Image(
            width,
            300,
            bytes(
                (x * 17 + y * 29 + ch * 41 + (x * y) % 67) % 256
                for y in range(300)
                for x in range(width)
                for ch in range(3)
            ),
        )

        def frame(scroll):
            data = bytearray()
            for y in range(height):
                for x in range(width):
                    if y < header or x < left or x >= width - right or y >= height - bottom:
                        color = ((x * 3 + y * 5) % 60 + 20,) * 3
                    else:
                        source = (scroll + y - header) * width + x
                        offset = source * 3
                        color = page.rgb[offset : offset + 3]
                    data.extend(color)
            return Image(width, height, bytes(data))

        frames = [frame(scroll) for scroll in (0, 50, 100)]
        region = _detect_inner_scroll_region(frames)
        self.assertIsNotNone(region)
        self.assertLessEqual(region[0], left)
        self.assertLessEqual(region[1], header)
        self.assertGreaterEqual(region[2], width - right)
        self.assertGreaterEqual(region[3], height - bottom)
        result = stitch_layered(frames)
        self.assertGreater(result.image.height, height)
        self.assertLess(result.image.width, width)


@unittest.skipUnless(HAS_NATIVE, 'Rust cdylib not built in this environment')
class ActualRustIntegrationTests(unittest.TestCase):
    def test_compose_and_codec(self):
        red = Image(16, 12, b'\xff\x00\x00' * (16 * 12))
        blue = Image(16, 13, b'\x00\x00\xff' * (16 * 13))
        result = compose([red, blue], gap=1)
        self.assertEqual((result.width, result.height), (16, 26))
        self.assertEqual(result.rgb[:3], b'\xff\x00\x00')
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / 'frame.png'
            result.save(file)
            self.assertEqual(Image.open(file), result)

    def test_exact_reconstruction_from_known_positions(self):
        w, h = 32, 80
        source = Image(
            w,
            h,
            bytes(
                (x * 9 + y * 7 + ch * 13) % 256
                for y in range(h)
                for x in range(w)
                for ch in range(3)
            ),
        )
        frames = []
        for offset in [0, 20, 40]:
            source_y = offset * w * 3
            frames.append(Image(w, 40, source.rgb[source_y : source_y + 40 * w * 3]))
        result = stitch_basic(
            frames,
            positions=[0, 20, 40],
            viewport=Viewport(0, 40),
            options=AlignOptions(auto_trim=False),
        )
        self.assertEqual((result.image.width, result.image.height), (w, h))
        self.assertEqual(result.image.rgb, source.rgb)
        self.assertEqual(result.positions, ((0, 0), (0, 20), (0, 40)))
        self.assertEqual(result.viewport, Viewport(0, 40))

    def test_decode_shipped_png(self):
        image = Image.open(ROOT / 'fixtures' / 'compressed_rgb.png')
        self.assertEqual((image.width, image.height), (97, 81))

    def test_image_file_errors_are_contextual(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / 'missing.png'
            with self.assertRaises(ImageIOError) as caught:
                Image.open(missing)
            message = str(caught.exception)
            self.assertIn(str(missing), message)
            self.assertIn('not found', message.lower())

            unsupported = root / 'notes.txt'
            unsupported.write_text('not an image', encoding='utf-8')
            with self.assertRaises(UnsupportedFormatError) as caught:
                Image.open(unsupported)
            self.assertIn(str(unsupported), str(caught.exception))

            corrupt = root / 'corrupt.png'
            corrupt.write_bytes(b'\x89PNG\r\n\x1a\n' + b'bad')
            with self.assertRaises(ImageFormatError) as caught:
                Image.open(corrupt)
            self.assertIn(str(corrupt), str(caught.exception))

            image = Image(2, 2, b'\x00\x00\x00' * 4)
            with self.assertRaises(UnsupportedFormatError) as caught:
                image.save(root / 'frame.webp')
            self.assertIn('frame.webp', str(caught.exception))
            self.assertIn('.png', str(caught.exception))

            with self.assertRaises(ImageIOError) as caught:
                image.save(root / 'missing-dir' / 'frame.png')
            self.assertIn('missing-dir', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
