from pathlib import Path
import numpy as np
from PIL import Image
import json, time
B=Path(__file__).resolve().parents[1]/'fixtures'
files=sorted((B/'screenshots').glob('*.png'))
imgs=np.stack([np.asarray(Image.open(p).convert('RGB')) for p in files]).astype(np.uint8)
N,H,W,C=imgs.shape
# Motion-rich rows are automatic estimated, not parameterized as known UI coordinates.
small=imgs[:,::2,::2,:].astype(np.float32)
variance=np.sqrt(small.var(axis=0).mean(axis=2)) # h,w
row_motion=np.median(variance,axis=1)
# contiguous motion-bearing band
mask=row_motion > max(6, np.percentile(row_motion,75)*.30)
ys=np.where(mask)[0]
y0=max(0, 2*int(ys.min())-3);y1=min(H,2*int(ys.max()+1)+3)
print('automatically located moving page region',y0,y1,'row variance',row_motion.min(),row_motion.max())
# Static-confidence probability from temporal color constancy (calibrated on this dataset with no known masks).
median_rgb=np.median(small,axis=0)
stable=np.mean(np.max(np.abs(small-median_rgb),axis=-1)<=5,axis=0)
# static element typically remains identical in >=70% frames
wstatic=np.clip((.77-stable)/.22,0,1)
print('non-static fraction',np.mean(wstatic>0.5))
# lowres features for phase correlation
DS=4
h0=y0//DS;h1=y1//DS
LR=imgs[:,y0:y1,:,:].astype(np.float32)
# ensure dims divisible by 4
LH=(LR.shape[1]//DS)*DS;LW=(W//DS)*DS
LR=LR[:,:LH,:LW,:]
g=(0.299*LR[...,0]+0.587*LR[...,1]+0.114*LR[...,2])
g=g.reshape(N,LH//DS,DS,LW//DS,DS).mean(axis=(2,4))
weights=wstatic[y0//2:(y0+LH)//2:2,:LW//2:2]
weights=weights[:g.shape[1],:g.shape[2]]
# gradients high-pass preserve contour field, weights force stationary UI to zero
feat=np.zeros_like(g)
feat[:,:,1:-1]=(g[:,:,2:]-g[:,:,:-2])/2
feat[:,1:-1,:]+= (g[:,2:,:]-g[:,:-2,:])/2
feat=feat*weights[None,:,:]
# FFT across vertical axis per x, sum global 2D evidence. No fixed tiles.
h=feat.shape[1];w=feat.shape[2];fftlen=1
while fftlen < 2*h:fftlen*=2
FF=np.fft.rfft(feat,n=fftlen,axis=1)
print('shape',feat.shape)

def refine(i,j,dy_coarse,rs=7):
    A=imgs[i,y0:y1].astype(np.int16)
    BB=imgs[j,y0:y1].astype(np.int16)
    staticfull=np.repeat(np.repeat(weights,4,axis=0),4,axis=1)
    staticfull=staticfull[:A.shape[0],:A.shape[1]]
    # match only positions well observed in both images; robust to local masks
    if staticfull.shape != A.shape[:2]:
        staticfull=np.pad(staticfull,((0,A.shape[0]-staticfull.shape[0]),(0,A.shape[1]-staticfull.shape[1])),mode='edge')
    best=None
    for dy in range(dy_coarse-rs,dy_coarse+rs+1):
        if abs(dy)>=A.shape[0]*.76:continue
        if dy>=0:
            aa=A[dy::3,::3];bb=BB[:A.shape[0]-dy:3,::3]
            wa=staticfull[dy::3,::3];wb=staticfull[:A.shape[0]-dy:3,::3]
        else:
            aa=A[:A.shape[0]+dy:3,::3];bb=BB[-dy::3,::3]
            wa=staticfull[:A.shape[0]+dy:3,::3];wb=staticfull[-dy::3,::3]
        valid=((wa>0.12)&(wb>0.12))
        if valid.sum()<300:continue
        pixdiff=np.max(np.abs(aa-bb),axis=-1)
        # Robust inlier proportion: untouched decoded source pixels agree exactly.
        score=float(np.sum((pixdiff<=3)*valid)/valid.sum())
        if best is None or score>best[1]:best=(dy,score,int(valid.sum()))
    return best

def match(i,j,topk=5):
    z=np.fft.irfft((FF[i]*np.conj(FF[j])).sum(axis=1),n=fftlen)
    # sign convention: dy>0 => B to be placed below A
    # frequency index d indicates fA(y+d) ~ fB(y)
    dvals=np.concatenate((np.arange(-h+1,0),np.arange(0,h)))
    idx=np.mod(dvals,fftlen)
    scores=z[idx]/(h-np.abs(dvals)).clip(1)
    good=(np.abs(dvals)>=6)&(np.abs(dvals)<h*.72)
    scores[~good]=-1e12
    top=np.argpartition(scores,-topk)[-topk:]
    top=top[np.argsort(scores[top])[::-1]]
    peaks=[]
    for k in top:
        d=int(dvals[k]*DS)
        v=refine(i,j,d,rs=DS*2)
        if v and all(abs(v[0]-p[0])>2 for p in peaks):peaks.append(v)
    # screen-fixed candidate dy=0 only if it is truly a still image, not used here
    return max(peaks,key=lambda x:x[1]) if peaks else None

if __name__=='__main__':
    true=[x['scroll'] for x in json.loads((B/'oracle_ground_truth.json').read_text())['positions']]
    t=time.time()
    for i in range(N-1):
        m=match(i,i+1)
        print(i,i+1,'guess',m,'true',true[i+1]-true[i])
    print('time',time.time()-t)
