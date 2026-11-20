"""Cover the entire continuous domain of each necessary reuse chain with boxes.

Box layout [p.xlo,p.ylo,q.xlo,q.ylo,p.xhi,p.yhi,q.xhi,q.yhi].
Run as a script to write a compact certificate log. See README for the bounds.
"""
from itertools import permutations
from decimal import Decimal, localcontext
import json
import sys

import numpy as np

from model import BLD, DURATION, H0, OUT, R, SCENE, SHADOW, SIDE, SPEED, TGT, VEH


GOAL = 10.3520
PARTNER = 1.5320327
TIME_GUARD = 1e-9
METRE_GUARD = 1e-7
XY_PAD = 1e-10
ANGLE_PAD = 1e-11
PI_UP = 3.141592653589794


def norm(x,y):
    return np.sqrt(x*x+y*y)


def atan2_lower(y,x):
    """0<=y, approximate atan2 without a libm atan and round down by 1e-11 rad.

    Range reduction first to [0,pi/4], then by a half angle to [0,pi/8].
    The remainder is bounded by 2*.415**29/29. See README for the roundoff.
    """
    X = np.abs(x)
    biggest = np.maximum(X,y)
    ratio = np.minimum(X,y)/np.where(biggest>0,biggest,1.)
    t = ratio/(1.+np.sqrt(1.+ratio*ratio))
    t2 = t*t
    poly = -1./27
    for k in reversed(range(13)):
        poly = (-1.)**k/(2*k+1) + poly*t2
    angle = 2*t*poly
    angle = np.where(y>X,np.pi/2-angle,angle)
    angle = np.where(x<0,np.pi-angle,angle)
    return np.maximum(0.,angle-ANGLE_PAD)


def min_dist(lo,hi,x,y):
    return norm(np.maximum(np.maximum(lo[:,0]-x, x-hi[:,0]), 0),
                np.maximum(np.maximum(lo[:,1]-y, y-hi[:,1]), 0))


def slew_lower(lo,hi,t):
    x,y,h = TGT[t]
    dx = x-lo[:,0]+XY_PAD
    far_y = np.maximum(abs(y-lo[:,1]),abs(y-hi[:,1]))+XY_PAD
    near_y = np.maximum(np.maximum(lo[:,1]-y,y-hi[:,1])-XY_PAD,0.)
    min_yaw = atan2_lower(np.where(dx < 0,far_y,near_y),dx)
    far_x = np.maximum(abs(x-lo[:,0]),abs(x-hi[:,0]))+XY_PAD
    rmax = norm(far_x,far_y)+XY_PAD
    return np.maximum(min_yaw,atan2_lower(h-H0,rmax))/(SCENE['axis_speed_deg_s']*PI_UP/180)


def corner_occluded(x,y,t,building):
    """Use clipping only to propose u, confirm the resulting interior witness."""
    tx,ty,h = TGT[t]
    bx,by,Bx,By,bh = building
    l = np.zeros_like(x)
    r = np.full_like(x,(bh-H0)/(h-H0))
    for start,end,low,high in [(x,tx,bx,Bx),(y,ty,by,By)]:
        d = end-start
        safe = np.where(d != 0,d,1.)
        a,b = (low-start)/safe,(high-start)/safe
        l = np.maximum(l,np.where(d!=0,np.minimum(a,b),0.))
        r = np.minimum(r,np.where(d!=0,np.maximum(a,b),1.))
    u = (l+r)/2
    X,Y,Z = x+u*(tx-x),y+u*(ty-y),H0+u*(h-H0)
    e = METRE_GUARD
    return ((0<u)&(u<1)&(X>bx+e)&(X<Bx-e)&(Y>by+e)&(Y<By-e)&(Z>e)&(Z<bh-e))


def blocked(lo,hi,t):
    """Only remove boxes whose four vertices have interior prism witnesses."""
    removed = np.zeros(len(lo),dtype=bool)
    for planes,b in zip(SHADOW[t],BLD.values()):
        # Hull planes are only a fast prescreen. A poor prescreen can cost time
        # but can never prove occlusion or delete a feasible box by itself.
        possible = ~removed
        for nx,ny,c in planes:
            possible &= (nx*(hi[:,0] if nx>0 else lo[:,0]) +
                         ny*(hi[:,1] if ny>0 else lo[:,1]) + c < 0.)
        idx = np.flatnonzero(possible)
        if len(idx):
            covered = np.ones(len(idx),dtype=bool)
            for x in [lo[idx,0],hi[idx,0]]:
                for y in [lo[idx,1],hi[idx,1]]:
                    covered &= corner_occluded(x,y,t,b)
            removed[idx[covered]] = True
    return removed


