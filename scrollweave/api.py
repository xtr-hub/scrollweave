"""Domain-oriented Python API for the ScrollWeave native engine.

The public surface is intentionally small:

* ``stitch_basic``    - legacy 2D alignment/compositing pipeline
* ``stitch_layered``  - vertical layered reconstruction
* ``plan_views``      - verification/repair planning over a layered result
* ``compose``         - layout without alignment

Advanced algorithm knobs live in option dataclasses, not in the operation
signatures. A result contains only the page plus nested evidence/diagnostics.
"""

from __future__ import annotations

from array import array
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .errors import (
    ImageIOError,
    InvalidArgumentError,
    ScrollWeaveError,
)


class GeometryMode(str, Enum):
    INSUFFICIENT_MOTION = 'insufficient_motion'
    STABLE_BORDERS = 'stable_screen_borders'
    ACTIVITY_ENVELOPE = 'temporal_activity_envelope'


class Segmentation(str, Enum):
    TEMPORAL = 'temporal'
    GRAPH_CUT = 'graphcut'


class Goal(str, Enum):
    VERIFICATION = 'verification'
    REPAIR = 'repair'


@dataclass(frozen=True, slots=True)
class Image:
    """Owned RGB888 frame: width x height x 3 bytes, no NumPy required."""

    width: int
    height: int
    rgb: bytes

    def __post_init__(self):
        if self.width <= 0 or self.height <= 0:
            raise InvalidArgumentError('image dimensions must be positive')
        if self.width > 2**32 - 1 or self.height > 2**32 - 1:
            raise InvalidArgumentError('image dimensions exceed Rust ABI range')
        if not isinstance(self.rgb, bytes):
            object.__setattr__(self, 'rgb', bytes(self.rgb))
        if len(self.rgb) != self.width * self.height * 3:
            raise InvalidArgumentError('invalid RGB888 buffer length')

    @classmethod
    def open(cls, path: str | Path) -> Image:
        from ._binding import read_image

        return read_image(path)

    def save(self, path: str | Path) -> None:
        from ._binding import write_image

        write_image(path, self)

    def __len__(self):
        return len(self.rgb)


@dataclass(frozen=True, slots=True)
class Viewport:
    """Content region in source-frame coordinates; ``bottom/right`` are exclusive."""

    top: int
    bottom: int
    left: int = 0
    right: int | None = None

    def __post_init__(self):
        if self.top < 0 or self.bottom <= self.top:
            raise InvalidArgumentError('viewport must satisfy 0 <= top < bottom')
        if self.left < 0:
            raise InvalidArgumentError('viewport left must be non-negative')
        if self.right is not None and self.right <= self.left:
            raise InvalidArgumentError('viewport right must be greater than left')

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def pair(self) -> tuple[int, int]:
        return self.top, self.bottom

    def bounds(self, width: int) -> tuple[int, int, int, int] | None:
        right = width if self.right is None else self.right
        if self.left >= right or right > width:
            raise InvalidArgumentError('viewport is outside frame bounds')
        if self.left == 0 and right == width:
            return None
        return self.left, self.top, right, self.bottom


@dataclass(frozen=True, slots=True)
class PriorMasks:
    """Per-frame UI priors, one W*H byte mask per frame."""

    width: int
    height: int
    frames: tuple[bytes, ...]

    def __post_init__(self):
        if self.width <= 0 or self.height <= 0:
            raise InvalidArgumentError('mask dimensions must be positive')
        if not self.frames:
            raise InvalidArgumentError('at least one prior mask is required')
        expected = self.width * self.height
        for index, mask in enumerate(self.frames):
            if len(mask) != expected:
                raise InvalidArgumentError(
                    f'prior mask {index} has {len(mask)} bytes; expected {expected}'
                )

    def raw(self) -> bytes:
        return b''.join(self.frames)


@dataclass(frozen=True, slots=True)
class AlignOptions:
    min_overlap: float = 0.18
    min_match_score: float = 0.54
    auto_trim: bool = True

    def __post_init__(self):
        if not 0 < self.min_overlap < 1:
            raise InvalidArgumentError('min_overlap must be in (0, 1)')
        if not 0 < self.min_match_score <= 1:
            raise InvalidArgumentError('min_match_score must be in (0, 1]')


