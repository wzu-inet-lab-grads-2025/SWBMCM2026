"""Add rigorously relaxed first-partner readiness to ordered-pair box bounds."""
import heapq,itertools,json,math,time
import numpy as np
import bounds
from geometry import V,OUT,R

def single_lb(i,k,upper,tol=2e-6,max_nodes=80000):
    x,y=V[i];travel=3*upper
    root=(max(R,x-travel),min(100-R,x+travel),max(R,y-travel),min(100-R,y+travel))
    point=(x,x,y,y)
    def lb(b):return bounds.rect_distance(point,b)/3+bounds.slew_lower(b,k)
    heap=[(lb(root),root)];nodes=0
    while heap and heap[0][0]<upper-tol and nodes<max_nodes:
        lower,b=heapq.heappop(heap);nodes+=1
        axis=0 if b[1]-b[0]>b[3]-b[2] else 2;middle=(b[axis]+b[axis+1])/2
        for side in [0,1]:
            child=list(b);child[axis+side]=middle;child=tuple(child)
            if bounds.impossible_station(child,k):continue
            lower=lb(child)
            if lower<upper:heapq.heappush(heap,(lower,child))
    return {'lower_bound_s':min(upper,heap[0][0] if heap else upper)-1e-10,'upper_bound_s':upper,'nodes':nodes}

if __name__=='__main__':
    data=json.loads((OUT/'single_ready.json').read_text());single=[];start=time.time()
    for i in range(5):
        single.append([single_lb(i,k,data[i][k]['ready_s']) for k in range(3)])
    partner=np.array([[min(single[j][k]['lower_bound_s'] for j in range(5) if j!=i) for k in range(3)] for i in range(5)])
    def upgraded(cell,i,a,b):
        p,q=cell[:4],cell[4:];x,y=V[i];origin=(x,x,y,y)
        sa=bounds.slew_lower(p,a)
        return 5+max(bounds.rect_distance(origin,p)/3+sa,partner[i,a])+sa+bounds.rect_distance(p,q)/3+bounds.slew_lower(q,b)
    bounds.pair_cell_bound=upgraded
    upper=json.loads((OUT/'summary.json').read_text())['q22']['latest_start_s']
    result=bounds.lower_bound_run(cutoff=upper,tolerance=.00005,max_pops=800000)
    result['single_readiness_bounds']=single;result['first_partner_bounds_s']=partner.tolist()
    result['method']='Pigeonhole + first-partner single-station box bounds + 30 ordered-pair continuous relaxations; full shadow and disk pruning; not interval arithmetic.'
    result['total_elapsed_s']=time.time()-start
    (OUT/'q22_strong_lower_bound.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
