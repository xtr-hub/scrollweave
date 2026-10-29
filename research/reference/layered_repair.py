"""Repair-oriented view planning layered on the planned compositor.

No claims of improved current-image accuracy: this stage is a research
planner, and real quality improvements must be measured after a held-out view.
"""
import numpy as np
from .layered_planned import reconstruct_layered as _planned
from .active import screen_risk_from_masks
from .core import _moving_band
from .repair import plan_repair_views
from .evidence import audit_sources


def reconstruct_layered(frames, positions, *args, recommend_repair_views=0,
                        repair_candidates=None, **kwargs):
    result, debug = _planned(frames, positions, *args, **kwargs)
    if recommend_repair_views:
        a = np.asarray(frames,dtype=np.uint8)
        p = np.asarray(positions,dtype=np.int64)
        evidence = audit_sources(a,p,result.source_ids,band=_moving_band(a))
        risk = screen_risk_from_masks(result.estimated_ui,independent_offsets=p)
        proposals, priority=plan_repair_views(
            a,p,result.source_ids,evidence,viewport_band=_moving_band(a),
            estimated_ui=result.estimated_ui,screen_risk=risk,
            outlier_hint=debug.get('screenshot_disagreement'),
            candidates=repair_candidates,count=int(recommend_repair_views),
            return_priority=True)
        debug['suggested_repair_views']=proposals
        debug['repair_priority']=priority
    return result,debug
