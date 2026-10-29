"""Rust ABI binding via the Python standard library (ctypes)."""

from __future__ import annotations

import ctypes as c
import os
import sys
import threading
from contextlib import suppress
from pathlib import Path


class NativeUnavailable(RuntimeError):
    """The zero-dependency Rust shared library has not been built or installed."""


class _Ref(c.Structure):
    _fields_ = [
        ('width', c.c_uint32),
        ('height', c.c_uint32),
        ('data', c.POINTER(c.c_uint8)),
        ('len', c.c_size_t),
    ]


class _Owned(c.Structure):
    _fields_ = [
        ('width', c.c_uint32),
        ('height', c.c_uint32),
        ('data', c.POINTER(c.c_uint8)),
        ('len', c.c_size_t),
    ]


class _Config(c.Structure):
    _fields_ = [
        ('level', c.c_uint8),
        ('auto_trim', c.c_uint8),
        ('diagnostics', c.c_uint8),
        ('reserved', c.c_uint8),
        ('viewport_top', c.c_int32),
        ('viewport_bottom', c.c_int32),
        ('known_positions', c.POINTER(c.c_int32)),
        ('min_overlap', c.c_double),
        ('min_match_score', c.c_double),
    ]


class _Stats(c.Structure):
    _fields_ = [
        ('viewport_top', c.c_size_t),
        ('viewport_bottom', c.c_size_t),
        ('graph_edges', c.c_size_t),
        ('graph_rejected', c.c_size_t),
        ('graph_residual', c.c_double),
        ('independent_support_ratio', c.c_double),
        ('unknown_ratio', c.c_double),
    ]


class _Response(c.Structure):
    _fields_ = [
        (n, _Owned) for n in ('image', 'coverage', 'confidence', 'provenance', 'screen_mask')
    ] + [('positions', c.POINTER(c.c_int32)), ('positions_len', c.c_size_t), ('stats', _Stats)]


def _library_name() -> str:
    if sys.platform.startswith('win'):
        return 'scrollweave_ffi.dll'
    if sys.platform == 'darwin':
        return 'libscrollweave_ffi.dylib'
    return 'libscrollweave_ffi.so'


_DLL = None
_LOAD_LOCK = threading.Lock()


def _load():
    global _DLL
    if _DLL is not None:
        return _DLL
    with _LOAD_LOCK:
        if _DLL is not None:
            return _DLL
        root = Path(__file__).resolve().parent.parent
        candidates = []
        configured = os.environ.get('SCROLLWEAVE_LIBRARY')
        if configured:
            candidates.append(Path(configured))
        candidates.extend(
            (
                Path(__file__).resolve().parent / '_native' / _library_name(),
                root / 'target' / 'release' / _library_name(),
                root / 'target' / 'debug' / _library_name(),
            )
        )
        for location in candidates:
            if location.is_file():
                lib = c.CDLL(str(location))
                _configure(lib)
                _DLL = lib
                return lib
    raise NativeUnavailable(
        'Rust core is not compiled. Run `pip install .` (Rust/Cargo required) '
        'or `cargo build --release --lib` in the project directory. '
        'Expected ' + _library_name()
    )


def _configure(lib):
    err = [c.c_void_p, c.c_size_t]
    lib.sw_api_version.restype = c.c_uint32
    if lib.sw_api_version() != 4:
        raise NativeUnavailable('ScrollWeave Rust ABI version mismatch (v4 required)')
    lib.sw_read_image.argtypes = [c.c_char_p, c.POINTER(_Owned), *err]
    lib.sw_read_image.restype = c.c_int32
    lib.sw_write_image.argtypes = [c.POINTER(_Ref), c.c_char_p, *err]
    lib.sw_write_image.restype = c.c_int32
    lib.sw_compose_rgbs.argtypes = [
        c.POINTER(_Ref),
        c.c_size_t,
        c.c_uint32,
        c.c_size_t,
        c.c_size_t,
        c.c_uint8,
        c.c_uint8,
        c.c_uint8,
        c.POINTER(_Owned),
        *err,
    ]
    lib.sw_compose_rgbs.restype = c.c_int32
    lib.sw_stitch_rgbs.argtypes = [
        c.POINTER(_Ref),
        c.c_size_t,
        c.POINTER(_Config),
        c.POINTER(_Response),
        *err,
    ]
    lib.sw_stitch_rgbs.restype = c.c_int32
    lib.sw_free_image.argtypes = [c.POINTER(_Owned)]
    lib.sw_free_image.restype = None
    lib.sw_free_stitch_response.argtypes = [c.POINTER(_Response)]
    lib.sw_free_stitch_response.restype = None
    lib.sw_layered_reconstruct_rgbs.argtypes = [
        c.POINTER(_Ref),
        c.c_size_t,
        c.POINTER(_LayeredConfig),
        c.POINTER(_LayeredResponse),
        c.c_void_p,
        c.c_size_t,
    ]
    lib.sw_layered_reconstruct_rgbs.restype = c.c_int32
    lib.sw_layered_free_response.argtypes = [c.POINTER(_LayeredResponse)]
    lib.sw_layered_free_response.restype = None
    lib.sw_layered_plan.argtypes = [
        c.c_void_p,
        c.POINTER(_PlanConfig),
        c.POINTER(_Views),
        c.c_void_p,
        c.c_size_t,
    ]
    lib.sw_layered_plan.restype = c.c_int32
    lib.sw_layered_free_plan.argtypes = [c.c_void_p]
    lib.sw_layered_free_plan.restype = None
    lib.sw_layered_free_views.argtypes = [c.POINTER(_Views)]
    lib.sw_layered_free_views.restype = None


