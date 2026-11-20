"""Independent continuous lower bound using globally valid cell bounds.
No optimizer values are used as lower bounds. Each car must serve an ordered
pair (pigeonhole); relax partners, optical/building/travel constraints.
"""
import heapq, itertools, json, math, time
import numpy as np
from geometry import V,T,R,OMEGA,OUT,BOXES
from scipy.spatial import ConvexHull
SHADOW=[]
for k in range(3):
    row=[]
    for x0,y0,x1,y1,h in BOXES:
        corners=np.array([[x0,y0],[x0,y1],[x1,y0],[x1,y1]])
        factor=(T[k,2]-2)/(T[k,2]-h)
        vertices=np.vstack((corners,T[k,:2]+factor*(corners-T[k,:2])))
        row.append(ConvexHull(vertices).equations.tolist())
    SHADOW.append(row)


def rect_distance(a,b):
    # boxes (xmin,xmax,ymin,ymax)
    return math.hypot(max(a[0]-b[1],b[0]-a[1],0.), max(a[2]-b[3],b[2]-a[3],0.))

def bearing_min_abs(box,target):
    """Minimal |atan2(Ty-y,Tx-x)| on the whole rectangle.
    If it contains target, use zero. Otherwise directions span a cone;
    crossing due east gives zero, and otherwise one of four corners attains min.
    """
    x0,x1,y0,y1=box; tx,ty=target
    if y0 <= ty <= y1 and x0 <= tx: return 0.
    return min(abs(math.atan2(ty-y,tx-x)) for x in [x0,x1] for y in [y0,y1])

def slew_lower(box,k):
    tx,ty,h=T[k]
    far=math.hypot(max(abs(tx-box[0]),abs(tx-box[1])),max(abs(ty-box[2]),abs(ty-box[3])))
    return max(bearing_min_abs(box,(tx,ty)),math.atan2(h-2.,far))/OMEGA

def pair_cell_bound(cell,i,a,b):
    p,q=cell[:4],cell[4:]
    origin=(V[i,0],V[i,0],V[i,1],V[i,1])
    return 5+rect_distance(origin,p)/3+2*slew_lower(p,a)+rect_distance(p,q)/3+slew_lower(q,b)

def impossible_station(box,k):
    tx,ty,h=T[k]; x0,x1,y0,y1=box
    far=math.hypot(max(abs(tx-x0),abs(tx-x1)),max(abs(ty-y0),abs(ty-y1)))
    if far < (h-2)/math.tan(math.radians(80))-1e-12: return True
    for equations in SHADOW[k]:
        # All four corners in a convex closed shadow => whole cell occluded.
        if all(max(ax*x0,ax*x1)+max(ay*y0,ay*y1)+c < -1e-12 for ax,ay,c in equations):
            return True
    for a,c,b,d,_ in BOXES:
        # Point-to-convex-set distance is convex, hence max on box at a vertex.
        far_from_box=max(math.hypot(max(a-x,0,x-b),max(c-y,0,y-d)) for x in [x0,x1] for y in [y0,y1])
        if far_from_box < R-1e-12: return True
    return False

def lower_bound_run(cutoff=10.968984244, tolerance=.002, max_pops=400000):
    """At every stop global heap minimum is a valid (non interval-arithmetic) bound.
    If a better-than-cutoff plan exists, reused car's total travel <=3(cutoff-5),
    so both stations lie in origin-centered axis-aligned box of that radius.
    Relaxation f=5+d(v,p)/3+2*s_a(p)+d(p,q)/3+s_b(q).
    ignores first-partner synchronization and all obstacles.
    """
    heap=[]; serial=itertools.count(); pair_info={}; pops=0
    travel=3*(cutoff-5)
    for i in range(5):
        x,y=V[i]; box=(max(R,x-travel),min(100-R,x+travel),max(R,y-travel),min(100-R,y+travel))
        for a,b in itertools.permutations(range(3),2):
            cell=box+box; lb=pair_cell_bound(cell,i,a,b)
            heapq.heappush(heap,(lb,next(serial),i,a,b,cell))
    history=[]; start=time.time(); frozen=float('inf')
    while heap and heap[0][0] < min(cutoff-tolerance,frozen) and pops<max_pops:
        lb,_,i,a,b,cell=heapq.heappop(heap); pops+=1
        if pops in [1,100,1000,10000,100000,200000,400000]:
            history.append({'pops':pops,'lower_bound_s':lb,'heap_size':len(heap),'elapsed_s':time.time()-start})
        widths=[cell[j+1]-cell[j] for j in [0,2,4,6]]
        axis=int(np.argmax(widths))*2; middle=(cell[axis]+cell[axis+1])/2
        if max(widths)<tolerance*.01:
            frozen=min(frozen,lb)
            continue
        for side in [0,1]:
            child=list(cell); child[axis+side]=middle; child=tuple(child)
            if impossible_station(child[:4],a) or impossible_station(child[4:],b): continue
            bound=pair_cell_bound(child,i,a,b)
            if bound<cutoff: heapq.heappush(heap,(bound,next(serial),i,a,b,child))
    lower=min(cutoff,frozen,heap[0][0] if heap else cutoff)
    return {'lower_bound_s':lower-1e-10,'conditional_cutoff_s':cutoff,'nodes_split':pops,
            'remaining_cells':len(heap),'elapsed_s':time.time()-start,'history':history,
            'limiting_pair':{'vehicle':f'V{heap[0][2]+1}','first':f'T{heap[0][3]+1}',
                             'second':f'T{heap[0][4]+1}','box':heap[0][5]} if heap else None,
            'method':'Pigeonhole + 30 continuous ordered-pair relaxations; box branch-and-bound; conservative 1e-10 floating guard, not directed rounding.'}

if __name__=='__main__':
    result=lower_bound_run()
    (OUT/'q22_lower_bound.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
