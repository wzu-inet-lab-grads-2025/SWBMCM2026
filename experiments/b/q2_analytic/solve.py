"""Reproduce the continuous precision optimum, moving schedule, and heuristics."""
from itertools import combinations, permutations, product
import json
import math

import numpy as np
from scipy.optimize import minimize

from model import (BLD, DURATION, H0, OUT, R, SCENE, SHADOW, SPEED, TGT, VEH,
                   angles, geometry, job, make_leg, path_ok, segment_clearance,
                   slew, visible)


def solve_precision():
    t = TGT['T2']
    r = (t[2]-H0)/math.tan(math.radians(SCENE['elevation_deg'][1]))
    p, q = (t[0]-r,t[1]), (t[0],t[1]+r)
    legs = [make_leg(v,VEH[v],s,0.,'T2') for v,s in zip(('V3','V5'),(p,q))]
    assert max(l['travel_s'] for l in legs) <= SCENE['q2_1']['travel_limit_s']
    geo = geometry(p,q,'T2')
    normals = np.array([[-math.sin(angles(s,'T2')[0]), math.cos(angles(s,'T2')[0])] for s in (p,q)])
    jacobian = normals/np.array(geo['ranges_m'])[:,None]
    sigma = math.radians(SCENE['q2_1']['bearing_sd_deg'])
    cov = sigma**2 * np.linalg.inv(jacobian.T @ jacobian)
    for l in legs:
        l['vehicle_min_building_clearance_m'] = min(segment_clearance(*l['path'],b) for b in BLD.values())-R
    return dict(target='T2',target_xyz=t,legs=legs,**geo,covariance_m2=cov.tolist(),
                minimum_range_m=r,continuous_rmse_lb_m=sigma*math.sqrt(2)*r)


def solve_moving(margin):
    # Optimize the T1->T3 reuse chain. Impose a positive distance outside each
    # shadow halfspace union at q. Independent finite-prism checks follow.
    v = 'V3'
    o = VEH[v]
    companion_ready = slew(VEH['V5'],'T1')

    def objective(x):
        p,q = x[:2],x[2:4]
        return x[4]+DURATION+slew(p,'T1')+math.dist(p,q)/SPEED+slew(q,'T3')

    def constraints(x):
        p,q = x[:2],x[2:4]
        return [x[4]-companion_ready, x[4]-math.dist(o,p)/SPEED-slew(p,'T1'),
                *(max(nx*q[0]+ny*q[1]+c for nx,ny,c in polygon)-margin for polygon in SHADOW['T3'])]

    result = minimize(objective,[15.2,86.9,17.,89.,companion_ready],method='SLSQP',
                      constraints=[dict(type='ineq',fun=constraints)],
                      options={'ftol':1e-13,'maxiter':300})
    assert min(constraints(result.x)) > -1e-9 and result.success, result.message
    p,q = tuple(result.x[:2]),tuple(result.x[2:4])
    north = (88.,71.)
    j1 = job('T1',[make_leg(v,o,p,0.,'T1'),make_leg('V5',VEH['V5'],VEH['V5'],0.,'T1')])
    j2 = job('T2',[make_leg('V1',VEH['V1'],VEH['V1'],0.,'T2'),make_leg('V4',VEH['V4'],north,0.,'T2')])
    j3 = job('T3',[make_leg('V2',VEH['V2'],VEH['V2'],0.,'T3'),make_leg(v,p,q,j1['legs'][0]['reset_end_s'],'T3')])
    jobs = sorted([j1,j2,j3],key=lambda j:j['start_s'])
    return dict(jobs=jobs,latest_start_s=jobs[-1]['start_s'],shadow_margin_m=margin,
                total_travel_m=sum(l['distance_m'] for j in jobs for l in j['legs']))


def stationary_enumeration():
    pairs = {t:[(i,j) for i,j in combinations(VEH,2) if visible(VEH[i],t) and visible(VEH[j],t)
                and 1e-7 < geometry(VEH[i],VEH[j],t)['gamma_deg'] < 180-1e-7] for t in TGT}
    best = math.inf
    count = 0
    for order in permutations(TGT):
        for seq in product(*(pairs[t] for t in order)):
            state = {v:0. for v in VEH}
            score = 0.
            for t,vehicles in zip(order,seq):
                d = [slew(VEH[v],t) for v in vehicles]
                start = max(state[v]+s for v,s in zip(vehicles,d))
                score = max(start,score)
                for v,s in zip(vehicles,d):
                    state[v] = start+DURATION+s
            best = min(best,score)
            count += 1
    return dict(best_latest_start_s=best,n_enumerated=count,n_visible_pairs={t:len(k) for t,k in pairs.items()})