@dataclass(frozen=True, slots=True)
class LayeredOptions:
    appearance: bool = True
    appearance_threshold: float = 0.60
    texture_strength: float = 80.0
    segmentation: Segmentation = Segmentation.TEMPORAL
    include_transients: bool = False
    calibrate_prior: bool = True
    recurrent_mask: PriorMasks | None = None
    flat_mask: PriorMasks | None = None
    training_indices: tuple[int, ...] | None = None

    def __post_init__(self):
        if not 0 <= self.appearance_threshold <= 1:
            raise InvalidArgumentError('appearance_threshold must be in [0, 1]')
        if self.texture_strength < 0:
            raise InvalidArgumentError('texture_strength must be non-negative')
        if not isinstance(self.segmentation, Segmentation):
            object.__setattr__(self, 'segmentation', Segmentation(self.segmentation))
        if self.training_indices is not None and (
            not self.training_indices or any(index < 0 for index in self.training_indices)
        ):
            raise InvalidArgumentError('training_indices must be non-empty and non-negative')


@dataclass(frozen=True, slots=True)
class PlanOptions:
    count: int = 3
    goal: Goal = Goal.REPAIR
    step: int = 32
    minimum_separation: int = 48
    candidates: tuple[int, ...] | None = None
    document_height: int | None = None

    def __post_init__(self):
        if self.count < 0:
            raise InvalidArgumentError('count must be non-negative')
        if self.step <= 0:
            raise InvalidArgumentError('step must be positive')
        if self.minimum_separation < 0:
            raise InvalidArgumentError('minimum_separation must be non-negative')
        if self.document_height is not None and self.document_height <= 0:
            raise InvalidArgumentError('document_height must be positive')
        if not isinstance(self.goal, Goal):
            object.__setattr__(self, 'goal', Goal(self.goal))


@dataclass(frozen=True, slots=True)
class ViewSuggestion:
    scroll_position: int
    estimated_gain: float
    target_pixels: int
    predicted_clean_fraction: float
    goal: Goal


@dataclass(frozen=True, slots=True)
class Evidence:
    """Document-space support and screen-space UI evidence."""

    source_ids: array
    support: array
    views: array
    conflict: bytes
    unverified: bytes
    invalid_source: bytes
    estimated_ui: bytes
    ui_probability: bytes
    rejected_prior_rows: bytes


@dataclass(frozen=True, slots=True)
class Diagnostics:
    coverage: Image | None = None
    confidence: Image | None = None
    provenance: Image | None = None
    screen_mask: Image | None = None


@dataclass(frozen=True, slots=True)
class GraphStats:
    edges: int
    rejected: int
    residual: float


@dataclass(frozen=True, slots=True)
class BasicResult:
    image: Image
    positions: tuple[tuple[int, int] | None, ...]
    viewport: Viewport
    graph: GraphStats
    support_fraction: float
    unverified_fraction: float
    diagnostics: Diagnostics | None = None

    def save(self, path: str | Path) -> None:
        self.image.save(path)

    def save_debug(self, directory: str | Path) -> None:
        _save_debug(self, directory)


@dataclass(frozen=True, slots=True)
class LayeredResult:
    image: Image
    scroll: tuple[int, ...]
    viewport: Viewport
    evidence: Evidence
    geometry_mode: GeometryMode
    support_fraction: float
    unverified_fraction: float
    diagnostics: Diagnostics | None = None
    _native: object | None = field(default=None, repr=False, compare=False)

    def save(self, path: str | Path) -> None:
        self.image.save(path)

    def save_debug(self, directory: str | Path) -> None:
        _save_debug(self, directory)


