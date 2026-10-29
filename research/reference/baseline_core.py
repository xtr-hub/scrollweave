"""Screen/document reconstruction from aligned screen observations.

No source image or synthetic ground-truth coordinates enter this module.
The `scroll_positions` argument comes from image-only motion estimation.
NumPy+Pillow prototype; color pixels are copied from input without resampling.
"""
from __future__ import annotations
import numpy as np
from PIL import Image, ImageFilter
from dataclasses import dataclass


@dataclass
class Reconstruction:
    image: np.ndarray
    source_ids: np.ndarray
    estimated_ui: np.ndarray
    coverage: np.ndarray
    auto_rail: np.ndarray


def _moving_band(frames: np.ndarray) -> tuple[int,int]:
    row_var = frames.astype(np.float32).var(axis=0).mean(axis=(1,2))
    active = row_var > max(60., np.percentile(row_var, 80) * .23)
    changes=np.flatnonzero(np.diff(np.r_[False,active,False]))
    runs=[(int(b-a),int(a),int(b)) for a,b in zip(changes[::2],changes[1::2])]
    if not runs: raise ValueError('No moving document band found')
    _, top,bottom=max(runs)
    return top,bottom


def _auto_side_rail(frames: np.ndarray, top: int, bottom: int) -> np.ndarray:
    """Detect narrow, screen-edge-attached overlay rails by long vertical boundaries.

    No fixed rail x coordinate; returns mask only when an unusually strong,
    persistent vertical boundary is next to an image edge.
    """
    N,H,W,_=frames.shape
    result=np.zeros((N,H,W), bool)
    body=frames[:,top:bottom].astype(np.float32)
    ed=np.mean(np.abs(np.diff(body,axis=2)),axis=(1,3)) # N x W-1
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
            j=int(search[np.argmax(v[search-1])]); peak=float(v[j-1])
            if peak < max(15.,2.25*median): continue
            # Confirm edge persists across rows, not a few bright artifacts.
            rowdiff=np.mean(np.abs(body[k,:,j]-body[k,:,j-1]),axis=1)
            if np.mean(rowdiff>10) < .52:continue
            if side=='right': result[k,top:bottom,j:]=True
            else: result[k,top:bottom,:j]=True
    return result


def _learn_outliers(frames:np.ndarray, p:np.ndarray, top:int, bottom:int, tolerance:int=3) -> np.ndarray:
    N,H,W,_=frames.shape
    page_h=bottom-top
    dh=int(p.max()+page_h)
    strong=np.zeros((N,H,W),np.uint8)
    # A third independent observation allows us to break ties. Compare
    # exact decoded RGB, not gradient strength (which rewards UI borders).
    for docy in range(dh):
        ids=np.flatnonzero((p<=docy)&(docy<p+page_h))
        if len(ids)<3: continue
        sy=(docy-p[ids]+top).astype(int)
        vals=frames[ids,sy].astype(np.int16)
        equal=(np.max(np.abs(vals[:,None]-vals[None,:]),axis=-1)<=tolerance)
        vote=equal.sum(axis=1)-1
        enough=vote.max(axis=0)>=1
        bad=enough[None,:]&(vote==0)
        for t, k in enumerate(ids): strong[k,sy[t],bad[t]]=255
    # Filter isolated point disagreements, preserving organized occluders.
    cleaned=[]
    for k in range(N):
        a=Image.fromarray(strong[k])
        a=a.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))
        a=a.filter(ImageFilter.MaxFilter(17))
        cleaned.append(np.asarray(a)>0)
    return np.stack(cleaned)


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
                improve=True, texture_strength:float=0) -> Reconstruction:
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
    dynamic=_learn_outliers(frames,p,top,bottom) if improve else np.zeros_like(base)
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
    result=np.zeros((dh,W,3),np.uint8)
    provenance=np.full((dh,W),-1,np.int16)
    for docy in range(dh):
        ids=np.flatnonzero((p<=docy)&(docy<p+bottom-top))
        if not len(ids):continue
        ys=(docy-p[ids]+top).astype(int)
        obs=frames[ids,ys].astype(np.int16)
        n=len(ids)
        # Multi-observation photometric agreement counts, including duplicate views.
        same=np.max(np.abs(obs[:,None]-obs[None,:]),axis=-1)<=3
        votes=same.sum(axis=1).astype(np.float32)-1
        score=votes*80
        if improve:
            # Smooth *evidence*, not image pixels. This favors a coherent
            # clean region over checkerboard source switching.
            score=score+_local_vote(votes,r=5)*17
        for ii,(k,sy) in enumerate(zip(ids,ys)):
            score[ii]+= .008*min(sy,H-1-sy)
            if improve and texture_strength:
                score[ii]+=texture_strength*texture[k,sy]
            score[ii]-=240*base[k,sy]
            if improve:
                score[ii]-=280*dynamic[k,sy]
                score[ii]-=450*rail[k,sy]
        choice=np.argmax(score,axis=0)
        result[docy]=obs[choice,np.arange(W)].astype(np.uint8)
        provenance[docy]=ids[choice]
        coverage[docy]=len(ids)
    return Reconstruction(result,provenance,suppress,coverage,rail)
