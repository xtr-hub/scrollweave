"""Experimental calibrated compositor: calibrate global flat masks against observed motion.

Maintains the pixel selector unchanged, and adds a strictly scoped mask
calibration pass. This is intentionally not a claim to handle arbitrary UI.
"""
from .layered import reconstruct_layered as _baseline
from .core import _moving_band
from .prior_calibration import calibrate_full_width_mask
import numpy as np

def reconstruct_layered(frames,positions,recurrent_mask=None,flat_mask=None,
                        *,calibrate_prior=True,**kwargs):
    diagnostic={}
    clean_flat=flat_mask
    if calibrate_prior and flat_mask is not None:
        low,high=_moving_band(np.asarray(frames,np.uint8))
        clean_flat,rejected=calibrate_full_width_mask(frames,flat_mask,recurrent_mask,low,high)
        diagnostic['rejected_full_width_rows']=np.flatnonzero(rejected)
    result,dbg=_baseline(frames,positions,recurrent_mask,clean_flat,**kwargs)
    dbg.update(diagnostic)
    return result,dbg