def _save_debug(result: BasicResult | LayeredResult, directory: str | Path) -> None:
    root = Path(directory)
    try:
        root.mkdir(parents=True, exist_ok=True)
        if result.diagnostics is not None:
            for name in ('coverage', 'confidence', 'provenance', 'screen_mask'):
                image = getattr(result.diagnostics, name)
                if image is not None:
                    image.save(root / f'{name}.png')
        if isinstance(result, LayeredResult):
            evidence = result.evidence
            width, height = result.image.width, result.image.height
            for name in ('conflict', 'unverified', 'invalid_source'):
                values = getattr(evidence, name)
                if len(values) != width * height:
                    continue
                data = bytearray(width * height * 3)
                for index, value in enumerate(values):
                    data[index * 3 : index * 3 + 3] = bytes((255 if value else 0,)) * 3
                Image(width, height, bytes(data)).save(root / f'{name}.png')
            (root / 'source_ids.i32le').write_bytes(evidence.source_ids.tobytes())
        elif result.positions:
            from array import array as _array

            values = _array('i')
            for position in result.positions:
                values.extend(position if position is not None else (-(2**31), -(2**31)))
            (root / 'positions.i32le').write_bytes(values.tobytes())
    except ScrollWeaveError:
        raise
    except OSError as error:
        raise ImageIOError(f'cannot write debug output to {str(directory)!r}: {error}') from error


def _images(values: Sequence[str | Path | Image]) -> list[Image]:
    frames = []
    for index, value in enumerate(values):
        if isinstance(value, Image):
            frames.append(value)
        elif isinstance(value, (str, Path)):
            frames.append(Image.open(value))
        else:
            raise TypeError(
                f'images[{index}] must be an Image or a PNG/BMP/PPM path, '
                f'got {type(value).__name__}'
            )
    return frames


def _viewport(
    value: Viewport | tuple[int, int] | None, height: int, width: int | None = None
) -> Viewport | None:
    if value is None:
        return None
    viewport = value if isinstance(value, Viewport) else Viewport(*value)
    if viewport.bottom > height:
        raise InvalidArgumentError(
            f'viewport bottom {viewport.bottom} exceeds frame height {height}'
        )
    if width is not None and viewport.right is not None and viewport.right > width:
        raise InvalidArgumentError(f'viewport right {viewport.right} exceeds frame width {width}')
    return viewport


def _positions(
    values: Sequence[int | tuple[int, int]] | None,
) -> tuple[tuple[int, int], ...] | None:
    if values is None:
        return None
    result = []
    for index, value in enumerate(values):
        if isinstance(value, int):
            result.append((0, value))
        elif isinstance(value, (tuple, list)) and len(value) == 2:
            result.append((int(value[0]), int(value[1])))
        else:
            raise InvalidArgumentError(
                f'positions[{index}] must be an integer or an (x, y) pair, got {value!r}'
            )
        if any(not (-(2**31) < coordinate < 2**31) for coordinate in result[-1]):
            raise InvalidArgumentError(f'positions[{index}] is outside the signed 32-bit range')
    return tuple(result)


def _scroll(values: Sequence[int]) -> tuple[int, ...]:
    result = []
    for index, value in enumerate(values):
        if isinstance(value, (tuple, list)):
            raise InvalidArgumentError(
                f'scroll[{index}] must be a vertical integer; got {value!r}. '
                'Use the basic API for horizontal/2D positions.'
            )
        result.append(int(value))
    result = tuple(result)
    if any(value < 0 for value in result):
        raise InvalidArgumentError('scroll positions must be non-negative')
    if any(value > 2**31 - 1 for value in result):
        raise InvalidArgumentError('scroll position is outside the signed 32-bit range')
    return result


def stitch_basic(
    images: Sequence[str | Path | Image],
    *,
    positions: Sequence[int | tuple[int, int]] | None = None,
    viewport: Viewport | tuple[int, int] | None = None,
    options: AlignOptions | None = None,
    diagnostics: bool = False,
) -> BasicResult:
    """Run the basic 2D alignment/compositing pipeline."""
    from ._binding import stitch_basic as native_stitch

    options = AlignOptions() if options is None else options
    frames = _images(images)
    if not frames:
        raise InvalidArgumentError('stitch_basic: at least one image is required')
    poses = _positions(positions)
    if poses is not None and len(poses) != len(frames):
        raise InvalidArgumentError(
            f'stitch_basic: positions has {len(poses)} entries but {len(frames)} images were supplied'
        )
    band = _viewport(viewport, frames[0].height, frames[0].width)
    return native_stitch(frames, poses, band, options, diagnostics)


