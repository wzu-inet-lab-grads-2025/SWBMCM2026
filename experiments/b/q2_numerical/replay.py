"""Independent event replay: deliberately does NOT import geometry/search modules.
Optical check clips only the horizontal footprint, then checks minimum segment
height; swept-disk check analytically minimizes a piecewise quadratic distance.
"""
import itertools,json,math,hashlib,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'runs/b/q2_numerical'
DATA=json.loads((ROOT/'experiments/b/scene.json').read_text())
BUILDINGS=list(DATA['buildings_xyxy_height_m'].values())
START=DATA['vehicles_xy_m'];R=DATA['vehicle_radius_m']
TARGET={key:[50+r*math.cos(math.radians(a)),50+r*math.sin(math.radians(a)),h]
        for key,(a,r,h) in DATA['alerts_azimuth_deg_range_m_altitude_m'].items()}
SPEED=DATA['vehicle_speed_m_s'];ANGULAR_SPEED=DATA['axis_speed_deg_s']
CHECKS=[]

def demand(value,label):
    CHECKS.append({'label':label,'pass':bool(value)})
    if not value: raise AssertionError(label)

def near(a,b,label,tol=2e-7): demand(abs(a-b)<tol,label+f' ({a:.15g} vs {b:.15g})')

def squared_distance(p,b):
    return max(b[0]-p[0],0,p[0]-b[2])**2+max(b[1]-p[1],0,p[1]-b[3])**2

def route_min_clearance(path):
    """Break at all coordinate crossings of raw rectangle sides, minimize exact quadratic."""
    minclear=float('inf')
    for p,q in zip(path,path[1:]):
        dx,dy=q[0]-p[0],q[1]-p[1]
        for b in BUILDINGS:
            times=[0.,1.]
            for axis,delta in enumerate([dx,dy]):
                if delta!=0:
                    times.extend((bound-p[axis])/delta for bound in [b[axis],b[axis+2]] if 0<(bound-p[axis])/delta<1)
            times=sorted(times); candidates=times.copy()
            for low,high in zip(times,times[1:]):
                middle=(low+high)/2; coefficients=[]
                for axis,delta in enumerate([dx,dy]):
                    val=p[axis]+middle*delta
                    if val<b[axis]: coefficients.append((p[axis]-b[axis],delta))
                    elif val>b[axis+2]: coefficients.append((p[axis]-b[axis+2],delta))
                den=sum(slope*slope for _,slope in coefficients)
                if den>0:
                    t=-sum(c*slope for c,slope in coefficients)/den
                    if low<t<high:candidates.append(t)
            d2=min(squared_distance([p[0]+t*dx,p[1]+t*dy],b) for t in candidates)
            minclear=min(minclear,math.sqrt(d2)-R)
    return minclear

def optics(p,target):
    t=TARGET[target]; radius=math.dist(p,t[:2]); elev=math.degrees(math.asin((t[2]-2)/math.sqrt(radius*radius+(t[2]-2)**2)))
    pan=math.degrees(math.atan2(t[1]-p[1],t[0]-p[0]));occluded=[]
    for j,b in enumerate(BUILDINGS):
        lo,hi=0.,1.; intersect=True
        for dim in range(2):
            d=t[dim]-p[dim]
            if d==0:
                if not b[dim]<=p[dim]<=b[dim+2]:intersect=False;break
            else:
                enter=(b[dim]-p[dim])/d;leave=(b[dim+2]-p[dim])/d
                lo=max(lo,min(enter,leave));hi=min(hi,max(enter,leave))
        if intersect and lo<=hi:
            minheight=min(2+lo*(t[2]-2),2+hi*(t[2]-2))
            if minheight<=b[4]:occluded.append(f'B{j+1}')
    return {'elevation_deg':elev,'pan_deg':pan,'slew_s':max(abs(pan),abs(elev))/ANGULAR_SPEED,'occluded':occluded}

def check_leg_geometry(leg,target):
    p=leg['station'];path=leg['path'];label=target+':'+leg['vehicle']
    demand(math.dist(path[-1],p)<1e-9,label+' path ends at station')
    demand(all(R-1e-9<=coordinate<=100-R+1e-9 for point in path for coordinate in point),label+' field bounds')
    clearance=route_min_clearance(path);demand(clearance>=-2e-8,label+' swept-disk route')
    op=optics(p,target)
    demand(-10-1e-10<=op['elevation_deg']<=80+1e-10,label+' elevation')
    demand(not op['occluded'],label+' optical segment noncontact')
    return {'vehicle':leg['vehicle'],'target':target,'distance_m':sum(math.dist(a,b) for a,b in zip(path,path[1:])),
            'min_building_clearance_m':clearance,**op}

def check_pair(job):
    p,q=[l['station'] for l in job['legs']];t=TARGET[job['target']]
    demand(len(set(l['vehicle'] for l in job['legs']))==2,job['target']+' distinct cars')
    demand(math.dist(p,q)>=3-1e-8,job['target']+' separated disks')
    cross=(p[0]-t[0])*(q[1]-t[1])-(p[1]-t[1])*(q[0]-t[0])
    demand(abs(cross)>1e-8,job['target']+' noncollinear bearing lines')

