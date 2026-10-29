"""Research-stage two-coordinate UI segmentation.

Uses cross-scroll-position witnesses to infer screen-space static overlays.
Never uses test ground truth. The graph-cut alternative is a separate ablation.
"""
import numpy as np
from scipy import ndimage as ndi
from .core import reconstruct, _moving_band
from .appearance import ui_appearance_probability

def train_occlusion(frames,p,top,bottom, tolerance=4):
    """Infer screen-layer UI using witness pairs at different page positions.

    Every doc location is compared across observations. If there exists a pair
    from distinct scroll positions that agrees, opposing observations are
    labelled suspicious. Project this evidence back to screen coordinates to
    find screen-stationary overlays and time-dependent local occlusion.
    """
    n,h,w,_=frames.shape; d_h=p.max()+(bottom-top)
    suspect=np.zeros((n,h,w),dtype=np.uint8)
    witness=np.zeros((n,h,w),dtype=np.uint8)
    confident=np.zeros((n,h,w),dtype=np.uint8)
    for docy in range(d_h):
        ids=np.flatnonzero((p<=docy)&(docy<p+(bottom-top)))
        if len(ids)<3: continue
        ys=(docy-p[ids]+top).astype(np.intp)
        vals=frames[ids,ys].astype(np.int16)
        matches=np.max(np.abs(vals[:,None,:,:]-vals[None,:,:,:]),axis=-1)<=tolerance
        # Evidence only from DISTINCT scroll offsets.
        matches &= (p[ids,None]!=p[None,ids])[:,:,None]
        good=matches.sum(axis=1)
        consensus=good.max(axis=0)>0
        # Only blame a candidate if an independently consistent pair exists.
        bad=consensus[None,:]&(good==0)
        for z,k in enumerate(ids):
            sy=ys[z]
            witness[k,sy,consensus]=1
            suspect[k,sy,bad[z]]=1
            confident[k,sy,(good[z]>0)]=1
    # Screen location prevalence, continuously regularized in 2D.
    denom=witness.sum(axis=0).astype(np.float32)
    num=suspect.sum(axis=0).astype(np.float32)
    rate=np.divide(num,denom,out=np.zeros_like(num),where=denom>0)
    # only where at least 3 independent visits have testable document evidence
    # structure smoothing helps capture UI border pixels omitted by exact equality
    smooth_num=ndi.uniform_filter(num,size=(11,11),mode='nearest')
    smooth_den=ndi.uniform_filter(denom,size=(11,11),mode='nearest')
    smooth_rate=smooth_num/np.maximum(smooth_den,1e-5)
    support=(smooth_den>=2.0)
    screen_seed=support & (smooth_rate>=0.45)
    # Extend into thin halos via proximity, not use indiscriminate dilation
    screen_mask=ndi.binary_dilation(ndi.binary_closing(screen_seed,structure=np.ones((5,5))),iterations=4)
    # Per-frame object candidate: large coherent outlier regions (not text)
    dyn=np.zeros_like(suspect,bool)
    for i in range(n):
        layer=ndi.binary_closing(suspect[i].astype(bool),structure=np.ones((7,7)))
        layer=ndi.binary_opening(layer,structure=np.ones((3,3)))
        # reject tiny disjoint flecks
        lab,cnt=ndi.label(layer)
        sizes=np.bincount(lab.ravel())
        keep=np.flatnonzero(sizes>=32)
        keep=keep[keep!=0]
        layer=np.isin(lab,keep)
        dyn[i]=ndi.binary_dilation(layer,iterations=3)
    # screen mask can be learned even when some frame has zero individual evidence
    return screen_mask,dyn,rate,suspect,confident


def _static_screen_structure(frames:np.ndarray, positions:np.ndarray, threshold=.82):
    """Independent screen-space evidence, without a document consensus.

    Stationary *edges* get seeded; stationary low-texture page backgrounds do
    not automatically become UI. Seed neighborhoods fill flat UI interiors.
    Uses one screenshot per unique scroll offset to avoid duplicate bias.
    """
    first_indices=list(dict((int(v),i) for i,v in reversed(list(enumerate(positions)))).values())
    # sort for deterministic result, not depending on arbitrary input order
    first_indices=sorted(first_indices)
    subset=frames[first_indices].astype(np.int16)
    if len(subset)<3:return np.zeros(frames.shape[1:3],bool)
    median=np.median(subset,axis=0).astype(np.int16)
    stable=(np.max(np.abs(subset-median[None,:,:,:]),axis=-1)<=4).mean(axis=0)>=threshold
    edge=np.zeros(stable.shape,np.float32)
    g=median.astype(np.float32).mean(axis=-1)
    edge[:,1:-1]+=np.abs(g[:,2:]-g[:,:-2])
    edge[1:-1,:]+=np.abs(g[2:,:]-g[:-2,:])
    seed=stable & (edge>=16)
    near=ndi.distance_transform_edt(~seed)<=13
    candidate=stable & near
    candidate=ndi.binary_closing(candidate,structure=np.ones((3,3)))
    candidate=ndi.binary_fill_holes(candidate)
    return candidate

def reconstruct_layered(frames, positions, recurrent_mask=None, flat_mask=None,
                        *, segmentation='temporal', include_transients=False,
                        texture_strength=80, appearance='off',
                        appearance_threshold=0.60, training_indices=None):
    """Reconstruct from ALIGNED RGB frames, inferring a stationary UI layer.

    segmentation='temporal': temporal witness mask (default, better on test).
    segmentation='graphcut': graph-cut Potts binary screen-layer mask.
    include_transients: experimental, may harm precision (default False).
    Returns (Reconstruction, debug-dict).
    """
    frames=np.asarray(frames,np.uint8)
    positions=np.asarray(positions,int)
    positions=positions-positions.min()
    top,bottom=_moving_band(frames)
    screen,transient,rate,susp,conf=train_occlusion(frames,positions,top,bottom)
    screen |= _static_screen_structure(frames,positions)
    if appearance not in ('off', 'adaptive'):
        raise ValueError("appearance must be 'off' or 'adaptive'")
    appearance_probability = (
        ui_appearance_probability(frames,susp,conf,training_indices=training_indices)
        if appearance=='adaptive' else np.zeros(frames.shape[:3],np.float32)
    )
    if segmentation=='graphcut':
        from .graphcut import segment
        witness_count=(susp | conf).sum(axis=0)
        screen|=segment(rate,witness_count,frames[0],step=3,boundary_strength=25)
    elif segmentation!='temporal':
        raise ValueError('segmentation must be temporal or graphcut')
    shape=frames.shape[:3]
    rm=np.zeros(shape,bool) if recurrent_mask is None else np.asarray(recurrent_mask,bool).copy()
    fm=np.zeros(shape,bool) if flat_mask is None else np.asarray(flat_mask,bool)
    rm|=screen[None,:,:]
    if appearance == 'adaptive':
        rm |= appearance_probability >= appearance_threshold
    if include_transients:
        rm|=transient
    result=reconstruct(frames,positions,rm,fm,improve=True,texture_strength=texture_strength)
    return result,dict(screen_mask=screen,transient_mask=transient,ui_rate=rate,
                       screenshot_disagreement=susp,document_witness=conf,
                       appearance_probability=appearance_probability)
