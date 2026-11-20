"""Command line for formal B-problem runs. Uses only the standard library."""

import argparse
from collections import Counter
import hashlib
from itertools import permutations
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))

from src.q2.baselines import RULES, allocation_summary, generate_plan
from src.q2.scoring import score
from src.q4.replay import ScenarioReplay, validate_manifest
from src.q4.reschedule import BeamPlanner
from src.shared.plan import Plan
from src.shared.scene import Scene
from src.shared.timeline import replay, state_at


def read_input(path, expected_digest=None):
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_digest is not None and expected_digest != digest:
        raise ValueError(f'{path}: SHA-256 differs from the configured input')
    return json.loads(raw), digest


def version():
    # A content hash identifies even uncommitted implementations and exported archives.
    files = sorted((ROOT / 'src').rglob('*.py'))
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(ROOT)).encode() + b'\0' + hashlib.sha256(path.read_bytes()).digest())
    try:
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        head = None
    return {'git_head': head, 'src_sha256': digest.hexdigest()}


def read_scene_inputs(config_path):
    config, config_hash = read_input(config_path)
    scene_data, scene_hash = read_input(config_path.parent / config['scene'], config.get('scene_sha256'))
    provenance = {**version(), 'config_sha256': config_hash, 'scene_sha256': scene_hash}
    return config, scene_data, provenance


def load_scene_config(config_path):
    config, scene_data, provenance = read_scene_inputs(config_path)
    snapshot = config['snapshot_s']
    if type(snapshot) not in (int, float) or not math.isfinite(snapshot) or snapshot < 0:
        raise ValueError('snapshot_s must be finite and nonnegative')
    horizon = config['record_until_s']
    if type(horizon) not in (int, float) or not math.isfinite(horizon) or horizon < snapshot:
        raise ValueError('record_until_s must cover snapshot_s')
    return config, Scene.from_dict(scene_data), provenance


def replay_report(scene, plan, config, provenance):
    events = replay(scene, plan, config['record_until_s'])
    snapshot = config['snapshot_s']
    return {'schema': 'b.nominal-replay.v1', 'config': config, 'plan': plan.to_dict(),
            'provenance': provenance,
            'rotation_model': 'independent_rate_limited_axes', 'events': events,
            'trace_complete_until_s': max(e['t1'] for e in events), 'score': score(events, scene.targets),
            'snapshot': {'t_s': snapshot, 'vehicles': state_at(scene, events, snapshot)}}


def run(config_path):
    config, scene, provenance = load_scene_config(config_path)
    document, plan_hash = read_input(config_path.parent / config['plan'], config.get('plan_sha256'))
    plan = Plan.load(document, config['method'], provenance['scene_sha256'])
    return replay_report(scene, plan, config, {**provenance, 'plan_sha256': plan_hash})


def baseline_report(scene, config, provenance, order, rule):
    plan, ties = generate_plan(scene, provenance['scene_sha256'], config['stations'], order, rule)
    report = replay_report(scene, plan, config, provenance)
    report['allocation'] = allocation_summary(plan, report['events'], report['score'], ties)
    return report


def run_baselines(config_path, rule=None, order=None):
    config, scene, provenance = load_scene_config(config_path)
    if sorted(config['order']) != sorted(scene.targets):
        raise ValueError('Configured order must contain every alert exactly once')
    if rule is not None:
        return baseline_report(scene, config, provenance, order or config['order'], rule)
    rows = [baseline_report(scene, config, provenance, allocation_order, method)['allocation']
            for allocation_order in permutations(config['order']) for method in RULES]
    return {'schema': 'b.baseline-comparison.v1', 'provenance': provenance, 'config': config, 'rows': rows}