def check_schedule(q22):
    state={vehicle:(0.,p) for vehicle,p in START.items()};events=[];geom=[]
    jobs=q22['jobs'];demand([j['start_s'] for j in jobs]==sorted(j['start_s'] for j in jobs),'jobs sorted by actual common start')
    for job in jobs:
        target=job['target'];check_pair(job);readies=[]
        for leg in job['legs']:
            vehicle=leg['vehicle'];release,origin=state[vehicle];label=target+':'+vehicle
            demand(leg['depart_s']>=release-2e-7,label+' no departure before prior reset')
            near(leg['depart_s'],release,label+' earliest dispatch after reset')
            demand(math.dist(origin,leg['path'][0])<1e-8,label+' chained path origin')
            g=check_leg_geometry(leg,target);geom.append(g)
            arrival=leg['depart_s']+g['distance_m']/SPEED;ready=arrival+g['slew_s'];readies.append(ready)
            near(arrival,leg['arrival_s'],label+' arrival');near(ready,leg['ready_s'],label+' ready')
            demand(job['start_s']>=ready-2e-7,label+' synchronization')
            end=job['start_s']+5;reset=end+g['slew_s'];near(reset,leg['reset_end_s'],label+' reset')
            state[vehicle]=(reset,leg['station'])
            events.extend([{'vehicle':vehicle,'target':target,'phase':name,'start_s':a,'end_s':b}
                           for name,a,b in [('travel',leg['depart_s'],arrival),('slew',arrival,ready),
                                            ('wait',ready,job['start_s']),('observe',job['start_s'],end),('reset',end,reset)]])
        near(max(readies),job['start_s'],target+' common start')
    near(max(j['start_s'] for j in jobs),q22['latest_start_s'],'objective replay')
    return {'geometry':geom,'events':sorted(events,key=lambda e:(e['vehicle'],e['start_s'])),'idle_vehicles':
            [v for v in START if not any(l['vehicle']==v for j in jobs for l in j['legs'])]}

def boundary_tests():
    tests=[]
    # Closed line touching top of B3 at x=24,y=76,z=12 for T2.
    target=TARGET['T2'];roof=[24.,76.];scale=(target[2]-2)/(target[2]-12)
    p=[target[j]+scale*(roof[j]-target[j]) for j in [0,1]]
    # Exact algebra suffers roundoff at contact; test 1e-7 either side along roof height line.
    for delta in [-1e-6,1e-6]:
        pp=[target[j]+(scale+delta)*(roof[j]-target[j]) for j in [0,1]]
        tests.append({'name':'roof projection side','delta_scale':delta,'station':pp,'optics':optics(pp,'T2')})
    tests.extend([{'name':'disk tangent B3 north','clearance':route_min_clearance([[14,77.5],[20,77.5]])},
                  {'name':'disk 1e-6 inside B3 north','clearance':route_min_clearance([[14,77.5-1e-6],[20,77.5-1e-6]])},
                  {'name':'square-corner approximation would overexclude','clearance':route_min_clearance([[8.9,77.1],[8.9,77.1]])}])
    demand(abs(tests[2]['clearance'])<1e-12,'tangent disk allowed')
    demand(tests[3]['clearance']<0,'disk intruding rejected')
    demand(tests[4]['clearance']>0,'rounded corner truly feasible')
    return tests

def main():
    summary=json.loads((OUT/'summary.json').read_text());q21=summary['q21'];g21=[]
    for leg in q21['legs']:
        demand(math.dist(START[leg['vehicle']],leg['path'][0])<1e-9,'q21 initial origin')
        g=check_leg_geometry(leg,'T2');g21.append(g)
        near(g['distance_m']/SPEED,leg['travel_s'],'q21 travel')
        demand(leg['travel_s']<=30+1e-8,'q21 <=30 s')
    check_pair({'target':'T2','legs':q21['legs']})
    result={'q21_geometry':g21,'q22_replay':check_schedule(summary['q22']),'boundary_tests':boundary_tests()}
    baselinepath=OUT/'baselines.json'
    if baselinepath.exists():
        baselines=json.loads(baselinepath.read_text());count=0
        for p in baselines['plans']:
            check_schedule(p);count+=1
        check_schedule(baselines['stationary']['best']);result['baseline_plans_checked']=count
    sensitivitypath=OUT/'sensitivity.json'
    if sensitivitypath.exists():
        sensitivity=json.loads(sensitivitypath.read_text())
        result['q22_interior_replay']=check_schedule(sensitivity['q22_interior_1mm'])
        for leg in sensitivity['q21_interior_1mm']['legs']:
            check_leg_geometry(leg,'T2')
    result['checks']=CHECKS;result['all_passed']=all(c['pass'] for c in CHECKS)
    result['source_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in [ROOT/'experiments/b/scene.json',*sorted((ROOT/'experiments/b/q2_numerical').glob('*.py'))]}
    (OUT/'replay.json').write_text(json.dumps(result,indent=2))
    import csv
    with (OUT/'events.csv').open('w') as f:
        writer=csv.DictWriter(f,['vehicle','target','phase','start_s','end_s']);writer.writeheader();writer.writerows(result['q22_replay']['events'])
    print('PASS',len(CHECKS),'checks; baselines',result.get('baseline_plans_checked',0))

if __name__=='__main__':main()