def _crop_frame(image: Image, left: int, top: int, right: int, bottom: int) -> Image:
    return Image(
        right - left,
        bottom - top,
        b''.join(
            image.rgb[(y * image.width + left) * 3 : (y * image.width + right) * 3]
            for y in range(top, bottom)
        ),
    )


def _motion_span(counts, total, threshold, max_gap):
    active = [index for index, value in enumerate(counts) if value / total >= threshold]
    if not active:
        return None
    spans = []
    start = previous = active[0]
    for index in active[1:]:
        if index - previous <= max_gap:
            previous = index
        else:
            spans.append((start, previous))
            start = previous = index
    spans.append((start, previous))
    return max(spans, key=lambda span: (span[1] - span[0], sum(counts[span[0] : span[1] + 1])))


def _detect_inner_scroll_region(frames: Sequence[Image]) -> tuple[int, int, int, int] | None:
    """Find a moving pane surrounded by fixed application chrome."""
    if len(frames) < 2:
        return None
    width, height = frames[0].width, frames[0].height
    if any(frame.width != width or frame.height != height for frame in frames):
        return None
    step = 4
    column_samples = len(range(0, width, step))
    row_samples = len(range(0, height, step))
    row_hits = [0] * height
    column_hits = [0] * width
    for y in range(0, height, step):
        base_row = y * width
        for x in range(0, width, step):
            offset = (base_row + x) * 3
            base = frames[0].rgb[offset : offset + 3]
            changed = False
            for frame in frames[1:]:
                other = frame.rgb[offset : offset + 3]
                if max(abs(base[channel] - other[channel]) for channel in range(3)) > 12:
                    changed = True
                    break
            if changed:
                row_hits[y] += 1
                column_hits[x] += 1
    rows = _motion_span(row_hits, column_samples, 0.02, 48)
    columns = _motion_span(column_hits, row_samples, 0.02, 48)
    if rows is None or columns is None:
        return None
    # The moving pane must be surrounded by fixed chrome. If motion reaches a
    # frame edge, this is a normal full-page/document scroll, not an inner pane.
    if rows[0] < 16 or rows[1] > height - 16 or columns[0] < 16 or columns[1] > width - 16:
        return None
    margin = 12
    top = max(16, rows[0] - margin)
    bottom = min(height - 16, rows[1] + 1 + margin)
    left = max(16, columns[0] - margin)
    right = min(width - 16, columns[1] + 1 + margin)
    if right - left < 32 or bottom - top < 32:
        return None
    area = (right - left) * (bottom - top)
    if area < width * height * 0.05 or area > width * height * 0.92:
        return None
    return left, top, right, bottom


def _estimate_scroll(frames: Sequence[Image], viewport: Viewport | None = None) -> tuple[int, ...]:
    """Estimate vertical positions, optionally on a cropped content pane."""
    from ._binding import stitch_basic as native_stitch

    matching_frames = frames
    if viewport is not None:
        bounds = viewport.bounds(frames[0].width)
        if bounds is not None:
            matching_frames = [_crop_frame(frame, *bounds) for frame in frames]
    estimated = native_stitch(matching_frames, None, None, AlignOptions(), False)
    ys = []
    for index, position in enumerate(estimated.positions):
        if position is None:
            raise InvalidArgumentError(
                f'stitch_layered: could not estimate a position for frame {index}; '
                'pass scroll=[...] explicitly'
            )
        ys.append(int(position[1]))
    if not ys:
        raise InvalidArgumentError('stitch_layered: no frames were supplied')
    origin = min(ys)
    return tuple(y - origin for y in ys)


