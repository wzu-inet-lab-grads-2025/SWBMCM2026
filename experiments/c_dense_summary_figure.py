"""Plot three independent, aggregate video-audit readouts without reading private frames.

AI-assisted visualization in OpenCode / OpenAI GPT family, GPT-6.2 Astra routing
group, 2026-11-21. Exact release date unavailable.
"""

import argparse
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def draw(source, out):
    association = pd.read_csv(source/'detector_association.csv').query("scope=='quality'").set_index('quality')
    flow = pd.read_csv(source/'flow_agreement.csv').query("scope=='quality'").set_index('quality')
    actions = pd.read_csv(source/'review_summary.csv').query("group_by=='all'").set_index('dimension')
    labels = ['Explicit normal','Occlusion','Near edge']
    colors = ['#306c8c','#a96321','#784571']
    fig, (a,b,c) = plt.subplots(1,3,figsize=(12,3.9),layout='constrained')
    y = np.array([2.,1.,0.])
    det = association.loc[['explicit_normal','occlusion','near edge']]
    for offset,col,label,color in [(0.15,'all21_inside','All 21 projections in view','#aebdc7'),
                                  (-0.15,'matched','Independent detector matched','#306c8c')]:
        p = 100*det[col]/det.hand_view_samples
        a.barh(y+offset, p, height=.27, color=color,label=label)
        for j,x in enumerate(p):
            a.text(x+1.4,y[j]+offset,f'{x:.1f}',va='center',fontsize=8)
    a.set(yticks=y,yticklabels=labels,xlim=(0,116),xlabel='Percent of hand–head-view frames',title='Projection and image matching')
    a.legend(loc='upper left',bbox_to_anchor=(0,-.19),frameon=False,fontsize=8)
    subset=flow.loc[['normal','occlusion','near edge']]
    for i,(_,r) in enumerate(subset.iterrows()):
        value=r['epe_p50_px']; b.barh(y[i],value,height=.4,color=colors[i]); b.text(value+.035,y[i],f'{value:.2f}',va='center',fontsize=9)
    b.set(yticks=y,yticklabels=labels,xlim=(0,3),xlabel='Median displacement residual (px at 360×273)',title='Trackable local textures')
    review = actions.loc[['object','action','overall']]
    left=np.zeros(3)
    for key,label,color in [('visible','Supported','#306c8c'),('partial','Partial','#c6a457'),
                            ('insufficient','Insufficient','#bbbcc3'),('contradiction','Contradiction','#9d3d62')]:
        widths=review[key].to_numpy()
        c.barh(y,widths,left=left,height=.45,color=color,label=label)
        for i,n in enumerate(widths):
            if n>10: c.text(left[i]+n/2,y[i],str(n),ha='center',va='center',color='white' if key=='visible' else '#302b1e',fontsize=9)
        left+=widths
    c.set(yticks=y,yticklabels=['Object','Action','Both'],xlim=(0,349),xlabel='Number of annotated intervals (349 total)',title='Visual review of interval text')
    c.legend(loc='upper left',bbox_to_anchor=(0,-.19),frameon=False,fontsize=8,ncol=2)
    for ax in (a,b,c):
        ax.spines[['top','right','left']].set_visible(False)
        ax.tick_params(axis='y',length=0)
        ax.set_axisbelow(True); ax.grid(axis='x',alpha=.18)
    out.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(out,dpi=170); plt.close(fig)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=Path('research/c_dense_audit'))
    p.add_argument('--out',type=Path,default=Path('runs/c_dense_joint_check/diagnostics.png'))
    args=p.parse_args(); draw(args.source,args.out)
