"""Binary graph-cut segmentation of screen-space stationary overlays.

A CPU-only reference implementation uses scipy.sparse.csgraph.maximum_flow.
The graph represents a contiguous screen-space UI vs document segmentation,
with contrast-dependent Potts regularization. No machine learning / ground truth.
"""
import numpy as np
from scipy import ndimage as ndi
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import maximum_flow, breadth_first_order

def segment(rate: np.ndarray, witness_count: np.ndarray, rgb: np.ndarray, *, step=3,
            data_strength=120, boundary_strength=20, ui_prior=0.47):
    H,W=rate.shape
    # Note: using strided samples, not interpolation of source pixels
    rr=ndi.uniform_filter(rate.astype(np.float32),size=5)[::step,::step]
    support=ndi.uniform_filter(witness_count.astype(np.float32),size=5)[::step,::step]
    sample=rgb[::step,::step].astype(np.float32)
    h,w=rr.shape; total=h*w; source=total;sink=total+1
    evidence=np.clip(rr,0,1)
    # data: log odds from witness-derived screen suspicion
    # untested/undersampled regions default to a document hypothesis.
    signal=(evidence-ui_prior)*data_strength
    signal=np.where(support>=1.75,signal,-data_strength*.7)
    # Allow strong UI evidence to overcome prior, force very-low rate to doc.
    D_ui=np.maximum(0,-signal).astype(np.int32).ravel()
    D_doc=np.maximum(0, signal).astype(np.int32).ravel()
    S=np.arange(total,dtype=np.int32)
    rows=[np.full(total,source,dtype=np.int32), S, S, np.full(total,sink,dtype=np.int32)]
    cols=[S,np.full(total,source,dtype=np.int32),np.full(total,sink,dtype=np.int32),S]
    caps=[D_doc,D_doc,D_ui,D_ui]
    grid=np.arange(total,dtype=np.int32).reshape(h,w)
    for axis in (0,1):
        if axis==0:
            a=grid[:-1,:].ravel();b=grid[1:,:].ravel()
            grad=np.mean(np.abs(sample[:-1]-sample[1:]),axis=2).ravel()
        else:
            a=grid[:,:-1].ravel();b=grid[:,1:].ravel()
            grad=np.mean(np.abs(sample[:,:-1]-sample[:,1:]),axis=2).ravel()
        weight=np.maximum(1,np.rint(boundary_strength * np.exp(-grad/22))).astype(np.int32)
        rows.extend((a,b));cols.extend((b,a));caps.extend((weight,weight))
    graph=coo_matrix((np.concatenate(caps),(np.concatenate(rows),np.concatenate(cols))),shape=(total+2,total+2)).tocsr()
    # Coalescing sparse duplicates is important: maximum_flow expects integer caps.
    graph.sum_duplicates()
    flow=maximum_flow(graph,source,sink)
    residual=graph-flow.flow
    residual.data=(residual.data>0).astype(np.int32)
    residual.eliminate_zeros()
    reach=breadth_first_order(residual,source,directed=True,return_predecessors=False)
    labels=np.zeros(total,bool)
    labels[reach[reach<total]]=True
    small=labels.reshape(h,w)
    up=np.repeat(np.repeat(small,step,axis=0),step,axis=1)[:H,:W]
    if up.shape!=(H,W): up=np.pad(up,((0,H-up.shape[0]),(0,W-up.shape[1])),mode='edge')
    return up
