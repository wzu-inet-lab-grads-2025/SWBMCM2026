"""Exact exhaustive branch/prune search on the SHARED finite station set.
All car-pair assignments, all 6 task allocation orders, zero or multiple reuses.
No restriction of 30 seconds. Uses only the incumbent objective for safe pruning.
"""
import itertools,json,math,time
import numpy as np
from geometry import *

if __name__=='__main__':
    start_time=time.time();data=json.loads((OUT/'baselines.json').read_text())
    points=np.array(data['candidate_sets']['initial_plus_searched'])
    n=len(points);distance=np.full((n,n),float('inf'));turn=np.zeros((3,n));valid=np.zeros((3,n),bool)
    pair_valid=np.zeros((3,n,n),bool)
    for i,p in enumerate(points):
        for j,q in enumerate(points):
            if segment_ok(p,q):distance[i,j]=math.dist(p,q)
        for k in range(3):
            valid[k,i]=observable(p,k);turn[k,i]=slew(p,k)
            for j,q in enumerate(points):pair_valid[k,i,j]=pair_ok(p,q,k)
    # Future-feasibility pruning must allow indirect reachable paths through
    # remaining task stations; using a forbidden direct edge (=inf) is unsafe.
    # A graph shortest-path relaxation is <= any subsequent task-route length.
    optimistic_distance=distance.copy()
    for mid in range(n):
        optimistic_distance=np.minimum(optimistic_distance,optimistic_distance[:,mid,None]+optimistic_distance[None,mid,:])
    incumbent=json.loads((OUT/'summary.json').read_text())['q22']['latest_start_s']
    upper=incumbent+1e-8;best=math.inf;best_records=None;nodes=[0,0,0,0];all_order_results=[]
    def visit(order,depth,pos,free,latest,records):
        global best,best_records
        nodes[depth]+=1
        if depth==3:
            if latest<best:
                best=latest;best_records=records
            return
        k=order[depth];options=[]
        for i in range(5):
            opts=[]
            for st in range(n):
                if valid[k,st]:
                    arrival=free[i]+distance[pos[i],st]/3;ready=arrival+turn[k,st]
                    if ready<=min(best+1e-8,upper):opts.append((st,ready,arrival))
            options.append(opts)
        for i,j in itertools.combinations(range(5),2):
            for a,b in itertools.product(options[i],options[j]):
                s,t=a[0],b[0]
                if not pair_valid[k,s,t]:continue
                common=max(a[1],b[1]);objective=max(latest,common)
                if objective>best+1e-8:continue
                pp=pos.copy();ff=free.copy();pp[i]=s;pp[j]=t
                ff[i]=common+5+turn[k,s];ff[j]=common+5+turn[k,t]
                # For every unallocated target, at least two different cars have a
                # feasible future station ready by incumbent, ignoring competition.
                possible=True
                for remaining in order[depth+1:]:
                    count=0
                    for car in range(5):
                        if any(valid[remaining,ss] and ff[car]+optimistic_distance[pp[car],ss]/3+turn[remaining,ss]<=best+1e-8 for ss in range(n)): count+=1
                    if count<2:possible=False;break
                if not possible:continue
                visit(order,depth+1,pp,ff,objective,records+[{'target':k,'vehicles':[i,j],'stations':[s,t],'start_s':common}])
    for order in itertools.permutations(range(3)):
        before=nodes.copy();visit(order,0,list(range(5)),[0.]*5,0.,[])
        all_order_results.append({'order':[x+1 for x in order],'nodes_delta':[a-b for a,b in zip(nodes,before)]})
    if best_records is None or best > upper:
        raise ValueError('Finite search could not reproduce a feasible incumbent plan')
    result={'candidate_count':n,'best_latest_start_s':best,'nodes_by_depth':nodes,'orders':all_order_results,
            'plan_indices':best_records,'elapsed_s':time.time()-start_time,'scope':'all assignments/reuses and orders on identical 9-point common candidate set, certified feasible direct routes; 1e-8 pruning guard'}
    (OUT/'finite_search.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
