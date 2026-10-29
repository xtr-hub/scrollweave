"""Unsupervised calibration of externally supplied UI masks.

Certain color/flatness detectors generate entire-width false-positive bands at
viewport boundaries. We reject a band only if the scene demonstrably changes
under scrolling, the external mask is almost universal, and a separately
obtained recurrence mask doesn't corroborate that row as fixed chrome.
This correction is conditional; it does not require known UI coordinates.
"""
from __future__ import annotations
import numpy as np


def calibrate_full_width_mask(frames: np.ndarray, flat_mask:np.ndarray,
                              recurrent_mask:np.ndarray|None, top:int, bottom:int,
                              *, prevalence:float=.85, width_fraction:float=.97) -> tuple[np.ndarray,np.ndarray]:
    frames=np.asarray(frames,np.uint8)
    base=np.asarray(flat_mask,bool)
    if base.shape!=frames.shape[:3]:raise ValueError('mask shape does not match frames')
    corrected=base.copy()
    row_fraction=base.mean(axis=2)
    broad=(row_fraction>=width_fraction).mean(axis=0)>=prevalence
    moving=frames.astype(np.float32).var(axis=0).mean(axis=(1,2))
    active=np.zeros(frames.shape[1],bool)
    # Calibration addresses halo/bleed around detected viewport chrome,
    # never an arbitrary full-width banner in the *middle* of the page.
    edge_margin=max(12,min(36,int(round((bottom-top)*.04))))
    active[top:min(bottom,top+edge_margin)]=True
    active[max(top,bottom-edge_margin):bottom]=True
    q90=float(np.percentile(moving[top:bottom],90))
    active &= moving>max(20.,.02*q90)
    if recurrent_mask is not None:
        recurrent=np.asarray(recurrent_mask,bool)
        if recurrent.shape!=base.shape:raise ValueError('recurrent mask shape does not match frames')
        recurrent_fraction=recurrent.mean(axis=(0,2))
        active &= recurrent_fraction<.65
    reject=broad & active
    # This calibration can only remove a globally full-width *prior* band;
    # any independent UI evidence remains intact.
    corrected[:,reject,:]=False
    return corrected,reject
