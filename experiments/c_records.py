"""Read official C split membership and locate records for exploration.

AI-assisted preprocessing, OpenCode, OpenAI GPT family, GPT-6.2 Astra routing
group, 2026-11-20. Exact model release date unavailable.
"""

import argparse
from collections import Counter
from pathlib import Path

if __package__:
    from .c_audit_quality import ROOT, raw_metadata, rows
else:
    from c_audit_quality import ROOT, raw_metadata, rows


def official_records(root):
    records = rows(root / 'Restocking_Tasks_and_Constraints/record_split.csv')
    memberships, episodes = {}, set()
    for record in records:
        episode, env, split = (record[k] for k in ('episode_uuid', 'environment_id', 'split'))
        if not episode or episode in episodes:
            raise ValueError('Missing or duplicated episode in official record index')
        if not env or split not in ('train', 'validation', 'test') or memberships.setdefault(env, split) != split:
            raise ValueError('Invalid official environment membership')
        episodes.add(episode)
    return records


def split_records(root, split):
    if split not in ('train', 'validation'):
        raise ValueError('The diagnostic supports train and validation')
    records = [r for r in official_records(root) if r['split'] == split]
    folders = {}
    for r in rows(root / 'Restocking_Tasks_and_Constraints/source_segment_index.csv'):
        if r['split'] == split:
            folder = root / 'Raw_Data' / r['dataset'] / r['sample']
            if folders.setdefault(r['episode_uuid'], folder) != folder:
                raise ValueError('Conflicting folders for one episode')
    prefix = 'V' if split == 'validation' else 'T'
    for ordinal, record in enumerate(records, 1):
        folder = folders[record['episode_uuid']]
        meta = raw_metadata(folder)
        if folder.parent.name != record['dataset'] or any(meta[k] != record[k] for k in ('episode_uuid', 'environment_id')):
            raise ValueError('Raw metadata and split index disagree')
        yield f'{prefix}{ordinal:02}', folder, meta


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    records = official_records(parser.parse_args().root)
    print('split,records,environments,single_record_environments')
    for split in ('train', 'validation', 'test'):
        counts = Counter(r['environment_id'] for r in records if r['split'] == split)
        print(f'{split},{sum(counts.values())},{len(counts)},{sum(n == 1 for n in counts.values())}')