def write_output(output, inputs, records, jsonl=False):
    if any(output.resolve() == path.resolve() for path in inputs):
        raise ValueError('Output must be different from every input path')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            for record in records:
                json.dump(record, stream, ensure_ascii=False, allow_nan=False, indent=None if jsonl else 2)
                stream.write('\n')
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_q4(config_path, manifest_path, plan_override, output, allow_stress=False, *, planner_config=None):
    config, scene_data, provenance = read_scene_inputs(config_path)
    scene = Scene.from_dict(scene_data)
    if plan_override is None:
        plan_path = config_path.parent / config['plan']
        document, plan_hash = read_input(plan_path, config.get('plan_sha256'))
        method = config['method']
    else:
        plan_path = plan_override
        document, plan_hash = read_input(plan_path)
        native = document['plan'] if document.get('schema') == 'b.nominal-replay.v1' else document
        if native.get('schema') != 'b.plan.v1':
            raise ValueError('--plan expects a native Plan or a formal nominal replay containing one')
        method = native['method']
    plan = Plan.load(document, method, provenance['scene_sha256'])
    manifest, manifest_hash = read_input(manifest_path)
    rules = scene_data['q4']
    scenarios = validate_manifest(manifest, scene, rules, allow_stress)
    engine = ScenarioReplay(scene, plan, rules)
    inputs = [config_path, config_path.parent / config['scene'], plan_path, manifest_path]
    planner = None
    action, schema = 'retain', 'b.retention.v1'
    if planner_config is not None:
        settings, settings_hash = read_input(planner_config)
        planner = BeamPlanner(scene, plan, settings)
        provenance.update(planner=settings, planner_config_sha256=settings_hash)
        inputs.append(planner_config)
        action, schema = 'reschedule', 'b.reschedule.v1'
    # This identity is independent of whether the plan was carried in a replay or supplied on its own.
    canonical = json.dumps(plan.to_dict(), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    provenance.update(plan_method=method, plan_input_sha256=plan_hash, plan_sha256=hashlib.sha256(canonical).hexdigest(),
                      manifest_sha256=manifest_hash, allow_stress_recovery=allow_stress)
    counts = Counter()

    def records():
        for scenario in scenarios:
            result = engine.run(scenario, planner)
            counts[result['k'], result['N']] += 1
            recovery = scenario['recovery_s']
            yield dict(result, provenance=provenance, schema=schema,
                       stress_recovery=recovery is not None and recovery > rules['recovery_s_left_open'][1])

    plan_label = plan_path.stem if plan_override is not None else config_path.stem
    output = output or ROOT / 'runs/b/formal' / f'q4-{action}-{plan_label}-{manifest_path.stem}.jsonl'
    write_output(output, inputs, records(), jsonl=True)
    return output, counts


def run_retention(config_path, manifest_path, plan_override, output, allow_stress=False):
    return run_q4(config_path, manifest_path, plan_override, output, allow_stress)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    nominal = commands.add_parser('replay', help='Replay a fixed nominal Q2(2) plan')
    single = commands.add_parser('baseline', help='Generate and replay one Q2(2) greedy plan')
    single.add_argument('--rule', required=True, choices=RULES)
    single.add_argument('--order', nargs='+', help='Allocation order; defaults to the configured order')
    panel = commands.add_parser('compare-baselines', help='Compare all rules and alert orders on the same station pool')
    retain = commands.add_parser('retain', help='Run the preloaded fixed plan on a Q4 scenario manifest')
    reschedule = commands.add_parser('reschedule', help='Reschedule pending Q4 jobs at the first connected decision time')
    reschedule.add_argument('--planner-config', type=Path, default=ROOT / 'configs/b/q4-reschedule.json')
    for cmd in (retain, reschedule):
        cmd.add_argument('--config', type=Path, default=ROOT / 'configs/b/q22-numerical.json')
        cmd.add_argument('--plan', type=Path, help='Use the Plan from this formal replay or native Plan JSON')
        cmd.add_argument('--manifest', type=Path, default=ROOT / 'experiments/b/q4_scenarios/manifest.json')
        cmd.add_argument('--allow-stress-recovery', action='store_true', help='Include explicitly labelled recovery times after 30 s')
        cmd.add_argument('--output', type=Path, help='Output JSONL, one complete record per scenario')
    for cmd in (nominal, single, panel):
        default = 'q22-numerical.json' if cmd is nominal else 'q22-baselines.json'
        cmd.add_argument('--config', type=Path, default=ROOT / 'configs/b' / default)
        cmd.add_argument('--output', type=Path, help='Default: a separate JSON per run in runs/b/formal/')
    args = parser.parse_args()
    try:
        if args.command in ('retain', 'reschedule'):
            output, counts = run_q4(args.config, args.manifest, args.plan, args.output, args.allow_stress_recovery,
                                    planner_config=getattr(args, 'planner_config', None))
            print('k/N → scenarios:', ', '.join(f'{k}/{n}: {count}' for (k, n), count in sorted(counts.items())))
            print(f'{sum(counts.values())} scenarios → {output}')
            return
        stem = args.config.stem
        if args.command == 'replay':
            result = run(args.config)
        elif args.command == 'baseline':
            result = run_baselines(args.config, args.rule, args.order)
            stem += '-' + args.rule + '-' + '-'.join(result['allocation']['order'])
        else:
            result = run_baselines(args.config)
            stem += '-comparison'
        output = args.output or ROOT / 'runs/b/formal' / (stem + '.json')
        inputs = [args.config, args.config.parent / result['config']['scene']]
        if args.command == 'replay':
            inputs.append(args.config.parent / result['config']['plan'])
        write_output(output, inputs, (result,))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    if args.command == 'compare-baselines':
        order = result['config']['order']
        print('allocation order:', ', '.join(order))
        print('rule         latest start (s)     travel (m)     total wait (vehicle-s)')
        for row in result['rows']:
            if row['order'] == order:
                print(f"{row['rule']:10} {row['latest_s']:18.9f} {row['total_drive_m']:14.6f} {row['total_wait_s']:17.6f}")
        print(f"{len(result['rows'])} plans → {output}")
    else:
        print(f"{result['plan']['method']}: latest_start_s={result['score']['latest_start_s']:.9f} → {output}")


if __name__ == '__main__':
    main()
