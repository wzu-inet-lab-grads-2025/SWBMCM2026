"""Constructive search, no code shared with baseline/other experiment implementations."""
import itertools, json, math, time
import numpy as np
from scipy.optimize import minimize, differential_evolution, minimize_scalar
from scipy.spatial import ConvexHull
from geometry import *

# Signed halfspace residual: positive = strictly outside that closed shadow.
SHADOW=[]
for k in range(3):
    row=[]
    for x0,y0,x1,y1,h in BOXES:
        corners=np.array([[x0,y0],[x0,y1],[x1,y0],[x1,y1]])
        factor=(T[k,2]-2)/(T[k,2]-h)
        vertices=np.vstack((corners,T[k,:2]+factor*(corners-T[k,:2])))
        row.append(ConvexHull(vertices).equations)
    SHADOW.append(row)

def constraints(p,k,margin=1e-7):
    p=np.asarray(p)
    d=T[k,:2]-p
    return np.array([*(point_box_distance(p,b)-R for b in BOXES),
                     np.linalg.norm(d)-(T[k,2]-2)/math.tan(math.radians(80)),
                     *(np.max(eq[:,:2]@p+eq[:,2])-margin for eq in SHADOW[k])])

def numerical_single_ready():
    """Multi-start station search. Euclidean path cost, route checked afterwards."""
    rng=np.random.default_rng(20261120)
    out=[]
    for i in range(5):
        row=[]
        for k in range(3):
            def f(p): return np.linalg.norm(p-V[i])/3+slew(p,k)
            starts=[V[i]]+[np.clip(V[i]+rng.uniform(-25,25,2),R,100-R) for _ in range(15)]
            trials=[]
            bounds=[(max(R,v-33),min(100-R,v+33)) for v in V[i]]
            for x in starts:
                opt=minimize(f,np.clip(x,*np.array(bounds).T),method='SLSQP',bounds=bounds,
                             constraints={'type':'ineq','fun':lambda p:constraints(p,k)},
                             options={'maxiter':180,'ftol':1e-11})
                if min(constraints(opt.x,k)) >= -1e-8 and observable(opt.x,k) and segment_ok(V[i],opt.x):
                    trials.append((float(opt.fun),opt.x.tolist(),bool(opt.success)))
            if observable(V[i],k): trials.append((slew(V[i],k),V[i].tolist(),True))
            best=min(trials) if trials else (float('inf'),None,False)
            row.append({'vehicle':f'V{i+1}','target':f'T{k+1}','ready_s':best[0],
                        'station':best[1], 'valid_trials':len(trials)})
        out.append(row)
    return out

def optimize_reuse(i,a,b,partner_ready,seed=1,starts_n=16):
    # Minimize the reused car's second readiness; first partner readiness is fixed.
    # Relaxation ignores transit buildings, then each successful solution is route-audited.
    def f(x):
        p,q=x[:2],x[2:]
        sa=slew(p,a)
        return max(np.linalg.norm(p-V[i])/3+sa,partner_ready)+5+sa+np.linalg.norm(q-p)/3+slew(q,b)
    def con(x): return np.r_[constraints(x[:2],a),constraints(x[2:],b)]
    rng=np.random.default_rng(seed)
    bounds=[(max(R,v-18),min(100-R,v+18)) for v in V[i]]*2
    starts=[np.tile(V[i],2)]
    starts.extend(np.clip(np.tile(V[i],2)+rng.uniform(-8,8,4),*np.array(bounds).T) for _ in range(starts_n-1))
    best=None; logs=[]
    for x in starts:
        opt=minimize(f,x,method='SLSQP',bounds=bounds,constraints={'type':'ineq','fun':con},
                     options={'maxiter':250,'ftol':2e-11})
        p,q=opt.x[:2],opt.x[2:]
        feasible=(min(con(opt.x))>=-1e-8 and observable(p,a) and observable(q,b)
                  and segment_ok(V[i],p) and segment_ok(p,q))
        logs.append({'fun':float(opt.fun),'success':bool(opt.success),'feasible':bool(feasible)})
        if feasible and (best is None or opt.fun<best['second_ready_s']):
            best={'second_ready_s':float(opt.fun),'p':p.tolist(),'q':q.tolist()}
    return best,logs

