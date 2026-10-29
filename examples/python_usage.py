"""Usage with PNG files and optional raw RGB888 input; no third-party Python packages."""

from pathlib import Path

from scrollweave import Image, LayeredOptions, Viewport, compose, stitch_layered

# Example fixture frames (same size, known to overlap).
base = Path(__file__).resolve().parent.parent
files = [base / 'fixtures' / name for name in ('shot_01.png', 'shot_02.png', 'shot_03.png')]

# The bundled fixture pattern is periodic, so image-only alignment can pick a
# content-equivalent alias. Known offsets reconstruct the exact ground-truth page.
reconstruction = stitch_layered(
    files,
    scroll=[0, 90, 180],
    viewport=Viewport(0, 150),
    options=LayeredOptions(appearance=False, texture_strength=0.0),
    diagnostics=True,
)
reconstruction.save('stitched.png')
reconstruction.save_debug('stitch_diagnostics')
print('Result:', reconstruction.image.width, reconstruction.image.height)
print('Scroll positions:', reconstruction.scroll)
print('Support fraction:', reconstruction.support_fraction)

# Simple vertical layout without motion estimation.
compose(files, layout='vertical', gap=0).save('stacked.png')

# Zero dependency bytes interface; for real camera/video input, upstream code
# must decode each video frame into RGB888 and pass Image objects.
red = Image(16, 16, b'\xff\x00\x00' * (16 * 16))
red.save('red.png')