def box_cover(v,a,b,goal,partner):
    """b=None means certify initial arrival + slew at alert a only."""
    o = VEH[v]
    dim = 4 if b else 2
    # Round up to entire metres. The 5-s first observation cannot overlap travel.
    radius = int(np.ceil(SPEED*(goal-(DURATION if b else 0))))+1
    lo = [max(R,z-radius) for z in o]*(dim//2)
    hi = [min(SIDE-R,z+radius) for z in o]*(dim//2)
    boxes = np.array([lo+hi],dtype='float64')
    counts = dict(tested=0,time_pruned=0,shadow_pruned=0,max_depth=0)
    for depth in range(200):
        if len(boxes)==0:
            return counts
        counts['max_depth'] = depth
        counts['tested'] += len(boxes)
        plo,phi = boxes[:,:2],boxes[:,dim:dim+2]
        A = slew_lower(plo,phi,a)
        d0 = min_dist(plo,phi,*o)/SPEED
        if b:
            qlo,qhi = boxes[:,2:4],boxes[:,dim+2:dim+4]
            B = slew_lower(qlo,qhi,b)
            gap = np.maximum(np.maximum(plo-qhi, qlo-phi),0.)
            d1 = norm(gap[:,0],gap[:,1])/SPEED
            direct = min_dist(qlo,qhi,*o)/SPEED
            lower = DURATION+A+B+np.maximum(partner+d1, A+np.maximum(d0+d1,direct))
        else:
            lower = d0+A
        keep = lower < goal+TIME_GUARD
        counts['time_pruned'] += int(np.sum(~keep))
        boxes = boxes[keep]
        if not len(boxes):
            return counts
        bad = blocked(boxes[:,:2],boxes[:,dim:dim+2],a)
        if b:
            bad |= blocked(boxes[:,2:4],boxes[:,dim+2:dim+4],b)
        counts['shadow_pruned'] += int(np.sum(bad))
        boxes = boxes[~bad]
        if not len(boxes):
            return counts
        # Split each widest dimension, deterministically taking the first tie.
        k = np.argmax(boxes[:,dim:]-boxes[:,:dim],axis=1)
        rows = np.arange(len(boxes))
        mid = (boxes[rows,k]+boxes[rows,k+dim])*.5
        child = boxes.copy()
        child[rows,k] = mid
        boxes[rows,k+dim] = mid
        boxes = np.concatenate([boxes,child])
    raise RuntimeError((v,a,b,depth,len(boxes)))


def run(goal=GOAL):
    # For these three nominal bearings the centers have exact radical forms.
    with localcontext() as ctx:
        ctx.prec = 60
        d = Decimal
        c = d(50)
        r2,r3,r6 = [d(n).sqrt() for n in [2,3,6]]
        centers = {'T1':(c+20*r3,d(70)), 'T2':(c-45*r3/2,d('72.5')),
                   'T3':(c+38*(r6-r2)/4,c-38*(r6+r2)/4)}
        assert all(abs(d(TGT[t][i])-p[i]) < d('1e-12') for t,p in centers.items() for i in [0,1])
    companion = {v:box_cover(v,'T1',None,PARTNER,0.) for v in VEH if v!='V3'}
    chains = {f'{v}/{a}/{b}':box_cover(v,a,b,goal,PARTNER if (v,a)==('V3','T1') else 0.)
              for v in VEH for a,b in permutations(TGT,2)}
    return dict(lower_bound_s=goal,other_than_V3_T1_arrival_and_slew_lb_s=PARTNER,
                time_guard_s=TIME_GUARD,occlusion_interior_guard_m=METRE_GUARD,
                target_enclosure_pad_m=XY_PAD,atan_lower_guard_rad=ANGLE_PAD,
                companion_covers=companion,reuse_covers=chains)


if __name__ == '__main__':
    OUT.mkdir(exist_ok=True,parents=True)
    result = run(*map(float,sys.argv[1:]))
    (OUT/'certificate.json').write_text(json.dumps(result,indent=2)+'\n')
    counts = list(result['companion_covers'].values())+list(result['reuse_covers'].values())
    assert all(c['tested']==c['time_pruned']+c['shadow_pruned']+(c['tested']-1)//2 for c in counts)
    print(result['lower_bound_s'],sum(c['tested'] for c in counts),'boxes tested')