def stitch_layered(
    images: Sequence[str | Path | Image],
    *,
    scroll: Sequence[int] | None = None,
    viewport: Viewport | tuple[int, int] | None = None,
    options: LayeredOptions | None = None,
    diagnostics: bool = False,
) -> LayeredResult:
    """Reconstruct a vertically scrolling page using layered evidence.

    When no viewport is supplied, a moving pane surrounded by fixed application
    chrome is detected and stitched first. Otherwise the native layer infers the
    page content region. ``scroll`` is optional; explicit positions are useful
    for periodic or heavily occluded pages.
    """
    from ._binding import reconstruct_layered as native_reconstruct

    options = LayeredOptions() if options is None else options
    frames = _images(images)
    if not frames:
        raise InvalidArgumentError('stitch_layered: at least one image is required')
    if len({(frame.width, frame.height) for frame in frames}) != 1:
        sizes = ', '.join(f'{frame.width}x{frame.height}' for frame in frames)
        raise InvalidArgumentError(f'stitch_layered: all frames must share dimensions; got {sizes}')
    if viewport is None:
        region = _detect_inner_scroll_region(frames)
        if region is not None:
            left, top, right, bottom = region
            viewport = Viewport(top=top, bottom=bottom, left=left, right=right)
    band = _viewport(viewport, frames[0].height, frames[0].width)
    positions = _scroll(scroll) if scroll is not None else _estimate_scroll(frames, band)
    if len(positions) != len(frames):
        raise InvalidArgumentError(
            f'stitch_layered: scroll has {len(positions)} entries but {len(frames)} images were supplied'
        )
    for mask in (options.recurrent_mask, options.flat_mask):
        if mask is not None:
            if (mask.width, mask.height) != (frames[0].width, frames[0].height):
                raise InvalidArgumentError(
                    'stitch_layered: prior mask dimensions '
                    f'{mask.width}x{mask.height} do not match frame '
                    f'{frames[0].width}x{frames[0].height}'
                )
            if len(mask.frames) != len(frames):
                raise InvalidArgumentError(
                    f'stitch_layered: prior mask has {len(mask.frames)} frames but {len(frames)} images were supplied'
                )
    return native_reconstruct(frames, positions, band, options, diagnostics)


def plan_views(
    result: LayeredResult, options: PlanOptions | None = None
) -> tuple[ViewSuggestion, ...]:
    """Plan verification or repair views for an existing layered result."""
    options = PlanOptions() if options is None else options
    if not isinstance(result, LayeredResult):
        raise TypeError(f'plan_views expects a LayeredResult, got {type(result).__name__}')
    if result._native is None:
        raise InvalidArgumentError(
            'plan_views: result does not carry a native planning handle; '
            'reconstruct it with stitch_layered first'
        )
    from ._binding import plan_layered

    return plan_layered(result, options)


def compose(
    images: Sequence[str | Path | Image],
    *,
    layout: str = 'vertical',
    columns: int = 2,
    gap: int = 0,
    background: tuple[int, int, int] = (255, 255, 255),
) -> Image:
    """Layout images without alignment."""
    from ._binding import compose as native_compose

    layouts = {'vertical': 0, 'horizontal': 1, 'grid': 2, 'overlay': 3}
    if layout not in layouts:
        raise InvalidArgumentError(
            f'compose: unknown layout {layout!r}; expected vertical, horizontal, grid or overlay'
        )
    if not isinstance(columns, int) or columns <= 0:
        raise InvalidArgumentError('compose: columns must be a positive integer')
    if not isinstance(gap, int) or not 0 <= gap <= 100000:
        raise InvalidArgumentError('compose: gap must be an integer between 0 and 100000')
    if len(background) != 3 or any(not isinstance(v, int) or not 0 <= v <= 255 for v in background):
        raise InvalidArgumentError('compose: background must be an RGB tuple of 0..255 integers')
    frames = _images(images)
    if not frames:
        raise InvalidArgumentError('compose: at least one image is required')
    return native_compose(frames, layouts[layout], columns, gap, tuple(background))


def read_image(path: str | Path) -> Image:
    return Image.open(path)


def write_image(path: str | Path, image: Image) -> None:
    image.save(path)
