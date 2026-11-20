"""Recompute fair greedy comparisons and exact stationary schedule enumeration."""
import csv,itertools,json,math
import numpy as np
from geometry import *

PAIRS=list(itertools.combinations(range(5),2))

def greedy(candidates,order,criterion,tie):
    # Shared station set, same admissibility and exact same feasible direct routes.
    pos=V.copy(); free=np.zeros(5); jobs=[]
    for k in order:
        options=[]
        for i in range(5):
            opts=[]
            for index,p in enumerate(candidates):
                if observable(p,k) and segment_ok(pos[i],p):
                    d=math.dist(pos[i],p); arrival=free[i]+d/3; s=slew(p,k)
                    opts.append((index,p,d,arrival,s,arrival+s))
            options.append(opts)
        pool=[]
        for i,j in PAIRS:
            for a,b in itertools.product(options[i],options[j]):
                if not pair_ok(a[1],b[1],k): continue
                start=max(a[5],b[5]); total=a[2]+b[2]; longest=max(a[2],b[2]); ident=(i,j,a[0],b[0])
                primary={'total_distance':total,'max_distance':longest,'earliest_ready':start}[criterion]
                # Ties at 1e-9 in primary; if readiness tie-break same 1e-9 precision.
                key=(round(primary,9),)+((round(start,9),) if tie=='ready' and criterion!='earliest_ready' else ())+ident
                pool.append((key,i,j,a,b,start,total,longest))
        if not pool: return None
        chosen=min(pool,key=lambda row:row[0]); key,i,j,a,b,start,total,longest=chosen
        legs=[]
        for car,rec in [(i,a),(j,b)]:
            ix,p,d,arrival,s,ready=rec
            legs.append({'vehicle':f'V{car+1}','station':list(p),'path':[pos[car].tolist(),list(p)],
                         'depart_s':float(free[car]),'distance_m':d,'travel_s':d/3,'arrival_s':arrival,
                         'slew_s':s,'ready_s':ready,'wait_s':start-ready,
                         'observation_end_s':start+5,'reset_end_s':start+5+s})
            pos[car]=p;free[car]=start+5+s
        jobs.append({'target':f'T{k+1}','start_s':start,'legs':legs,
                     'primary_ties':int(sum(row[0][0]==key[0] for row in pool)),
                     'selected_added_distance_m':total,'selected_max_added_distance_m':longest})
    return {'jobs':sorted(jobs,key=lambda job:(job['start_s'],job['target'])),
            'latest_start_s':max(j['start_s'] for j in jobs),
            'total_distance_m':sum(l['distance_m'] for j in jobs for l in j['legs']),
            'allocation_order':[f'T{k+1}' for k in order],'criterion':criterion,'tie':tie}

def stationary_exhaustive():
    possible={k:[pair for pair in PAIRS if all(observable(V[i],k) for i in pair) and pair_ok(*V[list(pair)],k)] for k in range(3)}
    best=None; n=0
    for assigned in itertools.product(*(possible[k] for k in range(3))):
        for order in itertools.permutations(range(3)):
            free=np.zeros(5);starts={}; jobs=[]
            for k in order:
                pair=assigned[k]; start=max(free[i]+slew(V[i],k) for i in pair);starts[k]=start;legs=[]
                for i in pair:
                    s=slew(V[i],k);d=float(free[i]);p=V[i].tolist()
                    legs.append({'vehicle':f'V{i+1}','station':p,'path':[p,p],'depart_s':d,'arrival_s':d,
                                 'slew_s':s,'ready_s':d+s,'wait_s':start-d-s,'observation_end_s':start+5,
                                 'reset_end_s':start+5+s,'distance_m':0.,'travel_s':0.})
                    free[i]=start+5+s
                jobs.append({'target':f'T{k+1}','start_s':start,'legs':legs})
            n+=1;value=max(starts.values())
            if best is None or value<best['latest_start_s']:
                best={'latest_start_s':value,'jobs':sorted(jobs,key=lambda j:(j['start_s'],j['target'])),'total_distance_m':0.}
    return {'count':n,'possible_pairs':{f'T{k+1}':[[f'V{i+1}' for i in pair] for pair in value] for k,value in possible.items()},'best':best}

def run():
    candidate_sets={'initial':V.tolist(),'grid20':[[x,y] for x in [10.,30.,50.,70.,90.] for y in [10.,30.,50.,70.,90.]]}
    summary_path=OUT/'summary.json'
    if summary_path.exists():
        q22=json.loads(summary_path.read_text())['q22']
        extras=[l['station'] for j in q22['jobs'] for l in j['legs']]
        candidate_sets['initial_plus_searched']=V.tolist()+[p for i,p in enumerate(extras) if p not in V.tolist()+extras[:i]]
    plans=[]
    for name,points in candidate_sets.items():
        for order in itertools.permutations(range(3)):
            for criterion,tie in [('total_distance','id'),('total_distance','ready'),('max_distance','id'),
                                  ('max_distance','ready'),('earliest_ready','id')]:
                plan=greedy(points,order,criterion,tie);plan['candidate_set']=name;plans.append(plan)
    result={'candidate_sets':candidate_sets,'tie_tolerance_units':1e-9,'plans':plans,'stationary':stationary_exhaustive()}
    (OUT/'baselines.json').write_text(json.dumps(result,indent=2))
    columns=['candidate_set','allocation_order','criterion','tie','latest_start_s','total_distance_m']
    with (OUT/'baselines.csv').open('w') as f:
        w=csv.DictWriter(f,columns);w.writeheader()
        for p in plans:w.writerow({key:('>'.join(p[key]) if key=='allocation_order' else p[key]) for key in columns})
    print('plans',len(plans),'stationary',result['stationary']['best']['latest_start_s'])

if __name__=='__main__':run()
