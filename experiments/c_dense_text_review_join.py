"""Compare within-record frozen CLIP retrieval and the separate interval image review.
AI-assisted aggregation, OpenCode, OpenAI GPT family, GPT-6.2 Astra routing group,
2026-11-21. Exact release date unavailable.
"""

import argparse
import json
from pathlib import Path

import pandas as pd


def compare(run, out):
    keys = {}
    for f in (run/'c_full_video/full').glob('file_*.json'):
        stream = json.loads(f.read_text())
        sample = Path(stream['path']).parts[2]
        key = (stream['dataset'],sample)
        if keys.setdefault(stream['record_id'], key) != key:
            raise ValueError('Inconsistent source location')
    scores = pd.read_csv(run/'c_dense_vlm/interval_scores.csv').query("view=='equal_views'").copy()
    scores['sample'] = [keys[r][1] for r in scores.record]
    if not all(keys[r.record][0] == r.source for r in scores.itertuples()):
        raise ValueError('Record id does not match source')
    notes = pd.read_csv(run/'c_dense_segment_review/reviewed.csv')
    linked = scores.merge(notes, left_on=['source','sample','ordinal','split'],
                          right_on=['dataset','sample','source_segment_index','split'],validate='one_to_one')
    if len(linked)!=len(notes) or len(linked)!=len(scores) or not (linked.frames==linked.available).all():
        raise ValueError('Interval populations differ')
    result=[]
    for grade,group in linked.groupby('overall'):
        alt=group[group.k>1]
        result.append(dict(visual_grade=grade, all_intervals=len(group), has_alternative=len(alt),
                           first_place_credit=alt.top.sum(), beats_next_alternative=alt.wins.sum(),
                           original_pair_beats_swap=alt.swap_wins.sum()))
    table=pd.DataFrame(result)
    out.parent.mkdir(parents=True,exist_ok=True)
    table.to_csv(out,index=False,float_format='%.9g')
    print(table.to_string(index=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runs',type=Path,default=Path('runs'))
    p.add_argument('--out',type=Path,default=Path('runs/c_dense_joint_check/text_review.csv'))
    args=p.parse_args(); compare(args.runs,args.out)
