"""Calibrated compositor plus independent evidence and active view planning.

This stage intentionally does NOT claim a reconstruction accuracy gain; it
provides better measured uncertainty and a useful next-observation decision.
"""
import numpy as np
from .layered_calibrated import reconstruct_layered as _reconstruct
from .evidence import audit_sources
from .active import plan_views, screen_risk_from_masks
from .core import _moving_band


def reconstruct_layered(frames, positions, *args, recommend_views=0,
                        candidate_positions=None, **kwargs):
    result, debug = _reconstruct(frames, positions, *args, **kwargs)
    frame_array=np.asarray(frames,dtype=np.uint8)
    p=np.asarray(positions,dtype=np.int64)
    band=_moving_band(frame_array)
    ev = audit_sources(frame_array,p,result.source_ids,band=band)
    debug.update(independent_support=ev.independent_support,
                 independent_views=ev.independent_views,
                 conflict_map=ev.conflict,
                 unverified_map=ev.unverified,
                 invalid_source_map=ev.invalid_source)
    if recommend_views:
        mask_risk=screen_risk_from_masks(result.estimated_ui,independent_offsets=p)
        debug['suggested_views']=plan_views(
            ev,p,viewport_band=band,screen_risk=mask_risk,
            candidates=candidate_positions,count=int(recommend_views))
    return result, debug
