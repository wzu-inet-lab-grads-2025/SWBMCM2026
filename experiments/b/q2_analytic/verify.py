"""Independent standard-library replay of the two exported solutions.

Geometry here minimizes point-to-rectangle squared distance along a segment.
The LOS test uses the low-height part of the ray, independent of the 3-D slabs.
"""
from itertools import combinations
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT/'runs/b/q2_analytic'
data = json.loads((ROOT/'experiments/b/scene.json').read_text())
initial = data['vehicles_xy_m']
rho = data['vehicle_radius_m']
buildings = data['buildings_xyxy_height_m']
speed = data['vehicle_speed_m_s']
cam = data['camera_height_m']
targets = {}
for name,(ang,d,h) in data['alerts_azimuth_deg_range_m_altitude_m'].items():
    rad = math.radians(ang)
    targets[name] = [data['centre_xy_m'][0]+d*math.cos(rad),data['centre_xy_m'][1]+d*math.sin(rad),h]


def lerp(p,q,t):
    return [x+(y-x)*t for x,y in zip(p,q)]


def rectangle_distance(p,q,b):
    """On each active-set interval the objective is a univariate quadratic."""
    times = {0.,1.}
    for i in (0,1):
        if q[i]!=p[i]:
            for wall in (b[i],b[i+2]):
                t = (wall-p[i])/(q[i]-p[i])
                if 0<t<1:
                    times.add(t)
    knots = sorted(times)
    for l,r in zip(knots,knots[1:]):
        m = lerp(p,q,(l+r)/2)
        num,den = 0.,0.
        for i in (0,1):
            if not b[i] <= m[i] <= b[i+2]:
                wall = b[i] if m[i]<b[i] else b[i+2]
                delta=q[i]-p[i]
                num += (wall-p[i])*delta
                den += delta*delta
        if den and l<num/den<r:
            times.add(num/den)
    return min(math.hypot(max(b[0]-x,0.,x-b[2]),max(b[1]-y,0.,y-b[3]))
               for x,y in (lerp(p,q,t) for t in times))


def close(x,y):
    assert abs(x-y)<2e-10, (x,y)


def check_leg(l,t):
    p, path = l['station'],l['path']
    assert p==path[-1] and len(path)>=2
    length = sum(math.dist(a,b) for a,b in zip(path,path[1:]))
    assert all(rho<=c<=data['field_side_m']-rho for xy in path for c in xy)
    body = min(rectangle_distance(a,b,rect)-rho for a,b in zip(path,path[1:]) for rect in buildings.values())
    assert body>=0
    tx,ty,h = targets[t]
    r = math.hypot(tx-p[0],ty-p[1])
    yaw = math.degrees(math.atan2(ty-p[1],tx-p[0]))
    tilt = math.degrees(math.atan2(h-cam,r))
    assert data['elevation_deg'][0]<=tilt<=data['elevation_deg'][1]
    clear = min(rectangle_distance(p,lerp(p,[tx,ty],(rect[-1]-cam)/(h-cam)),rect)
                for rect in buildings.values())
    assert clear>0
    n = max(abs(yaw),abs(tilt))/data['axis_speed_deg_s']
    for x,y in [(length,l['distance_m']),(length/speed,l['travel_s']),
                (l['depart_s']+length/speed,l['arrival_s']),(n,l['slew_s']),
                (l['arrival_s']+n,l['ready_s']),(yaw,l['azimuth_deg']),(tilt,l['elevation_deg'])]:
        close(x,y)
    return dict(vehicle=l['vehicle'],body_building_margin_m=body,low_ray_xy_clearance_m=clear)


def check_geo(j):
    (p,q) = [l['station'] for l in j['legs']]
    tx,ty,_ = targets[j['target']]
    x,y = tx-p[0],ty-p[1]
    u,v = tx-q[0],ty-q[1]
    r,s = math.hypot(x,y),math.hypot(u,v)
    cross = abs(x*v-y*u)
    angle = math.atan2(cross,x*u+y*v)
    assert 0<angle<math.pi
    close(math.degrees(angle),j['gamma_deg'])
    close(math.dist(p,q),j['baseline_m'])
    close(math.radians(data['q2_1']['bearing_sd_deg'])*math.hypot(r,s)*r*s/cross,j['horizontal_rmse_m'])


def min_separation(moves):
    """All five disks, including idle/finished ones, along the selected paths."""
    def position(v,t):
        p = initial[v]
        for a,b,start,end in moves[v]:
            if t<a:
                return p
            if a<=t<b:
                return lerp(start,end,(t-a)/(b-a))
            p=end
        return p
    knots = sorted({0.,*(t for legs in moves.values() for a,b,_,_ in legs for t in (a,b))})
    best = min(math.dist(initial[i],initial[j]) for i,j in combinations(initial,2))
    for l,r in zip(knots,knots[1:]):
        for i,j in combinations(initial,2):
            a = [x-y for x,y in zip(position(i,l),position(j,l))]
            b = [x-y for x,y in zip(position(i,r),position(j,r))]
            v = [y-x for x,y in zip(a,b)]
            den = sum(w*w for w in v)
            t = max(0.,min(1.,-sum(x*w for x,w in zip(a,v))/den)) if den else 0.
            best = min(best,math.hypot(*lerp(a,b,t)))
    assert best >= 2*rho
    return best


def replay(problem):
    q21,q22 = problem['q21'],problem['q22']
    summary={}
    for section,jobs in [('q21',[q21]),('q22',q22['jobs'])]:
        state={v:(0.,p) for v,p in initial.items()}
        moves={v:[] for v in initial}
        checks=[]
        for j in jobs:
            check_geo(j)
            assert len({l['vehicle'] for l in j['legs']})==2
            if section=='q22':
                close(j['start_s'],max(l['ready_s'] for l in j['legs']))
                close(j['end_s']-j['start_s'],data['joint_observation_s'])
            for l in j['legs']:
                v = l['vehicle']
                release,origin = state[v]
                assert l['path'][0]==origin
                close(l['depart_s'],release)
                assert len(l['path'])==2  # selected routes are all straight
                moves[v].append((l['depart_s'],l['arrival_s'],origin,l['station']))
                checks.append(dict(target=j['target'],**check_leg(l,j['target'])))
                if section=='q22':
                    close(l['wait_s'],j['start_s']-l['ready_s'])
                    close(l['reset_start_s'],j['end_s'])
                    close(l['reset_end_s'],j['end_s']+l['slew_s'])
                    state[v]=(l['reset_end_s'],l['station'])
                else:
                    assert l['travel_s']<=data['q2_1']['travel_limit_s']
        summary[section]=dict(checks=checks,minimum_vehicle_center_separation_m=min_separation(moves))
    starts = [j['start_s'] for j in q22['jobs']]
    assert len(starts)==3 and starts==sorted(starts)
    close(starts[-1],q22['latest_start_s'])
    return summary


if __name__=='__main__':
    data_main=json.loads((OUT/'summary.json').read_text())
    extra=json.loads((OUT/'larger_margin.json').read_text())
    results=replay(data_main)
    results['q22_0.1m']=replay(dict(q21=data_main['q21'],q22=extra))['q22']
    for row in json.loads((OUT/'baseline_timelines.json').read_text()):
        replay(dict(q21=data_main['q21'],q22=row))
    (OUT/'replay.json').write_text(json.dumps(results,indent=2)+'\n')
    print('Replayed both timing plans, precision, and moving-disk separations.')
