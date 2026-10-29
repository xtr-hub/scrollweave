import tempfile
import unittest
from pathlib import Path

from scrollweave import (
    AlignOptions,
    Image,
    LayeredOptions,
    NativeUnavailable,
    PlanOptions,
    PriorMasks,
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


if __name__ == '__main__':
    unittest.main()
