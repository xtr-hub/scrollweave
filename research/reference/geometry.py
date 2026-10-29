"""Viewport geometry from persistent full-width boundaries.

No known UI coordinates, text extraction, or oracle scroll positions are used.
The original variance-run locator is kept as a fallback on unframed content.
"""
from __future__ import annotations
import numpy as np


def stable_boundary_band(frames: np.ndarray, *, min_fraction: float = 0.35):
    """Find a screen viewport bracketed by long stationary full-width bars.

    Median across x suppresses localized dialogs, captions and text. Median
    across frames suppresses transient horizontal overlays. Candidate bars
    must be motion-stable on the side facing the screen exterior, and enclose
    a region with measurable temporal change.

    Returns (top,bottom) with exclusive bottom, or None when unsupported.
    """
    frames = np.asarray(frames, dtype=np.uint8)
    if frames.ndim != 4 or frames.shape[-1] != 3 or len(frames) < 2:
        return None
    n,h,w,_ = frames.shape
    if h < 48 or w < 48:
        return None
    # Don't form an N x H x W x 3 float64 temporary.
    a = frames.astype(np.int16)
    diffs = np.mean(np.abs(a[:,1:]-a[:,:-1]), axis=-1, dtype=np.float32)
    row_edge = np.median(np.median(diffs,axis=-1),axis=0)
    # A boundary covers >half the width across multiple observations.
    candidates = np.flatnonzero(row_edge >= 34.) + 1
    if len(candidates) < 2:
        return None
    temporal = np.var(frames.astype(np.float32),axis=0).mean(axis=(1,2))
    # Bracket with stationary UI on outer side. Do NOT require the document
    # immediately inside to be text-rich: it may start/end with white space.
    candidates_top=[]; candidates_bottom=[]
    q = max(5,int(round(h*.015)))
    for b in candidates:
        b = int(b)
        if b < q or b > h-q:
            continue
        if np.median(temporal[max(0,b-3*q):b]) < 8.:
            candidates_top.append(b)
        if np.median(temporal[b:min(h,b+3*q)]) < 8.:
            candidates_bottom.append(b)
    if not candidates_top or not candidates_bottom:
        return None
    # We require the widest candidate pair with a demonstrably moving interior.
    best=None
    for top in candidates_top:
        for bottom in candidates_bottom:
            if bottom-top < min_fraction*h:
                continue
            inside=temporal[top:bottom]
            moving_fraction = float(np.mean(inside > 30.))
            if moving_fraction < .06:
                continue
            score=(bottom-top) * min(moving_fraction/.20,1.)
            if best is None or score>best[0]:
                best=(score,top,bottom)
    if best is None:
        return None
    _,top,bottom=best
    # UI bars sometimes have a one-pixel white border just inside the page.
    # Correct only when temporal evidence directly near the boundary exists.
    # Do not trim arbitrary blank trailing rows inside the viewport.
    k=min(4,bottom-top)
    initial=np.flatnonzero(temporal[top:top+k]>10.)
    if len(initial):top+=int(initial[0])
    final=np.flatnonzero(temporal[max(top,bottom-k):bottom]>10.)
    if len(final):
        bottom = max(top,bottom-k)+int(final[-1])+1
    return int(top),int(bottom)


def activity_envelope(frames: np.ndarray):
    """Fallback for borderless documents: bridge large internal white gaps.

    Uses sufficiently sustained temporal activity across the *whole* viewport,
    not just the longest contiguous text-rich interval. A brief animated UI
    region can still make this uncertain, so clients should inspect mode.
    """
    frames=np.asarray(frames,dtype=np.uint8)
    if frames.ndim!=4 or len(frames)<2:
        return None
    temporal=np.var(frames.astype(np.float32),axis=0).mean(axis=(1,2))
    h=len(temporal)
    q80=float(np.percentile(temporal,80))
    threshold=max(10.,q80*.005)
    active=temporal>threshold
    size=max(9,int(h*.012) | 1)
    count=np.convolve(active.astype(np.float32),np.ones(size),mode='same')
    supported=count>=max(3,size*.28)
    keep=np.flatnonzero(supported)
    if not len(keep):return None
    # Boundary of supported interval, refined with actual temporal evidence.
    margin=size
    left=max(0,int(keep[0])-margin)
    right=min(h,int(keep[-1]+1)+margin)
    ids=np.flatnonzero(active[left:right])+left
    if not len(ids):return None
    return int(ids[0]),int(ids[-1]+1)


def infer_viewport(frames: np.ndarray):
    """Return bounds and evidence mode for auditability."""
    border=stable_boundary_band(frames)
    if border is not None:
        return border,'stable_screen_borders'
    activity=activity_envelope(frames)
    if activity is not None:
        return activity,'temporal_activity_envelope'
    return None,'insufficient_motion'