def _check(status, error):
    if status != 0:
        raise RuntimeError(error.value.decode('utf-8', errors='replace') or 'Rust core failed')


def _path(value):
    s = os.fspath(value)
    if isinstance(s, bytes):
        s = os.fsdecode(s)
    return os.path.abspath(s).encode('utf-8')


def _copy_owned(im):
    if not im.data or not im.len:
        return None
    from .api import Image

    return Image(im.width, im.height, c.string_at(im.data, im.len))


def _refs(images):
    if not (1 <= len(images) <= 128):
        raise ValueError('Expected 1..128 input images')
    slots = (_Ref * len(images))()
    pinned = []
    for k, image in enumerate(images):
        data = image.rgb
        pinned.append(data)
        # from_buffer_copy keeps buffers stable for the duration of the native call.
        buffer = (c.c_uint8 * len(data)).from_buffer_copy(data)
        pinned.append(buffer)
        slots[k] = _Ref(image.width, image.height, c.cast(buffer, c.POINTER(c.c_uint8)), len(data))
    return slots, pinned


def read_image(path):
    lib = _load()
    out = _Owned()
    err = c.create_string_buffer(4096)
    _check(lib.sw_read_image(_path(path), c.byref(out), err, len(err)), err)
    try:
        return _copy_owned(out)
    finally:
        lib.sw_free_image(c.byref(out))


def write_image(path, image):
    lib = _load()
    refs, pinned = _refs([image])
    err = c.create_string_buffer(4096)
    _check(lib.sw_write_image(c.byref(refs[0]), _path(path), err, len(err)), err)


def compose(images, layout, columns, gap, background):
    lib = _load()
    refs, pinned = _refs(images)
    out = _Owned()
    err = c.create_string_buffer(4096)
    _check(
        lib.sw_compose_rgbs(
            refs, len(images), layout, columns, gap, *background, c.byref(out), err, len(err)
        ),
        err,
    )
    try:
        return _copy_owned(out)
    finally:
        lib.sw_free_image(c.byref(out))


def stitch_basic(images, positions, viewport, options, diagnostics):
    lib = _load()
    refs, pinned = _refs(images)
    known = None
    if positions is not None:
        values = [int(value) for pair in positions for value in pair]
        known = (c.c_int32 * len(values))(*values)
    top, bottom = (-1, -1) if viewport is None else viewport.pair()
    config = _Config(
        2,
        bool(options.auto_trim),
        bool(diagnostics),
        0,
        top,
        bottom,
        c.cast(known, c.POINTER(c.c_int32)) if known is not None else None,
        options.min_overlap,
        options.min_match_score,
    )
    out = _Response()
    err = c.create_string_buffer(4096)
    _check(lib.sw_stitch_rgbs(refs, len(images), c.byref(config), c.byref(out), err, len(err)), err)
    try:
        from .api import BasicResult, Diagnostics, GraphStats, Viewport

        poses = []
        for k in range(out.positions_len):
            xy = (out.positions[k * 2], out.positions[k * 2 + 1])
            poses.append(None if xy[0] == -(2**31) else xy)
        stats = out.stats
        debug = None
        if diagnostics:
            debug = Diagnostics(
                coverage=_copy_owned(out.coverage),
                confidence=_copy_owned(out.confidence),
                provenance=_copy_owned(out.provenance),
                screen_mask=_copy_owned(out.screen_mask),
            )
        return BasicResult(
            image=_copy_owned(out.image),
            positions=tuple(poses),
            viewport=Viewport(stats.viewport_top, stats.viewport_bottom),
            graph=GraphStats(stats.graph_edges, stats.graph_rejected, stats.graph_residual),
            support_fraction=stats.independent_support_ratio,
            unverified_fraction=stats.unknown_ratio,
            diagnostics=debug,
        )
    finally:
        lib.sw_free_stitch_response(c.byref(out))


# Layered binary ABI: original C ABI remains present; new responses are owned by Rust.
class _MaskRef(c.Structure):
    _fields_ = [('data', c.POINTER(c.c_uint8)), ('len', c.c_size_t)]


