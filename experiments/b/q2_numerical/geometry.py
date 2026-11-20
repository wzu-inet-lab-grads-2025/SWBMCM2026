"""Independent nominal geometry and certified-feasible polygonal routing.
No imports from other experiments. Paths may be conservative at rounded corners.
"""
import json, math, heapq
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
SCENE = json.loads((ROOT / 'experiments/b/scene.json').read_text())
OUT = ROOT / 'runs/b/q2_numerical'
OUT.mkdir(parents=True, exist_ok=True)
R = SCENE['vehicle_radius_m']
SPEED = SCENE['vehicle_speed_m_s']
OMEGA = math.radians(SCENE['axis_speed_deg_s'])
V = np.array(list(SCENE['vehicles_xy_m'].values()), float)
BOXES = np.array(list(SCENE['buildings_xyxy_height_m'].values()), float)
T = np.array([[50 + r * math.cos(math.radians(a)), 50 + r * math.sin(math.radians(a)), h]
              for a, r, h in SCENE['alerts_azimuth_deg_range_m_altitude_m'].values()])

def point_box_distance(p, b):
    return math.hypot(max(b[0]-p[0], 0., p[0]-b[2]), max(b[1]-p[1], 0., p[1]-b[3]))

def disk_ok(p, eps=1e-10):
    return min(p) >= R-eps and max(p) <= 100-R+eps and all(point_box_distance(p,b) >= R-eps for b in BOXES)

def segment_aabb(a, c, lo, hi):
    """Closed line segment vs closed box by parameter clipping; dimension agnostic."""
    enter, leave = 0., 1.
    for x, y, l, h in zip(a, c, lo, hi):
        d = y-x
        if abs(d) < 1e-15:
            if x < l or x > h:
                return False
        else:
            u, w = (l-x)/d, (h-x)/d
            enter, leave = max(enter,min(u,w)), min(leave,max(u,w))
            if enter > leave:
                return False
    return True

def visible(p, k):
    a = [p[0], p[1], 2.]
    return not any(segment_aabb(a, T[k], [b[0],b[1],0.], [b[2],b[3],b[4]]) for b in BOXES)

def angles(p, k):
    d = T[k,:2]-p
    r = float(np.linalg.norm(d))
    return math.atan2(d[1],d[0]), math.atan2(T[k,2]-2.,r)

def slew(p, k):
    a,b = angles(p,k)
    return max(abs(a),abs(b))/OMEGA

def observable(p,k):
    return disk_ok(p) and angles(p,k)[1] <= math.radians(80)+1e-13 and visible(p,k)

def segment_point_distance(a,b,p):
    a,b,p = np.asarray(a),np.asarray(b),np.asarray(p)
    d=b-a
    t=float(np.clip(np.dot(p-a,d)/max(np.dot(d,d),1e-300),0,1))
    return float(np.linalg.norm(a+t*d-p))

def segment_box_distance(a,c,b):
    if segment_aabb(a,c,b[:2],b[2:4]):
        return 0.
    corners = ([b[0],b[1]],[b[0],b[3]],[b[2],b[1]],[b[2],b[3]])
    return min(point_box_distance(a,b),point_box_distance(c,b),*(segment_point_distance(a,c,p) for p in corners))

def segment_ok(a,b,eps=1e-9):
    return disk_ok(a,eps) and disk_ok(b,eps) and all(segment_box_distance(a,b,c) >= R-eps for c in BOXES)

def path_length(path):
    return sum(math.dist(a,b) for a,b in zip(path,path[1:]))

def pair_ok(p,q,k):
    a,b=np.asarray(p)-T[k,:2],np.asarray(q)-T[k,:2]
    return math.dist(p,q) >= 3.-1e-9 and abs(a[0]*b[1]-a[1]*b[0]) > 1e-8

def pair_metrics(p,q,k):
    a,b = np.asarray(p)-T[k,:2],np.asarray(q)-T[k,:2]
    r1,r2 = np.linalg.norm(a),np.linalg.norm(b)
    co = np.clip(np.dot(a,b)/(r1*r2),-1,1)
    gamma = math.acos(co)
    rmse=math.radians(1)*math.sqrt(r1*r1+r2*r2)/abs(math.sin(gamma))
    return dict(ranges_m=[float(r1),float(r2)], baseline_m=math.dist(p,q),
                intersection_deg=math.degrees(gamma),first_order_horizontal_rmse_m=rmse)

class Router:
    """Visibility graph using safe square-offset corners (not claimed exact geodesic).
    Every segment is checked against exact radius-disk swept geometry.
    Direct feasible segments are exact shortest paths; general returned paths are upper bounds.
    """
    def __init__(self):
        self.nodes=[]
        for x0,y0,x1,y1,h in BOXES:
            for x in [x0-R-1e-7,x1+R+1e-7]:
                for y in [y0-R-1e-7,y1+R+1e-7]:
                    if disk_ok([x,y]): self.nodes.append([x,y])
        n=len(self.nodes)
        self.adj=[[] for _ in range(n)]
        for i in range(n):
            for j in range(i):
                if segment_ok(self.nodes[i],self.nodes[j]):
                    d=math.dist(self.nodes[i],self.nodes[j])
                    self.adj[i].append((j,d)); self.adj[j].append((i,d))
    def route(self,a,b):
        a,b=list(a),list(b)
        if segment_ok(a,b): return [a,b]
        if not disk_ok(a) or not disk_ok(b): return None
        pts=self.nodes+[a,b]; n=len(self.nodes)
        adj=[z.copy() for z in self.adj]+[[],[]]
        for s in [n,n+1]:
            for j in range(n):
                if segment_ok(pts[s],pts[j]):
                    d=math.dist(pts[s],pts[j]); adj[s].append((j,d)); adj[j].append((s,d))
        ds={n:0.}; prev={}; queue=[(0.,n)]
        while queue:
            d,i=heapq.heappop(queue)
            if d != ds[i]: continue
            if i == n+1:
                ix=[i]
                while ix[-1] != n: ix.append(prev[ix[-1]])
                return [pts[j] for j in reversed(ix)]
            for j,w in adj[i]:
                if d+w < ds.get(j,float('inf')):
                    ds[j]=d+w; prev[j]=i; heapq.heappush(queue,(d+w,j))
        return None
