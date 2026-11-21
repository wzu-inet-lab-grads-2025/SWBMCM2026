"""Count repeat sampling within the public Q3 validation design from index metadata.

AI-assisted in OpenCode, OpenAI GPT family, GPT-6.2 Astra routing group,
2026-11-21. Model release date unavailable. No policy trajectories or scores.
"""

import csv
import json
import random
from collections import defaultdict
from pathlib import Path


def read_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def describe(rules_dir):
    pool = defaultdict(list)
    for row in read_rows(rules_dir / 'source_segment_index.csv'):
        if row['eligible'] == '1' and row['split'] == 'validation':
            pool[row['family_id']].append(row)
    tasks = read_rows(rules_dir / 'tasks.csv')
    rules = json.loads((rules_dir / 'common_conditions.json').read_text(encoding='utf-8-sig'))
    for family, rows in sorted(pool.items()):
        print(f"{family}: {len(rows)} segments, {len({r['episode_uuid'] for r in rows})} records,"
              f" {len({r['environment_id'] for r in rows})} environments")
    for case in rules['cases']:
        case_id = case['case_id']
        jobs = sorted((job for job in tasks if job['case_id'] == case_id), key=lambda job: job['job_id'])
        covered, draws, repeated = set(), 0, 0
        for seed in rules['validation_seeds']:
            rng = random.Random(seed)
            batch = [rng.choice(pool[j['family_id']])['unit_id'] for j in jobs]
            covered.update(batch)
            draws += len(batch)
            repeated += int(len(set(batch)) < len(batch))
        print(f'{case_id}: {draws} draws, {len(covered)} unique segments,'
              f' {repeated}/{len(rules["validation_seeds"])} trials with repeats')


if __name__ == '__main__':
    describe(Path(__file__).resolve().parents[1] / 'data/raw/C/Restocking_Tasks_and_Constraints')
