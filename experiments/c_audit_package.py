"""Compare extracted C data against local ZIP directories and public case settings.

Reads existing files only. Run from any directory with Python 3.9+.
"""

import csv
import json
from collections import Counter
from graphlib import TopologicalSorter
from pathlib import Path
from zipfile import ZipFile
from zlib import crc32


ROOT = Path(__file__).resolve().parents[1] / 'data/raw/C'


def verify_packages():
    raw = ROOT / 'Raw_Data'
    sizes = []
    for name in ('EgoDemo', 'EgoPro', 'EgoStandard'):
        archive = raw / (name + '.zip')
        extracted = {p.relative_to(raw).as_posix() for p in (raw / name).rglob('*') if p.is_file()}
        with ZipFile(archive) as z:
            members = [m for m in z.infolist() if not m.is_dir()]
            names = {m.filename for m in members}
            assert len(names) == len(members), f'duplicate archive member in {archive}'
            assert names == extracted, (sorted(names - extracted), sorted(extracted - names))
            damaged = z.testzip()
            assert damaged is None, f'unreadable or damaged member: {damaged}'
            for m in members:
                path = raw / m.filename
                checksum = 0
                with path.open('rb') as stream:
                    while block := stream.read(1024 * 1024):
                        checksum = crc32(block, checksum)
                assert (path.stat().st_size, checksum) == (m.file_size, m.CRC), path
        sizes.append(archive.stat().st_size)
        print(name, 'verified archive members and matched paths / sizes / CRC32', len(members),
              'ZIP bytes', sizes[-1])
    print('ZIP bytes total', sum(sizes))


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def verify_cases():
    rules = ROOT / 'Restocking_Tasks_and_Constraints'
    common = json.loads((rules / 'common_conditions.json').read_text())
    tasks = read_csv(rules / 'tasks.csv')
    families = {r['family_id'] for r in read_csv(rules / 'job_families.csv')}
    zones = {r['zone_id'] for r in read_csv(rules / 'service_zones.csv')}
    assert all(r['family_id'] in families and r['zone_id'] in zones for r in tasks)
    assert Counter(r['case_id'] for r in tasks) == {c['case_id']: c['jobs'] for c in common['cases']}
    assert len({r['job_id'] for r in tasks}) == len(tasks)
    for case in common['cases']:
        jobs = [r for r in tasks if r['case_id'] == case['case_id']]
        ids = {r['job_id'] for r in jobs}
        graph = {r['job_id']: r['predecessors'].split('|') if r['predecessors'] else [] for r in jobs}
        assert all(p in ids for parents in graph.values() for p in parents)
        assert set(TopologicalSorter(graph).static_order()) == ids
        print(case['case_id'], 'jobs, horizon_s, budget, reviews', len(jobs), case['horizon_s'],
              case['cost_budget'], case['observation_limit'], 'valid links, acyclic')


def inspect_schemas():
    raw = ROOT / 'Raw_Data'
    for name in ('EgoPro', 'EgoStandard'):
        for field in ('pose_left_hand', 'pose_right_hand', 'annotation_bad_frame_pose_hand'):
            counts = Counter()
            for path in (raw / name).glob(f'*/tables/{field}.csv'):
                with path.open(encoding='utf-8-sig', newline='') as stream:
                    counts[tuple(next(csv.reader(stream)))] += 1
            print(name, field, 'schema widths and record counts',
                  sorted((len(header), n) for header, n in counts.items()))
    rotations = Counter()
    cameras = Counter()
    for path in (raw / 'EgoDemo').glob('*/meta/info.json'):
        features = json.loads(path.read_text())['features']
        rotations[tuple(features['observation.state.hand_left_world_rotation']['names'][-1])] += 1
        cameras[sum(key.startswith('observation.images.') for key in features)] += 1
    print('EgoDemo annotated quaternion order', dict(rotations), 'camera counts', dict(cameras))


if __name__ == '__main__':
    verify_packages()
    verify_cases()
    inspect_schemas()
