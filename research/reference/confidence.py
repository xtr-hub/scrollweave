"""Evidence-based quality layer, independent of any ground truth.

A confident pixel must have agreement from independently shifted viewports.
If two observations disagree and no third provides a witness, mark UNKNOWN.
This reports what the images can prove; it does not hallucinate pixels.
"""
import numpy as np

def document_confidence(frames, positions, top, bottom, tolerance=3):
    frames=np.asarray(frames,dtype=np.uint8)
    p=np.asarray(positions,dtype=np.int32);p=p-p.min()
    N,H,W,_=frames.shape; height=int(p.max()+bottom-top)
    independent_witness=np.zeros((height,W),bool)
    contradicting=np.zeros((height,W),bool)
    distinct_view_count=np.zeros((height,),np.uint8)
    for dy in range(height):
        ids=np.flatnonzero((p<=dy)&(dy<p+bottom-top))
        if len(ids)==0:continue
        distinct_view_count[dy]=len(np.unique(p[ids]))
        if len(ids)<2:continue
        sy=(dy-p[ids]+top).astype(np.intp)
        obs=frames[ids,sy].astype(np.int16)
        diff=np.max(np.abs(obs[:,None]-obs[None,:]),axis=-1)
        independent=(p[ids,None]!=p[None,ids])[:,:,None]
        witness=np.any((diff<=tolerance)&independent,axis=(0,1))
        contradict=np.any((diff>tolerance)&independent,axis=(0,1))
        independent_witness[dy]=witness
        contradicting[dy]=contradict
    unresolved=(~independent_witness)
    ambiguous=unresolved & contradicting
    unverified=unresolved & (~contradicting)
    return dict(confident=independent_witness,ambiguous=ambiguous,
                unverified=unverified,unresolved=unresolved,
                distinct_view_count=distinct_view_count)