def compare(stations):
    """All cars may use any of the same sites reachable by a straight leg."""
    reachable = {(origin,p):path_ok(origin,p) for origin,p in product(stations,repeat=2)}
    obs = {(p,t):visible(p,t) for p,t in product(stations,TGT)}
    acceptable = {}
    for t in TGT:
        for p,q in permutations(stations,2):
            if not obs[p,t] or not obs[q,t] or math.dist(p,q) < 2*R:
                continue
            g = geometry(p,q,t)['gamma_deg']
            if min(g,180-g) > 1e-7:
                acceptable[t,p,q] = True

    representative_plans = []

    def greedy(order,rule,tie):
        state = {v:(0.,p) for v,p in VEH.items()}
        jobs=[]
        for t in order:
            options=[]
            for i,j in combinations(VEH,2):
                i_release,i_pos = state[i]
                j_release,j_pos = state[j]
                for p,q in permutations(stations,2):
                    if ((t,p,q) not in acceptable or not reachable[i_pos,p] or not reachable[j_pos,q]):
                        continue
                    u,v = math.dist(i_pos,p),math.dist(j_pos,q)
                    ready = max(i_release+u/SPEED+slew(p,t),j_release+v/SPEED+slew(q,t))
                    distance = u+v if rule=='sum' else max(u,v)
                    if rule=='ready':
                        key = (round(ready,9),round(u+v,9),i,j,p,q)
                    else:
                        key = (round(distance,9), round(ready,9) if tie=='ready' else 0,i,j,p,q)
                    options.append((key,i,j,p,q))
            _,i,j,p,q = min(options)
            legs = [make_leg(k,state[k][1],s,state[k][0],t) for k,s in zip((i,j),(p,q))]
            mission = job(t,legs)
            for l in legs:
                state[l['vehicle']] = (l['reset_end_s'],l['station'])
            jobs.append(mission)
        latest = max(j['start_s'] for j in jobs)
        if order == tuple(TGT):
            representative_plans.append(dict(order=order,rule=rule,tie=tie,latest_start_s=latest,
                                             jobs=sorted(jobs,key=lambda j:j['start_s'])))
        return dict(order=order,rule=rule,tie=tie,latest_start_s=latest,
                    starts=[j['start_s'] for j in jobs],pairs=[[k['vehicle'] for k in j['legs']] for j in jobs],
                    station_pairs=[[k['station'] for k in j['legs']] for j in jobs],
                    distances_m=[[k['distance_m'] for k in j['legs']] for j in jobs])

    rows = [greedy(order,rule,tie) for order in permutations(TGT) for rule,tie in
            [('sum','id'),('max','id'),('sum','ready'),('max','ready'),('ready','sum')]]
    return rows,representative_plans


def write(name,data):
    (OUT/name).write_text(json.dumps(data,indent=2,ensure_ascii=False,allow_nan=False)+'\n')


def main():
    OUT.mkdir(exist_ok=True,parents=True)
    q21 = solve_precision()
    q22 = solve_moving(1e-5)
    alternate = solve_moving(.1)
    stations = sorted(set(VEH.values()) | {l['station'] for j in q22['jobs'] for l in j['legs']})
    rows,baseline_plans = compare(stations)
    stationary = stationary_enumeration()
    q22['stationary_enumeration'] = stationary
    q22['gain_over_stationary_s'] = stationary['best_latest_start_s']-q22['latest_start_s']
    q22['continuous_bound_certificate_file'] = 'certificate.json'
    write('summary.json',dict(q21=q21,q22=q22))
    write('larger_margin.json',alternate)
    write('comparison.json',dict(stations=stations,rows=rows))
    write('baseline_timelines.json',baseline_plans)
    print('Q2(1) RMSE',q21['horizontal_rmse_m'],'m')
    print('Q2(2) U',q22['latest_start_s'],'s; stationary',stationary['best_latest_start_s'],'s')
    for j in q22['jobs']:
        print(j['target'],j['start_s'],[l['vehicle'] for l in j['legs']])
    for k in range(0,len(rows),5):
        print(rows[k]['order'],[round(row['latest_start_s'],6) for row in rows[k:k+5]])


if __name__ == '__main__':
    main()
