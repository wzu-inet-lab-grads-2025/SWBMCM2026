"""Operationally interior perturbations, selected-station shadow margins and noise conditioning."""
import json,math
import numpy as np
from geometry import *
from search import SHADOW,build_plan

if __name__=='__main__':
    summary=json.loads((OUT/'summary.json').read_text());q22=summary['q22'];assignment={};stations={};margins=[]
    for job in q22['jobs']:
        k=int(job['target'][1:])-1;assignment[k]=[]
        for leg in job['legs']:
            i=int(leg['vehicle'][1:])-1;p=np.array(leg['station']);assignment[k].append(i);stations[k,i]=p.copy()
            margins.append({'target':job['target'],'vehicle':leg['vehicle'],'shadow_signed_outside_m':
                            [float(max(eq[:,:2]@p+eq[:,2])) for eq in SHADOW[k]],
                            'field_margin_m':float(min(*p,*(100-p))-R)})
    perturbed={key:p.copy() for key,p in stations.items()}
    for (k,i),p in perturbed.items():
        # Explicit move away from active occlusion edge and from field boundary.
        if (k,i) in [(1,1),(1,3),(2,2)]:
            for _ in range(3):
                for eq in SHADOW[k]:
                    vals=eq[:,:2]@p+eq[:,2];j=int(np.argmax(vals))
                    if vals[j]<.001:p+=(.001-vals[j])*eq[j,:2]
                p[:]=np.clip(p,R+.001,100-R-.001)
    interior=build_plan(assignment,perturbed,[0,1,2])
    q21=summary['q21'];radius=np.linalg.norm(np.array(q21['legs'][0]['station'])-T[1,:2]);safe_legs=[]
    for leg in q21['legs']:
        p=np.array(leg['station']);p=T[1,:2]+(p-T[1,:2])*(radius+.001)/radius
        origin=V[int(leg['vehicle'][1:])-1];path=[origin.tolist(),p.tolist()]
        assert observable(p,1) and segment_ok(*path)
        safe_legs.append({'vehicle':leg['vehicle'],'station':p.tolist(),'path':path,'travel_s':path_length(path)/3})
    r=radius;conditioning=[]
    for degree in [90,45,10,1,.1,179,179.9]:
        condition=(1+abs(math.cos(math.radians(degree))))/(1-abs(math.cos(math.radians(degree))))
        conditioning.append({'angle_deg':degree,'equal_range_m':r,
                             'linear_rmse_m':math.radians(1)*math.sqrt(2)*r/abs(math.sin(math.radians(degree))),
                             'covariance_condition_number':condition})
    result={'q22_margins':margins,'q22_interior_1mm':interior,'q21_interior_1mm':{'legs':safe_legs,
              **pair_metrics(*[l['station'] for l in safe_legs],1)},'angle_conditioning':conditioning}
    (OUT/'sensitivity.json').write_text(json.dumps(result,indent=2));print('interior 1 mm q22',interior['latest_start_s'])