class _LayeredConfig(c.Structure):
    _fields_ = [
        ('positions', c.POINTER(c.c_int32)),
        ('viewport_top', c.c_int32),
        ('viewport_bottom', c.c_int32),
        ('appearance', c.c_uint8),
        ('segmentation', c.c_uint8),
        ('include_transients', c.c_uint8),
        ('calibrate_prior', c.c_uint8),
        ('appearance_threshold', c.c_float),
        ('texture_strength', c.c_float),
        ('recurrent_mask', _MaskRef),
        ('flat_mask', _MaskRef),
        ('training_indices', c.POINTER(c.c_uint32)),
        ('training_count', c.c_size_t),
    ]


class _PlanConfig(c.Structure):
    _fields_ = [
        ('goal', c.c_uint32),
        ('count', c.c_size_t),
        ('step', c.c_size_t),
        ('minimum_separation', c.c_int32),
        ('candidates', c.POINTER(c.c_int32)),
        ('candidate_count', c.c_size_t),
        ('document_height', c.c_size_t),
    ]


class _I32(c.Structure):
    _fields_ = [('data', c.POINTER(c.c_int32)), ('len', c.c_size_t)]


class _U16(c.Structure):
    _fields_ = [('data', c.POINTER(c.c_uint16)), ('len', c.c_size_t)]


class _U8(c.Structure):
    _fields_ = [('data', c.POINTER(c.c_uint8)), ('len', c.c_size_t)]


class _View(c.Structure):
    _fields_ = [
        ('scroll_position', c.c_int32),
        ('estimated_gain', c.c_double),
        ('target_pixels', c.c_size_t),
        ('predicted_clean_fraction', c.c_double),
        ('goal', c.c_uint32),
    ]


class _Views(c.Structure):
    _fields_ = [('data', c.POINTER(_View)), ('len', c.c_size_t)]


class _LayeredResponse(c.Structure):
    _fields_ = [
        (name, _Owned) for name in ('image', 'coverage', 'confidence', 'provenance', 'screen_mask')
    ] + [
        ('source_ids', _I32),
        ('independent_support', _U16),
        ('independent_views', _U16),
        ('conflict', _U8),
        ('unverified', _U8),
        ('invalid_source', _U8),
        ('ui_mask', _U8),
        ('ui_probability', _U8),
        ('rejected_prior_rows', _U8),
        ('viewport_top', c.c_size_t),
        ('viewport_bottom', c.c_size_t),
        ('geometry_mode', c.c_uint32),
        ('handle', c.c_void_p),
    ]


class _LayeredHandle:
    """Owns a native PlannerInput handle for later planning calls."""

    __slots__ = ('_lib', 'ptr')

    def __init__(self, lib, ptr):
        self._lib = lib
        self.ptr = ptr

    def close(self):
        pointer = self.ptr
        self.ptr = None
        if pointer:
            with suppress(Exception):
                self._lib.sw_layered_free_plan(pointer)

    def __del__(self):
        self.close()