def build_plan(assignment, stations, order):
    pos=V.copy(); free=np.zeros(5); jobs=[]
    for k in order:
        legs=[]
        for i in assignment[k]:
            p=np.array(stations[k,i]); path=[pos[i].tolist(),p.tolist()]
            assert segment_ok(*path) and observable(p,k)
            distance=path_length(path); arrival=free[i]+distance/3; s=slew(p,k)
            legs.append({'vehicle':f'V{i+1}','station':p.tolist(),'path':path,'depart_s':float(free[i]),
                         'distance_m':distance,'travel_s':distance/3,'arrival_s':float(arrival),
                         'slew_s':s,'ready_s':float(arrival+s)})
        assert pair_ok(*[leg['station'] for leg in legs],k)
        start=max(leg['ready_s'] for leg in legs)
        for leg in legs:
            i=int(leg['vehicle'][1:])-1
            leg.update(wait_s=start-leg['ready_s'],observation_end_s=start+5,reset_end_s=start+5+leg['slew_s'])
            free[i]=leg['reset_end_s']; pos[i]=leg['station']
        jobs.append({'target':f'T{k+1}','start_s':start,'legs':legs,
                     'geometry':pair_metrics(*[leg['station'] for leg in legs],k)})
    jobs.sort(key=lambda j:(j['start_s'],j['target']))
    return {'latest_start_s':max(j['start_s'] for j in jobs),'jobs':jobs,
            'total_distance_m':sum(l['distance_m'] for j in jobs for l in j['legs']),
            'idle_vehicles':[f'V{i+1}' for i in range(5) if not any(i in x for x in assignment.values())]}

def single_reuse_search(single):
    """All 360 assignments with five used cars (5*6*4*3); station multi-start."""
    records=[]; best_plan=None; cache={}
    for i in range(5):
        others=[j for j in range(5) if j!=i]
        for a,b in itertools.permutations(range(3),2):
            c=3-a-b
            for partner_a in others:
                pa=single[partner_a][a]
                if not np.isfinite(pa['ready_s']) or pa['ready_s']+5 >= 10.968984245: continue
                key=(i,a,b,partner_a)
                found, logs=optimize_reuse(i,a,b,pa['ready_s'],seed=2026+i*50+a*10+b*3+partner_a)
                cache[str(key)]={'best':found,'trials':logs}
                if found is None: continue
                for partner_b in others:
                    if partner_b==partner_a: continue
                    rest=[j for j in others if j not in [partner_a,partner_b]]
                    assignment={a:[i,partner_a],b:[i,partner_b],c:rest}
                    stations={(a,i):found['p'],(b,i):found['q'],(a,partner_a):pa['station'],
                              (b,partner_b):single[partner_b][b]['station']}
                    for j in rest: stations[c,j]=single[j][c]['station']
                    if any(x is None for x in stations.values()): continue
                    try: plan=build_plan(assignment,stations,[a,b,c])
                    except AssertionError: continue
                    records.append({'reuse':i+1,'ordered_tasks':[a+1,b+1],'partner_a':partner_a+1,
                                    'partner_b':partner_b+1,'last_s':plan['latest_start_s']})
                    if best_plan is None or plan['latest_start_s']<best_plan['latest_start_s']:
                        best_plan=plan
                        print('new best',plan['latest_start_s'],i,a,b,partner_a,partner_b,flush=True)
    return best_plan,records,cache

