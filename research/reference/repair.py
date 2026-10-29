"""Research-stage expected *repair*, not just corroboration, active view planning.

All inputs come from CURRENTLY OBSERVED frames, reconstruction provenance,
and their fitted masks. Never consumes hidden candidate-frame pixels or ground truth.

A risk score is not a calibrated probability; it is an explicitly documented
heuristic proxy for source corruption and possible replacement. Return scores
along with a per-pixel uncertainty breakdown; don't call scores accuracy.
"""
from dataclasses import dataclass
import numpy as np

@dataclass(frozen=True)
class RepairSuggestion:
    scroll_position: int
    estimated_repair_score: float
    estimated_at_risk_pixels: int
    expected_clean_fraction: float


def _validate(frames, positions, source_ids, band, evidence):
    frames = np.asarray(frames, dtype=np.uint8)
    p = np.asarray(positions, dtype=np.int64)
    sources = np.asarray(source_ids, dtype=np.int64)
    if frames.ndim != 4 or frames.shape[-1] != 3 or p.shape != (len(frames),):
        raise ValueError('N,H,W,3 frames and N scroll positions are required')
    top, bottom = map(int,band)
    if not 0 <= top < bottom <= frames.shape[1] or sources.ndim != 2 or sources.shape[1]!=frames.shape[2]:
        raise ValueError('invalid viewport band or source geometry')
    if sources.shape != evidence.unverified.shape:
        raise ValueError('source IDs and evidence do not match')
    return frames,p,sources,top,bottom


def source_risk(frames, positions, source_ids, evidence, *, band, estimated_ui=None,
                screen_risk=None, outlier_hint=None):
    """Build a *document-space* risk field from independent evidence and source provenance.

    Evidence is measured at different scroll positions, not repeated frames.
    Pixel-source masks are sampled at the chosen source's SCREEN coordinate.
    No tests' ground truth or unobserved pixels are used.
    """
    frames,p,sources,top,bottom=_validate(frames,positions,source_ids,band,evidence)
    dh,w=sources.shape
    origin=p.min(); p=p-origin
    n,h=frames.shape[:2]
    # Missing corroboration raises uncertainty, but is NOT labelled a true error.
    risk=(0.08 + 0.52*evidence.unverified.astype(np.float32)
          + 0.23*evidence.conflict.astype(np.float32))
    risk += 0.07*(evidence.independent_views==0)
    risk += 0.11*((evidence.independent_support==0)&(evidence.independent_views>=1))
    bad = evidence.invalid_source.copy()
    if estimated_ui is not None:
        ui=np.asarray(estimated_ui,dtype=np.float32)
        if ui.shape!=(n,h,w):raise ValueError('estimated_ui shape mismatch')
    else:ui=None
    if screen_risk is not None:
        screen_risk=np.asarray(screen_risk,dtype=np.float32)
        if screen_risk.shape!=(h,w):raise ValueError('screen_risk shape mismatch')
    if outlier_hint is not None:
        outlier_hint=np.asarray(outlier_hint,dtype=np.float32)
        if outlier_hint.shape!=(n,h,w):raise ValueError('outlier_hint shape mismatch')
    cols=np.arange(w)
    for docy in range(dh):
        row=sources[docy]
        good=(row>=0)&(row<n)
        ids=np.clip(row,0,n-1)
        sy=docy-p[ids]+top
        good &= (sy>=top)&(sy<bottom)
        bad[docy] |= ~good
        if not good.any():continue
        indices=np.where(good)[0]
        f=ids[indices]; screen_y=sy[indices]
        if ui is not None:
            risk[docy,indices] += 0.50*np.clip(ui[f,screen_y,indices],0,1)
        if outlier_hint is not None:
            risk[docy,indices] += 0.40*np.clip(outlier_hint[f,screen_y,indices],0,1)
        if screen_risk is not None:
            risk[docy,indices] += 0.20*np.clip(screen_risk[screen_y,indices],0,1)
        # Proximity to screen overlays makes a unique observation harder to trust.
        margin=np.minimum(screen_y-top,bottom-1-screen_y).astype(np.float32)
        risk[docy,indices] += 0.12*np.clip(1-margin/35.,0.,1.)
    risk[bad]=0.9
    return np.clip(risk,0,1.7).astype(np.float32)


def plan_repair_views(frames, positions, source_ids, evidence, *, viewport_band,
                      estimated_ui=None, screen_risk=None, outlier_hint=None,
                      candidates=None, document_height=None, count=1, step=32,
                      minimum_separation=48, return_priority=False):
    """Select a new scroll viewport maximizing estimated repair opportunity.

    Assumes candidate offsets are feasible actions. The planner does not inspect
    any candidate RGB pixels, and doesn't guarantee a compositor will repair.
    It explicitly prioritizes *already reconstructed* at-risk regions; this
    separates its objective from verification-oriented planners.
    """
    frames,p,sources,top,bottom=_validate(frames,positions,source_ids,viewport_band,evidence)
    if count<1 or step<1 or minimum_separation<0:raise ValueError('bad planning controls')
    w=sources.shape[1]; view_h=bottom-top
    origin=int(p.min()); relative=p-origin
    H=int(document_height if document_height is not None else sources.shape[0])
    if H < sources.shape[0]:raise ValueError('document_height too small')
    if screen_risk is not None:
        screen_risk=np.asarray(screen_risk,dtype=np.float32)
        if screen_risk.shape!=(frames.shape[1],w):raise ValueError('screen_risk shape mismatch')
        clean=1.0-np.clip(screen_risk[top:bottom],0,1)
    else:clean=np.ones((view_h,w),np.float32)
    risk=source_risk(frames,positions,source_ids,evidence,band=(top,bottom),
                     estimated_ui=estimated_ui,screen_risk=screen_risk,outlier_hint=outlier_hint)
    priority=np.zeros((H,w),np.float32)
    # Only content already represented in the reconstruction is repairable; unseen
    # document extension is a separate *coverage* planner objective.
    priority[:len(risk)]=risk
    # Respect candidate's ability to offer a NEW independent view.
    if candidates is None:
        grid=list(range(0,max(1,H-view_h+1),step))+[max(0,H-view_h)]
    else:grid=[int(x)-origin for x in candidates]
    possible=sorted(set(q for q in grid if 0<=q<=max(0,H-view_h)))
    existing=set(map(int,relative))
    selected=[]
    remaining=priority.copy()
    for _ in range(count):
        best=None
        for q in possible:
            if any(abs(q-o)<minimum_separation for o in existing):continue
            if any(abs(q-(s.scroll_position-origin))<minimum_separation for s in selected):continue
            # A quality-aware preferred area discourages choosing text near an
            # overlay border, and avoids counting new images identical to old.
            v=remaining[q:q+view_h]
            benefit=float(np.sum(v*clean,dtype=np.float64))
            if best is None or benefit>best[0]:
                best=(benefit,q,int(np.count_nonzero(v>0.45)),float(clean.mean()))
        if best is None or best[0]<=0:break
        benefit,q,at_risk,clean_fraction=best
        selected.append(RepairSuggestion(q+origin,benefit,at_risk,clean_fraction))
        # Approximate one additional independent clean observation. Retain
        # uncertainty because the future pixels are unknown.
        remaining[q:q+view_h]*=1.-0.75*clean
    if return_priority:return selected,priority
    return selected
