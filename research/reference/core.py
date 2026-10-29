"""Screen/document reconstruction from aligned screen observations.

No source image or synthetic ground-truth coordinates enter this module.
The `scroll_positions` argument comes from image-only motion estimation.
NumPy+Pillow prototype; color pixels are copied from input without resampling.
"""
from __future__ import annotations
import numpy as np
from PIL import Image, ImageFilter
from .fast_morph import open_dilate
from .spatial import agreement_fields
from dataclasses import dataclass


@dataclass
class Reconstruction:
    image: np.ndarray
    source_ids: np.ndarray
    estimated_ui: np.ndarray
    coverage: np.ndarray
    auto_rail: np.ndarray


from .geometry import infer_viewport

def _moving_band_variance(frames: np.ndarray) -> tuple[int,int]:
    """Find the *whole* moving viewport, including low-texture document rows.

    A longest high-variance raw run splits on paragraphs, blank lines and
    large solid illustrations. Smooth row-level evidence to connect adjacent
    content bands, then refine the endpoints on unsmoothed temporal variance.
    No UI coordinates or document layout constants enter the inference.
    """
    row_var=frames.astype(np.float32).var(axis=0).mean(axis=(1,2))
    q80=float(np.percentile(row_var,80))
    window=max(9, (int(round(len(row_var)*0.017)) | 1))
    smooth=np.convolve(row_var,np.ones(window,np.float32)/window,mode='same')
    active=smooth>max(60.,q80*0.12)
    changes=np.flatnonzero(np.diff(np.r_[False,active,False]))
    runs=[(int(b-a),int(a),int(b)) for a,b in zip(changes[::2],changes[1::2])]
    if not runs:raise ValueError('No moving document band found')
    _,low,high=max(runs)
    margin=max(8,int(round(len(row_var)*.016)))
    start=max(0,low-margin)
    stop=min(len(row_var),high+margin)
    fine=np.flatnonzero(row_var[start:stop]>max(10.,q80*.005))
    if len(fine):return int(start+fine[0]),int(start+fine[-1]+1)
    return low,high


def _moving_band(frames: np.ndarray) -> tuple[int,int]:
    """Prefer screen-boundary evidence; fall back for borderless inputs.

    Unlike the variance-run heuristic, the boundary detector doesn't clip
    document whitespace when only a few scroll views are available.
    """
    band, _ = infer_viewport(frames)
    return band if band is not None else _moving_band_variance(frames)


def _auto_side_rail(frames: np.ndarray, top: int, bottom: int) -> np.ndarray:
    """Detect narrow, screen-edge-attached overlay rails by long vertical boundaries.

    No fixed rail x coordinate; returns mask only when an unusually strong,
    persistent vertical boundary is next to an image edge.
    """
    N,H,W,_=frames.shape
    result=np.zeros((N,H,W), bool)
    body=frames[:,top:bottom].astype(np.float32)
    # Vectorized edge strength and row-persistence; the same evidence is
    # reused for every candidate boundary rather than scanning RGB rows.
    edge_rows=np.mean(np.abs(np.diff(body,axis=2)),axis=3)  # N,viewport_height,W-1
    ed=edge_rows.mean(axis=1)
    persistent=(edge_rows>10).mean(axis=1)
    for k in range(N):
        v=ed[k]
        median=np.median(v)
        # A reasonable candidate must be within 5% of image edge and
        # extend over most of viewport height.
        for side in ('left','right'):
            if side=='right':
                search=np.arange(max(2,int(W*.94)),W-2)
            else:
                search=np.arange(2,max(3,int(W*.06)))
            if not len(search): continue
            # Search all candidate boundaries rather than accepting only the
            # strongest gradient: colored UI edges can be stronger than the
            # true *persistent* screen-rail boundary.
            candidates=[]
            for x in search:
                x=int(x)
                peak=float(v[x-1])
                if side=='right':
                    ref=v[max(0,x-max(12,int(W*.04))):max(1,x-4)]
                else:
                    ref=v[min(len(v),x+4):min(len(v),x+max(12,int(W*.04)))]
                local_base=float(np.median(ref)) if len(ref) else float(median)
                if peak < max(15.,1.65*local_base):continue
                persistence=float(persistent[k,x-1])
                if persistence < .66:continue
                candidates.append((persistence*peak/(local_base+1e-6),x))
            if not candidates: continue
            _,j=max(candidates)
            if side=='right': result[k,top:bottom,j:]=True
            else: result[k,top:bottom,:j]=True
    return result