def q21_solve():
    k=1; radius=(T[k,2]-2)/math.tan(math.radians(80))
    # Numerical scan on the analytically optimal orthogonal equal-range family.
    router=Router(); candidates=[]
    for deg in np.arange(0,360,.25):
        p=T[k,:2]+radius*np.array([math.cos(math.radians(deg)),math.sin(math.radians(deg))])
        q=T[k,:2]+radius*np.array([math.cos(math.radians(deg+90)),math.sin(math.radians(deg+90))])
        if not observable(p,k) or not observable(q,k): continue
        # Use direct paths for this optional total-distance tie-break.
        for i,j in itertools.permutations(range(5),2):
            if math.dist(V[i],p)<=90 and math.dist(V[j],q)<=90 and segment_ok(V[i],p) and segment_ok(V[j],q):
                candidates.append((math.dist(V[i],p)+math.dist(V[j],q),deg,i,j))
    candidates.sort()
    # Keep accuracy optimum exact, avoid an unnecessary numerical optical-boundary
    # tie solution. Select a 10-degree representative with >=0.1 m conservative
    # signed shadow/disk clearance, then shortest sum of feasible direct routes.
    safe=[]
    for candidate in candidates:
        _,angle,car1,car2=candidate
        if abs(angle/10-round(angle/10))>1e-10: continue
        pts=[T[k,:2]+radius*np.array([math.cos(math.radians(angle+off)),math.sin(math.radians(angle+off))]) for off in [0,90]]
        if all(min(point_box_distance(pt,box)-R for box in BOXES)>=.1 and
               min(max(eq[:,:2]@pt+eq[:,2]) for eq in SHADOW[k])>=.1 for pt in pts):safe.append(candidate)
    _,deg,i,j=min(safe)
    def stations(angle):
        theta=math.radians(angle)
        return (T[k,:2]+radius*np.array([math.cos(theta),math.sin(theta)]),
                T[k,:2]+radius*np.array([-math.sin(theta),math.cos(theta)]))
    p,q=stations(deg)
    legs=[]
    for vehicle,station in [(i,p),(j,q)]:
        path=router.route(V[vehicle],station)
        legs.append({'vehicle':f'V{vehicle+1}','station':station.tolist(),'path':path,
                     'distance_m':path_length(path),'travel_s':path_length(path)/3})
    metrics=pair_metrics(p,q,k)
    return dict(target='T2',legs=legs,**metrics,elevation_deg=[math.degrees(angles(x,k)[1]) for x in [p,q]],
                lower_bound_rmse_m=math.sqrt(2)*math.radians(1)*radius,
                scan={'angular_step_deg':.25,'feasible_vehicle_station_pairs':len(candidates),'safe_10_degree_candidates':len(safe),'selected_phi_deg':float(deg)},
                optimum_scope='continuous global first-order RMSE; tie: 10-degree family, 0.1 m geometric/shadow margin, then total direct distance')

def noise_validation(q21):
    rng=np.random.default_rng(20261120); n=1000000
    p,q=[np.array(l['station']) for l in q21['legs']]
    a,b=[angles(x,1)[0] for x in [p,q]]
    noise=rng.normal(0,math.radians(1),(n,2))
    u=np.c_[np.cos(a+noise[:,0]),np.sin(a+noise[:,0])]
    v=np.c_[np.cos(b+noise[:,1]),np.sin(b+noise[:,1])]
    w=q-p; den=u[:,0]*v[:,1]-u[:,1]*v[:,0]
    t=(w[0]*v[:,1]-w[1]*v[:,0])/den
    estimates=p+t[:,None]*u; err=estimates-T[1,:2]
    norm=np.linalg.norm(err,axis=1)
    return {'seed':20261120,'n':n,'empirical_horizontal_rmse_m':float(np.sqrt(np.mean(norm**2))),
            'bias_xy_m':np.mean(err,axis=0).tolist(),'empirical_covariance_m2':np.cov(err.T).tolist(),
            'radial_error_quantiles_m':{str(x):float(np.quantile(norm,x)) for x in [.5,.9,.95,.99,.999]},
            'max_sample_error_m':float(norm.max()),
            'warning':'Exact untruncated Gaussian bearing-line intersection has nonzero density at parallel rays: population second moment is infinite. Empirical RMSE is a local approximation check, not that population moment.'}

if __name__=='__main__':
    t=time.time(); q21=q21_solve(); q21['nonlinear_validation']=noise_validation(q21)
    (OUT/'q21.json').write_text(json.dumps(q21,indent=2))
    single=numerical_single_ready(); (OUT/'single_ready.json').write_text(json.dumps(single,indent=2))
    print('single ready',[[z['ready_s'] for z in row] for row in single],flush=True)
    best,records,cache=single_reuse_search(single)
    (OUT/'q22_search.json').write_text(json.dumps({'best':best,'assignments':records,'trials':cache,'elapsed_s':time.time()-t},indent=2))
    (OUT/'summary.json').write_text(json.dumps({'q21':q21,'q22':best},indent=2))
    print('final',best['latest_start_s'],'seconds search',time.time()-t,flush=True)
