"""Independent document-coordinate evidence, including duplicate-view handling.

This module never accesses a ground-truth page. Multiple frames captured at the
same scroll offset count as ONE independent viewpoint. They may still supply
alternative pixels, so a corrupt member of a duplicate group is not allowed
silently to suppress a clean member during audit.
"""
from dataclasses import dataclass
import numpy as np
from .core import _moving_band


@dataclass
class Evidence:
    independent_support: np.ndarray
    independent_views: np.ndarray
    conflict: np.ndarray
    unverified: np.ndarray
    invalid_source: np.ndarray


def audit_sources(frames, positions, source_ids, *, tolerance=3, band=None) -> Evidence:
    frames = np.asarray(frames, dtype=np.uint8)
    positions = np.asarray(positions, dtype=np.int64)
    sources = np.asarray(source_ids, dtype=np.int64)
    if frames.ndim != 4 or frames.shape[-1] != 3:
        raise ValueError('frames must be N,H,W,3 RGB')
    n, h, w, _ = frames.shape
    if positions.shape != (n,) or sources.ndim != 2 or sources.shape[1] != w:
        raise ValueError('incompatible positions or source_ids')
    p = positions - positions.min()
    top, bottom = _moving_band(frames) if band is None else band
    if not (0 <= top < bottom <= h):
        raise ValueError('invalid document viewport')
    dh = sources.shape[0]
    support = np.zeros((dh, w), np.uint16)
    views = np.zeros((dh, w), np.uint16)
    conflict = np.zeros((dh, w), bool)
    invalid = (sources < 0) | (sources >= n)
    cols = np.arange(w)
    # Mapping by unique scroll position; duplicates are alternatives, not votes.
    by_position = {}
    for index, offset in enumerate(p):
        by_position.setdefault(int(offset), []).append(index)
    for docy in range(dh):
        row_ids = sources[docy]
        safe = np.clip(row_ids, 0, n - 1)
        screen_y = docy - p[safe] + top
        good = (~invalid[docy]) & (screen_y >= top) & (screen_y < bottom)
        invalid[docy] |= ~good
        if not good.any():
            continue
        chosen = np.zeros((w, 3), dtype=np.int16)
        chosen[good] = frames[safe[good], screen_y[good], cols[good]].astype(np.int16)
        for offset, members in by_position.items():
            y = docy - offset + top
            if y < top or y >= bottom:
                continue
            usable = good & (p[safe] != offset)
            if not usable.any():
                continue
            # At least one frame at this scroll position must agree; repeated
            # UI overlays at that same location do NOT gain extra votes.
            group_equal = np.zeros(w, dtype=bool)
            for k in members:
                obs = frames[k, y].astype(np.int16)
                group_equal |= np.max(np.abs(chosen - obs), axis=-1) <= tolerance
            views[docy, usable] += 1
            support[docy, usable & group_equal] += 1
            conflict[docy, usable & ~group_equal] = True
    return Evidence(support, views, conflict, support == 0, invalid)
