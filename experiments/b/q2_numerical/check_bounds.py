"""Numerical regression of analytic lower-bound formulas; not a formal proof."""
import json,math
import numpy as np
import bounds
from geometry import OUT,V,slew,observable

if __name__=='__main__':
    rng=np.random.default_rng(20261121);checks=0;violations=[];false_pruning=[]
    for iteration in range(20000):
        rectangles=[]
        for _ in range(2):
            x=np.sort(rng.uniform(1.5,98.5,2));y=np.sort(rng.uniform(1.5,98.5,2))
            rectangles.append((x[0],x[1],y[0],y[1]))
        pbox,qbox=rectangles;i=int(rng.integers(5));a,b=rng.choice(3,2,replace=False)
        p=np.array([rng.uniform(pbox[0],pbox[1]),rng.uniform(pbox[2],pbox[3])])
        q=np.array([rng.uniform(qbox[0],qbox[1]),rng.uniform(qbox[2],qbox[3])])
        actual=5+math.dist(V[i],p)/3+2*slew(p,a)+math.dist(p,q)/3+slew(q,b)
        lower=bounds.pair_cell_bound(pbox+qbox,i,a,b)
        checks+=1
        if lower>actual+1e-10:violations.append({'cell':pbox+qbox,'lb':lower,'actual':actual})
        for box,k in [(pbox,a),(qbox,b)]:
            if bounds.impossible_station(box,k):
                for xx in np.linspace(box[0],box[1],3):
                    for yy in np.linspace(box[2],box[3],3):
                        checks+=1
                        if observable([xx,yy],k):false_pruning.append({'box':box,'target':int(k),'point':[xx,yy]})
    result={'seed':20261121,'random_cells':20000,'checks':checks,'bound_violations':violations,'false_pruning':false_pruning}
    assert not violations and not false_pruning
    (OUT/'bound_regression.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
