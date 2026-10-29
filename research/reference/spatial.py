"""Spatially coherent agreement evidence, estimated from the screenshots alone.

All operations take place on aligned document coordinates; the actual
pixel values are never blurred or resampled for output.
"""
from __future__ import annotations
import numpy as np
from .fast_morph import _window_count


def box_mean(a: np.ndarray, width: int) -> np.ndarray:
    if width < 1 or width % 2 != 1:
        raise ValueError('width must be positive odd')
    if width == 1:
        return a.astype(np.float32)
    # _window_count expects binary uint8; agreement count may be >1, so
    # accumulate float/int scores with a generic cumsum instead.
    r = width // 2
    a = np.asarray(a,dtype=np.float32)
    for axis in (0,1):
        pads=[(0,0)]*a.ndim; pads[axis]=(r,r)
        tmp=np.pad(a,pads,mode='edge')
        cs=np.cumsum(tmp,axis=axis,dtype=np.float32)
        z=list(cs.shape);z[axis]=1
        cs=np.concatenate([np.zeros(z,dtype=np.float32),cs],axis=axis)
        hi=[slice(None)]*a.ndim;lo=[slice(None)]*a.ndim
        hi[axis]=slice(width,None);lo[axis]=slice(None,-width)
        a=(cs[tuple(hi)]-cs[tuple(lo)])/width
    return a


def agreement_fields(frames:np.ndarray, p:np.ndarray, top:int, bottom:int,
                     tolerance:int=3, neighborhood:int=17) -> np.ndarray:
    """For each screenshot pixel, count agreeing pixels in other aligned views.

    Pairwise comparisons use **all** overlapping views, without rectangular
    feature blocks. Local spatial averaging is applied to evidence only.
    """
    n,h,w,c=frames.shape
    page_h=bottom-top
    scores=np.zeros((n,h,w),np.uint8)
    for i in range(n):
        for j in range(i+1,n):
            lo=max(int(p[i]),int(p[j]))
            hi=min(int(p[i])+page_h,int(p[j])+page_h)
            if hi<=lo:continue
            ya0=top+lo-int(p[i]); yb0=top+lo-int(p[j]); k=hi-lo
            a=frames[i,ya0:ya0+k].astype(np.int16)
            b=frames[j,yb0:yb0+k].astype(np.int16)
            eq=np.max(np.abs(a-b),axis=-1)<=tolerance
            scores[i,ya0:ya0+k]+=eq.astype(np.uint8)
            scores[j,yb0:yb0+k]+=eq.astype(np.uint8)
    smoothed=np.stack([box_mean(scores[k],neighborhood) for k in range(n)])
    return smoothed
