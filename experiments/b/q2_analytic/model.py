"""Geometry and timeline for the nominal stationary calibration problem."""
import json
import math
from pathlib import Path

from scipy.spatial import ConvexHull


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'runs/b/q2_analytic'
SCENE = json.loads((ROOT / 'experiments/b/scene.json').read_text())
SPEED = SCENE['vehicle_speed_m_s']
R = SCENE['vehicle_radius_m']
H0 = SCENE['camera_height_m']
W = math.radians(SCENE['axis_speed_deg_s'])
DURATION = SCENE['joint_observation_s']
SIDE = SCENE['field_side_m']
DOMAIN = (R, SIDE-R, R, SIDE-R)
VEH = {k: tuple(v) for k,v in SCENE['vehicles_xy_m'].items()}
BLD = SCENE['buildings_xyxy_height_m']


def target(theta, distance, height):
    a,b = SCENE['centre_xy_m']
    return (a+distance*math.cos(math.radians(theta)), b+distance*math.sin(math.radians(theta)), height)


TGT = {k: target(*v) for k,v in SCENE['alerts_azimuth_deg_range_m_altitude_m'].items()}


def corners(box):
    x,X,y,Y = box
    return ((x,y),(x,Y),(X,y),(X,Y))


def prism_hit(p, t, b):
    """Closed finite segment vs closed prism by 3-D slab clipping."""
    x,y,X,Y,z = b
    t0,t1 = 0.,1.
    for a,e,lo,hi in zip((*p,H0), t, (x,y,0), (X,Y,z)):
        d = e-a
        if not d:
            if a < lo or a > hi:
                return False
            continue
        u,v = sorted(((lo-a)/d,(hi-a)/d))
        t0,t1 = max(u,t0),min(v,t1)
        if t0 > t1:
            return False
    return True


def point_box(p,b):
    x,X,y,Y = b
    return math.hypot(max(x-p[0],0,p[0]-X), max(y-p[1],0,p[1]-Y))


def xybox(building):
    x,y,X,Y,_ = building
    return x,X,y,Y


def point_segment(p,a,b):
    dx,dy = b[0]-a[0], b[1]-a[1]
    if a == b:
        return math.dist(p,a)
    s = max(0.,min(1., ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy)))
    return math.hypot(a[0]+s*dx-p[0], a[1]+s*dy-p[1])


def segment_clearance(a,b,k):
    box = xybox(k)
    # In-plane intersection is exactly a 3-D slab test at camera height.
    if prism_hit(a,(*b,H0),k):
        return 0.
    return min(point_box(a,box), point_box(b,box), *(point_segment(p,a,b) for p in corners(box)))


def path_ok(a,b):
    return all(R <= c <= SIDE-R for p in (a,b) for c in p) and all(
        segment_clearance(a,b,k) >= R for k in BLD.values())


def angles(p,t):
    a,b,z = TGT[t]
    dx,dy = a-p[0], b-p[1]
    return math.atan2(dy,dx), math.atan2(z-H0,math.hypot(dx,dy))


def slew(p,t):
    bearing,tilt = angles(p,t)
    return max(abs(bearing), abs(tilt))/W


def visible(p,t):
    el = math.degrees(angles(p,t)[1])
    return (SCENE['elevation_deg'][0] <= el <= SCENE['elevation_deg'][1]+2e-13
            and not any(prism_hit(p,TGT[t],b) for b in BLD.values()))


def geometry(p,q,t):
    a,b = angles(p,t)[0], angles(q,t)[0]
    g = abs(math.remainder(a-b,2*math.pi))
    r,s = (math.dist(v,TGT[t][:2]) for v in [p,q])
    sigma = math.radians(SCENE['q2_1']['bearing_sd_deg'])
    return dict(baseline_m=math.dist(p,q),gamma_deg=math.degrees(g),ranges_m=[r,s],
                horizontal_rmse_m=sigma*math.hypot(r,s)/abs(math.sin(g)))


def make_leg(vehicle,origin,p,depart,t):
    assert path_ok(origin,p) and visible(p,t), (vehicle,origin,p,t)
    a,b = angles(p,t)
    length = math.dist(origin,p)
    travel = length/SPEED
    return dict(vehicle=vehicle,station=p,path=[origin,p],distance_m=length,travel_s=travel,
                azimuth_deg=math.degrees(a),elevation_deg=math.degrees(b),slew_s=slew(p,t),
                depart_s=depart,arrival_s=depart+travel,ready_s=depart+travel+slew(p,t))


def job(t,legs):
    start = max(l['ready_s'] for l in legs)
    geo = geometry(*(l['station'] for l in legs),t)
    assert min(geo['gamma_deg'],180-geo['gamma_deg']) > 1e-7 and geo['baseline_m'] >= 2*R
    end = start + DURATION
    for l in legs:
        l.update(wait_s=start-l['ready_s'],reset_start_s=end,reset_end_s=end+l['slew_s'])
    return dict(target=t,start_s=start,end_s=end,legs=legs,**geo)


def shadows(t):
    """Convex visibility exclusion polygons, outward unit halfspace normals."""
    tx,ty,h = TGT[t]
    result=[]
    for b in BLD.values():
        kappa = (h-H0)/(h-b[-1])
        points = [(tx+k*(x-tx), ty+k*(y-ty)) for k in [1.,kappa] for x,y in corners(xybox(b))]
        result.append(ConvexHull(points).equations.tolist())
    return result


SHADOW = {t:shadows(t) for t in TGT}
