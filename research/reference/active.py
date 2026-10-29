"""Active scroll observation planning from *existing* reconstruction evidence.

Ranks future scroll positions without inspecting their future pixels or any
hidden true document. This is a planning/diagnostic capability, NOT a promise
that a new frame at the nominated position will reveal hidden content.
"""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class ViewSuggestion:
    scroll_position: int
    estimated_gain: float
    target_pixels: int
    predicted_clean_fraction: float


def plan_views(
    evidence, positions, *, viewport_band, screen_risk=None,
    document_height=None, width=None, candidates=None, step=32,
    count=1, minimum_separation=48, conflict_weight=0.5,
    outside_weight=1.5
):
    """Suggest offsets maximizing expected *independently verifiable* coverage.

    The score is expectation based on masks/evidence, not future measured gain.
    If a screen-space UI risk map is supplied, the corresponding projected
    document pixels are downweighted. A score for an existing scroll offset is
    zero: a repeat of that position cannot provide independent evidence.

    Input/output candidate positions use the same absolute scroll coordinate
    system as `positions`; internally the minimum observed offset is origin.
    The target document height may be larger than the current output height.
    """
    p = np.asarray(positions, np.int64)
    if p.ndim != 1 or not p.size:
        raise ValueError('positions must be nonempty vector')
    origin = int(p.min())
    p = p - origin
    top, bottom = map(int, viewport_band)
    if not 0 <= top < bottom:
        raise ValueError('invalid viewport band')
    viewport = bottom - top
    H = int(document_height if document_height is not None else evidence.unverified.shape[0])
    W = int(width if width is not None else evidence.unverified.shape[1])
    if W != evidence.unverified.shape[1] or H < evidence.unverified.shape[0]:
        raise ValueError('incompatible document geometry')
    if count < 1 or step < 1 or minimum_separation < 0:
        raise ValueError('invalid planning parameters')
    priority = np.full((H, W), float(outside_weight), np.float32)
    hh = evidence.unverified.shape[0]
    priority[:hh] = (evidence.unverified.astype(np.float32) +
                     float(conflict_weight) * evidence.conflict.astype(np.float32))
    # Already verified conflict pixels are lower priority than unknown pixels.
    priority[:hh][~evidence.unverified] *= 0.20
    if screen_risk is None:
        clean = np.ones((viewport, W), dtype=np.float32)
    else:
        risk = np.asarray(screen_risk, dtype=np.float32)
        if risk.shape[1] != W or risk.shape[0] < bottom:
            raise ValueError('screen_risk must cover viewport and width')
        clean = 1.0 - np.clip(risk[top:bottom], 0, 1)

    if candidates is None:
        grid = list(range(0, max(1, H-viewport+1), step))
        grid.append(max(0,H-viewport))
    else:
        grid = [int(x)-origin for x in candidates]
    offsets = sorted(set(x for x in grid if 0 <= x <= max(0,H-viewport)))
    occupied = set(map(int, p))
    suggestions = []
    remaining = priority.copy()
    for _ in range(count):
        best = None
        for q in offsets:
            if q in occupied or any(abs(q-x)<minimum_separation for x in occupied) or any(abs(q-(s.scroll_position-origin))<minimum_separation for s in suggestions):
                continue
            segment = remaining[q:q+viewport]
            gain = float(np.sum(segment * clean, dtype=np.float64))
            if best is None or gain > best[0]:
                best = (gain,q,int(np.count_nonzero(segment>0)),float(clean.mean()))
        if best is None or best[0] <= 0:
            break
        gain,q,pixels,clean_fraction = best
        suggestions.append(ViewSuggestion(q+origin,gain,pixels,clean_fraction))
        # Expected one extra independent sample lowers uncertainty of covered
        # pixels, but not to zero because UI may still obstruct the new view.
        remaining[q:q+viewport] *= (1.0-0.85*clean)
    return suggestions


def screen_risk_from_masks(frame_masks, *, independent_offsets=None):
    """Screen-coordinate empirical UI frequency, avoiding duplicate bias.

    Only mask observations enter this estimate; no hidden source document or
    future observation is consulted. It may underestimate unseen transient UI.
    """
    masks = np.asarray(frame_masks, dtype=np.float32)
    if masks.ndim != 3:
        raise ValueError('frame_masks should have shape N,H,W')
    if independent_offsets is not None:
        p=np.asarray(independent_offsets)
        if len(p)!=len(masks):
            raise ValueError('offset length mismatch')
        uniq = np.unique(p)
        masks = np.stack([np.mean(masks[p==u],axis=0) for u in uniq])
    return np.mean(masks, axis=0).astype(np.float32)