def _layered_run(images, positions, viewport, options, diagnostics):
    lib = _load()
    if lib.sw_api_version() != 4:
        raise NativeUnavailable('layered Rust reconstruction requires ABI version 4')
    from .api import (
        Diagnostics,
        Evidence,
        GeometryMode,
        LayeredOptions,
        LayeredResult,
        Segmentation,
        Viewport,
    )

    if not isinstance(options, LayeredOptions):
        raise TypeError('options must be LayeredOptions')
    if not images:
        raise ValueError('at least one frame is required')
    if len(positions) != len(images):
        raise ValueError('one vertical scroll position required per input frame')
    if len({(image.width, image.height) for image in images}) != 1:
        raise ValueError('all frames must share dimensions')
    n = len(images)
    width, height = images[0].width, images[0].height
    poses = (c.c_int32 * n)(*[int(x) for x in positions])
    refs, pinned = _refs(images)
    buf_pins = []

    def mask(value):
        if value is None:
            return _MaskRef(None, 0)
        raw = value.raw()
        if len(raw) != n * width * height:
            raise ValueError('UI prior mask must contain N * H * W bytes')
        buffer = (c.c_uint8 * len(raw)).from_buffer_copy(raw)
        buf_pins.append(buffer)
        return _MaskRef(c.cast(buffer, c.POINTER(c.c_uint8)), len(raw))

    recurrence = mask(options.recurrent_mask)
    flat = mask(options.flat_mask)

    train = None
    training_values = []
    if options.training_indices is not None:
        training_values = [int(value) for value in options.training_indices]
        if not training_values or any(value < 0 or value >= n for value in training_values):
            raise ValueError('training_indices must select existing frames')
        train = (c.c_uint32 * len(training_values))(*training_values)

    top, bottom = (-1, -1) if viewport is None else viewport.pair()
    config = _LayeredConfig(
        poses,
        top,
        bottom,
        bool(options.appearance),
        int(options.segmentation == Segmentation.GRAPH_CUT),
        bool(options.include_transients),
        bool(options.calibrate_prior),
        float(options.appearance_threshold),
        float(options.texture_strength),
        recurrence,
        flat,
        train,
        len(training_values),
    )
    response = _LayeredResponse()
    error = c.create_string_buffer(4096)
    _check(
        lib.sw_layered_reconstruct_rgbs(
            refs, n, c.byref(config), c.byref(response), error, len(error)
        ),
        error,
    )
    try:
        from array import array

        def numbers(pointer, typecode):
            values = array(typecode)
            if pointer.len:
                values.frombytes(c.string_at(pointer.data, pointer.len * values.itemsize))
            return values

        support = numbers(response.independent_support, 'H')
        unverified = (
            bytes(c.string_at(response.unverified.data, response.unverified.len))
            if response.unverified.len
            else b''
        )
        evidence = Evidence(
            source_ids=numbers(response.source_ids, 'i'),
            support=support,
            views=numbers(response.independent_views, 'H'),
            conflict=(
                bytes(c.string_at(response.conflict.data, response.conflict.len))
                if response.conflict.len
                else b''
            ),
            unverified=unverified,
            invalid_source=(
                bytes(c.string_at(response.invalid_source.data, response.invalid_source.len))
                if response.invalid_source.len
                else b''
            ),
            estimated_ui=(
                bytes(c.string_at(response.ui_mask.data, response.ui_mask.len))
                if response.ui_mask.len
                else b''
            ),
            ui_probability=(
                bytes(c.string_at(response.ui_probability.data, response.ui_probability.len))
                if response.ui_probability.len
                else b''
            ),
            rejected_prior_rows=(
                bytes(
                    c.string_at(response.rejected_prior_rows.data, response.rejected_prior_rows.len)
                )
                if response.rejected_prior_rows.len
                else b''
            ),
        )
        debug = None
        if diagnostics:
            debug = Diagnostics(
                coverage=_copy_owned(response.coverage),
                confidence=_copy_owned(response.confidence),
                provenance=_copy_owned(response.provenance),
                screen_mask=_copy_owned(response.screen_mask),
            )
        modes = (
            GeometryMode.INSUFFICIENT_MOTION,
            GeometryMode.STABLE_BORDERS,
            GeometryMode.ACTIVITY_ENVELOPE,
        )
        result = LayeredResult(
            image=_copy_owned(response.image),
            scroll=tuple(int(value) for value in positions),
            viewport=Viewport(response.viewport_top, response.viewport_bottom),
            evidence=evidence,
            geometry_mode=modes[response.geometry_mode],
            support_fraction=sum(1 for value in support if value > 0) / max(1, len(support)),
            unverified_fraction=sum(unverified) / max(1, len(unverified)),
            diagnostics=debug,
            _native=_LayeredHandle(lib, response.handle),
        )
        return result
    finally:
        lib.sw_layered_free_response(c.byref(response))


def reconstruct_layered(images, positions, viewport, options, diagnostics):
    return _layered_run(images, positions, viewport, options, diagnostics)


def plan_layered(result, plan_options):
    from .api import Goal, LayeredResult, PlanOptions, ViewSuggestion

    if not isinstance(result, LayeredResult):
        raise TypeError('plan_layered expects a LayeredResult')
    if not isinstance(plan_options, PlanOptions):
        raise TypeError('plan_options must be PlanOptions')
    handle = result._native
    if handle is None or not getattr(handle, 'ptr', None):
        raise ValueError('layered result does not carry a native planning handle')
    lib = handle._lib
    values = (
        [] if plan_options.candidates is None else [int(value) for value in plan_options.candidates]
    )
    candidates = (c.c_int32 * len(values))(*values) if values else None
    config = _PlanConfig(
        int(plan_options.goal == Goal.REPAIR),
        int(plan_options.count),
        int(plan_options.step),
        int(plan_options.minimum_separation),
        c.cast(candidates, c.POINTER(c.c_int32)) if candidates is not None else None,
        len(values),
        int(plan_options.document_height or 0),
    )
    views = _Views()
    error = c.create_string_buffer(4096)
    _check(
        lib.sw_layered_plan(handle.ptr, c.byref(config), c.byref(views), error, len(error)), error
    )
    try:
        return tuple(
            ViewSuggestion(
                view.scroll_position,
                view.estimated_gain,
                view.target_pixels,
                view.predicted_clean_fraction,
                Goal.REPAIR if view.goal else Goal.VERIFICATION,
            )
            for view in (views.data[index] for index in range(views.len))
        )
    finally:
        lib.sw_layered_free_views(c.byref(views))
