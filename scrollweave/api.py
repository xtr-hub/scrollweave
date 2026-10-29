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
            raise ValueError('image dimensions must be positive')
        if self.width > 2**32 - 1 or self.height > 2**32 - 1:
            raise ValueError('image dimensions exceed Rust ABI range')
        if not isinstance(self.rgb, bytes):
            object.__setattr__(self, 'rgb', bytes(self.rgb))
        if len(self.rgb) != self.width * self.height * 3:
            raise ValueError('invalid RGB888 buffer length')

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
            raise ValueError('viewport must satisfy 0 <= top < bottom')
        if self.left < 0:
            raise ValueError('viewport left must be non-negative')
        if self.right is not None and self.right <= self.left:
            raise ValueError('viewport right must be greater than left')

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def pair(self) -> tuple[int, int]:
        return self.top, self.bottom

    def bounds(self, width: int) -> tuple[int, int, int, int] | None:
        right = width if self.right is None else self.right
        if self.left >= right or right > width:
            raise ValueError('viewport is outside frame bounds')
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
            raise ValueError('mask dimensions must be positive')
        if not self.frames:
            raise ValueError('at least one prior mask is required')
        expected = self.width * self.height
        for index, mask in enumerate(self.frames):
            if len(mask) != expected:
                raise ValueError(f'prior mask {index} has {len(mask)} bytes; expected {expected}')

    def raw(self) -> bytes:
        return b''.join(self.frames)


@dataclass(frozen=True, slots=True)
class AlignOptions:
    min_overlap: float = 0.18
    min_match_score: float = 0.54
    auto_trim: bool = True

    def __post_init__(self):
        if not 0 < self.min_overlap < 1:
            raise ValueError('min_overlap must be in (0, 1)')
        if not 0 < self.min_match_score <= 1:
            raise ValueError('min_match_score must be in (0, 1]')


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
            raise ValueError('appearance_threshold must be in [0, 1]')
        if self.texture_strength < 0:
            raise ValueError('texture_strength must be non-negative')
        if not isinstance(self.segmentation, Segmentation):
            object.__setattr__(self, 'segmentation', Segmentation(self.segmentation))
        if self.training_indices is not None and (
            not self.training_indices or any(index < 0 for index in self.training_indices)
        ):
            raise ValueError('training_indices must be non-empty and non-negative')


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
            raise ValueError('count must be non-negative')
        if self.step <= 0:
            raise ValueError('step must be positive')
        if self.minimum_separation < 0:
            raise ValueError('minimum_separation must be non-negative')
        if self.document_height is not None and self.document_height <= 0:
            raise ValueError('document_height must be positive')
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


def _images(values: Sequence[str | Path | Image]) -> list[Image]:
    frames = []
    for value in values:
        if isinstance(value, Image):
            frames.append(value)
        elif isinstance(value, (str, Path)):
            frames.append(Image.open(value))
        else:
            raise TypeError('expected Image objects or PNG/BMP/PPM file paths')
    return frames


def _viewport(value: Viewport | tuple[int, int] | None, height: int) -> Viewport | None:
    if value is None:
        return None
    viewport = value if isinstance(value, Viewport) else Viewport(*value)
    if viewport.bottom > height:
        raise ValueError('viewport bottom exceeds frame height')
    return viewport


def _positions(
    values: Sequence[int | tuple[int, int]] | None,
) -> tuple[tuple[int, int], ...] | None:
    if values is None:
        return None
    result = []
    for value in values:
        if isinstance(value, int):
            result.append((0, value))
        elif len(value) == 2:
            result.append((int(value[0]), int(value[1])))
        else:
            raise ValueError('each position must be y or (x, y)')
        if any(not (-(2**31) < coordinate < 2**31) for coordinate in result[-1]):
            raise ValueError('position is outside i32 range')
    return tuple(result)


def _scroll(values: Sequence[int]) -> tuple[int, ...]:
    result = []
    for value in values:
        if isinstance(value, (tuple, list)):
            raise ValueError('layered reconstruction accepts vertical scroll integers only')
        result.append(int(value))
    result = tuple(result)
    if any(value < 0 for value in result):
        raise ValueError('scroll positions must be non-negative')
    if any(value > 2**31 - 1 for value in result):
        raise ValueError('scroll position is outside i32 range')
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
        raise ValueError('at least one image is needed')
    poses = _positions(positions)
    if poses is not None and len(poses) != len(frames):
        raise ValueError('positions length must equal images length')
    band = _viewport(viewport, frames[0].height)
    return native_stitch(frames, poses, band, options, diagnostics)


def stitch_layered(
    images: Sequence[str | Path | Image],
    *,
    scroll: Sequence[int],
    viewport: Viewport | tuple[int, int] | None = None,
    options: LayeredOptions | None = None,
    diagnostics: bool = False,
) -> LayeredResult:
    """Reconstruct a vertically scrolling page using layered evidence."""
    from ._binding import reconstruct_layered as native_reconstruct

    options = LayeredOptions() if options is None else options
    frames = _images(images)
    if not frames:
        raise ValueError('at least one image is needed')
    if len({(frame.width, frame.height) for frame in frames}) != 1:
        raise ValueError('all frames must share dimensions')
    positions = _scroll(scroll)
    if len(positions) != len(frames):
        raise ValueError('one scroll position is required per frame')
    band = _viewport(viewport, frames[0].height)
    for mask in (options.recurrent_mask, options.flat_mask):
        if mask is not None:
            if (mask.width, mask.height) != (frames[0].width, frames[0].height):
                raise ValueError('prior mask dimensions must match frames')
            if len(mask.frames) != len(frames):
                raise ValueError('one prior mask per frame is required')
    return native_reconstruct(frames, positions, band, options, diagnostics)


def plan_views(
    result: LayeredResult, options: PlanOptions | None = None
) -> tuple[ViewSuggestion, ...]:
    """Plan verification or repair views for an existing layered result."""
    options = PlanOptions() if options is None else options
    if not isinstance(result, LayeredResult):
        raise TypeError('plan_views expects a LayeredResult')
    if result._native is None:
        raise ValueError('result does not carry a native planning handle')
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
        raise ValueError('layout must be vertical/horizontal/grid/overlay')
    if not isinstance(columns, int) or columns <= 0:
        raise ValueError('columns must be positive')
    if not isinstance(gap, int) or not 0 <= gap <= 100000:
        raise ValueError('gap must be between 0 and 100000')
    if len(background) != 3 or any(not isinstance(v, int) or not 0 <= v <= 255 for v in background):
        raise ValueError('background must be an RGB tuple')
    frames = _images(images)
    if not frames:
        raise ValueError('at least one image is needed')
    return native_compose(frames, layouts[layout], columns, gap, tuple(background))


def read_image(path: str | Path) -> Image:
    return Image.open(path)


def write_image(path: str | Path, image: Image) -> None:
    image.save(path)