def _learn_outliers(frames:np.ndarray, p:np.ndarray, top:int, bottom:int, tolerance:int=3) -> tuple[np.ndarray,np.ndarray]:
    N,H,W,_=frames.shape
    page_h=bottom-top
    dh=int(p.max()+page_h)
    strong=np.zeros((N,H,W),np.uint8)
    cached_votes=np.zeros((N,H,W),np.uint8)
    # A third independent observation allows us to break ties. Compare
    # exact decoded RGB, not gradient strength (which rewards UI borders).
    for docy in range(dh):
        ids=np.flatnonzero((p<=docy)&(docy<p+page_h))
        if len(ids)==0: continue
        sy=(docy-p[ids]+top).astype(int)
        vals=frames[ids,sy].astype(np.int16)
        equal=(np.max(np.abs(vals[:,None]-vals[None,:]),axis=-1)<=tolerance)
        vote=(equal.sum(axis=1)-1).astype(np.uint8)
        for t,k in enumerate(ids): cached_votes[k,sy[t]]=vote[t]
        if len(ids)<3: continue
        enough=vote.max(axis=0)>=1
        bad=enough[None,:]&(vote==0)
        for t, k in enumerate(ids): strong[k,sy[t],bad[t]]=255
    # Same 5x5 open + 17x17 expand semantics, but O(HW), not O(HW*k*k).
    return np.stack([open_dilate(strong[k] > 0, 5, 17) for k in range(N)]), cached_votes


def _local_vote(vote:np.ndarray, r:int=3) -> np.ndarray:
    # Efficient sliding horizontal mean, vectorized over candidate source.
    # Avoid normalizing away pixels where few observations exist.
    left=np.pad(vote,((0,0),(r,r)),mode='edge').astype(np.float32)
    cs=np.cumsum(left,axis=1,dtype=np.float32)
    cs=np.pad(cs,((0,0),(1,0)))
    return (cs[:,2*r+1:]-cs[:,:-(2*r+1)])/(2*r+1)


def reconstruct(frames:np.ndarray, scroll_positions:list[int]|np.ndarray,
                recurrent_mask:np.ndarray|None=None,
                flat_mask:np.ndarray|None=None,
                improve=True, texture_strength:float=0, spatial_strength:float=0) -> Reconstruction:
    frames=np.asarray(frames,dtype=np.uint8)
    N,H,W,C=frames.shape
    if C!=3:raise ValueError('RGB frames required')
    p=np.asarray(scroll_positions,dtype=int)
    p=p-p.min()
    if len(p)!=N:raise ValueError('one scroll offset per frame')
    top,bottom=_moving_band(frames)
    dh=int(max(p)+bottom-top)
    coverage=np.zeros((dh,W),np.uint8)
    base=np.zeros((N,H,W),bool)
    if recurrent_mask is not None:base|=recurrent_mask
    if flat_mask is not None:base|=flat_mask
    rail=_auto_side_rail(frames,top,bottom) if improve else np.zeros_like(base)
    if improve:
        dynamic,cached_votes=_learn_outliers(frames,p,top,bottom)
    else:
        dynamic=np.zeros_like(base)
        cached_votes=None
    texture=None
    if texture_strength:
        # Broad-band energy from natural texture. The Gaussian-like box
        # average prevents this from spuriously rewarding a thin UI border.
        texture=[]
        for im in frames:
            a=im.astype(np.float32)
            g=0.299*a[:,:,0]+0.587*a[:,:,1]+0.114*a[:,:,2]
            dx=np.zeros_like(g);dy=np.zeros_like(g)
            dx[:,1:-1]=np.abs(g[:,2:]-g[:,:-2])
            dy[1:-1,:]=np.abs(g[2:,:]-g[:-2,:])
            energy=Image.fromarray((dx+dy).clip(0,255).astype(np.uint8)).filter(ImageFilter.BoxBlur(6))
            texture.append(np.asarray(energy,dtype=np.float32)/255.)
        texture=np.stack(texture)
    suppress=base|rail|dynamic
    spatial = (agreement_fields(frames,p,top,bottom)
               if spatial_strength else None)
    result=np.zeros((dh,W,3),np.uint8)
    provenance=np.full((dh,W),-1,np.int16)
    for docy in range(dh):
        ids=np.flatnonzero((p<=docy)&(docy<p+bottom-top))
        if not len(ids):continue
        ys=(docy-p[ids]+top).astype(int)
        obs=frames[ids,ys].astype(np.int16)
        n=len(ids)
        # Multi-observation photometric agreement counts, including duplicate views.
        if cached_votes is None:
            same=np.max(np.abs(obs[:,None]-obs[None,:]),axis=-1)<=3
            votes=same.sum(axis=1).astype(np.float32)-1
        else:
            votes=cached_votes[ids,ys].astype(np.float32)
        score=votes*80
        if improve:
            # Smooth *evidence*, not image pixels. This favors a coherent
            # clean region over checkerboard source switching.
            score=score+_local_vote(votes,r=5)*17
        for ii,(k,sy) in enumerate(zip(ids,ys)):
            score[ii]+= .008*min(sy,H-1-sy)
            if improve and texture_strength:
                score[ii]+=texture_strength*texture[k,sy]
            if spatial_strength:
                score[ii]+=spatial_strength*spatial[k,sy]
            score[ii]-=240*base[k,sy]
            if improve:
                score[ii]-=280*dynamic[k,sy]
                score[ii]-=450*rail[k,sy]
        choice=np.argmax(score,axis=0)
        result[docy]=obs[choice,np.arange(W)].astype(np.uint8)
        provenance[docy]=ids[choice]
        coverage[docy]=len(ids)
    return Reconstruction(result,provenance,suppress,coverage,rail)
