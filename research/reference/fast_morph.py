"""Dependency-light morphology for binary masks.

A 2-D square opening/dilation uses two separable prefix-window passes.
Each pass is O(HW), independent of kernel width. We deliberately retain
uint8 masks and use bounded int32 accumulators (safe for normal image sizes).
"""
from __future__ import annotations
import numpy as np


def _window_count(a: np.ndarray, radius: int, axis: int) -> np.ndarray:
    if radius < 0:
        raise ValueError('radius must be non-negative')
    if radius == 0:
        return np.asarray(a, dtype=np.int32)
    a = np.asarray(a, dtype=np.uint8)
    pads = [(0, 0)] * a.ndim
    pads[axis] = (radius, radius)
    padded = np.pad(a, pads, mode='edge')
    cs = np.cumsum(padded, axis=axis, dtype=np.int32)
    lead = [(slice(None),)] * 0
    z_shape = list(cs.shape)
    z_shape[axis] = 1
    cs = np.concatenate([np.zeros(z_shape, dtype=np.int32), cs], axis=axis)
    right = [slice(None)] * a.ndim
    left = [slice(None)] * a.ndim
    right[axis] = slice(2 * radius + 1, None)
    left[axis] = slice(None, -(2 * radius + 1))
    return cs[tuple(right)] - cs[tuple(left)]


def square_filter(binary: np.ndarray, width: int, *, mode: str) -> np.ndarray:
    """Binary max/min filtering using 2 separable O(HW) passes."""
    if width <= 0 or width % 2 == 0:
        raise ValueError('width must be a positive odd integer')
    if binary.ndim != 2:
        raise ValueError('expected 2D mask')
    r = width // 2
    if r == 0:
        return binary.astype(bool, copy=True)
    target = 0 if mode == 'dilate' else width
    if mode not in ('dilate', 'erode'):
        raise ValueError(mode)
    v = _window_count(binary, r, 0)
    first = (v > target) if mode == 'dilate' else (v == target)
    v2 = _window_count(first, r, 1)
    return (v2 > target) if mode == 'dilate' else (v2 == target)


def open_dilate(mask: np.ndarray, opening: int = 5, expand: int = 17) -> np.ndarray:
    """Remove isolated pixels then grow coherent mask clusters."""
    first = square_filter(mask, opening, mode='erode')
    second = square_filter(first, opening, mode='dilate')
    return square_filter(second, expand, mode='dilate')
