"""Attach evidence to exchange JSON without reducing coordinate/time precision."""
import json,platform
from pathlib import Path
import numpy as np,scipy
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'runs/b/q2_numerical'
if __name__=='__main__':
    summary=json.loads((OUT/'summary.json').read_text());bound=json.loads((OUT/'q22_strong_lower_bound.json').read_text())
    baselines=json.loads((OUT/'baselines.json').read_text());finite=json.loads((OUT/'finite_search.json').read_text())
    replay=json.loads((OUT/'replay.json').read_text());sensitivity=json.loads((OUT/'sensitivity.json').read_text())
    lower=bound['lower_bound_s'];upper=summary['q22']['latest_start_s']
    summary['q22']['continuous_optimum_bounds']={'lower_s':lower,'feasible_upper_s':upper,'gap_s':upper-lower,
      'status':'numerical global bounds from conservative analytic cell formulas; NOT directed-rounding interval proof; optical boundary may mean infimum'}
    summary['q22']['stationary_optimum_s']=baselines['stationary']['best']['latest_start_s']
    summary['q22']['improvement_s']=summary['q22']['stationary_optimum_s']-upper
    summary['q22']['finite_common_candidate_optimum_s']=finite['best_latest_start_s']
    summary['q22']['deployment_order']=['T1','T2','T3']
    summary['evidence']={'report':'experiments/b/q2_numerical/REPORT.md','independent_replay_passed':replay['all_passed'],
        'replay_checks':len(replay['checks']),'baseline_plans':len(baselines['plans']),'baseline_csv':'runs/b/q2_numerical/baselines.csv',
        'events_csv':'runs/b/q2_numerical/events.csv','bounds':'runs/b/q2_numerical/q22_strong_lower_bound.json',
        'interior_1mm_latest_s':sensitivity['q22_interior_1mm']['latest_start_s'],
        'software':{'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__}}
    summary['units']={'position':'m','time':'s','vehicle_speed':'m/s','bearing':'degrees unless explicitly radians'}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False))
    print('summary:',OUT/'summary.json','lower',lower,'upper',upper,'gap',upper-lower,'checks',len(replay['checks']))
