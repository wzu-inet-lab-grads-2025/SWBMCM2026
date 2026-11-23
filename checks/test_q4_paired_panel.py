"""第四问适配回归：复播真实毫米初排下的单故障及联合断联场景。

运行：nohup python3 -B -m pytest -q -p no:cacheprovider checks/test_q4_paired_panel.py。
AI 辅助：OpenCode / Magpie，路由标识 group/gpt-6.2-sol，2026-11-22（底层机构及版本发布日期未提供）。
"""

from itertools import islice

import pytest

from checks.q4_paired_panel import PEER, Q4, ReplayAdapter, json_rows


@pytest.mark.skipif(not PEER.is_dir(), reason='分区搜索工作树未挂载到本项目同级目录')
def test_local_replay_preserves_failure_at_exact_dispatch_boundary():
    cases = {('E2_V1', 'main'), ('E4_A0_R30_V1', 'main'), ('E6_V1_C10', 'strict_outage')}
    inputs = {(r['scenario_id'], r['variant']): r for r in islice(
        (row for row in json_rows(Q4 / 'scenario_inputs.jsonl.gz') if (row['scenario_id'], row['variant']) in cases), len(cases))}
    frozen = {(r['scenario_id'], r['variant']): r for r in islice(
        (row for row in json_rows(Q4 / 'plans_and_tracks.jsonl.gz')
         if row['model'] == 'strict_frozen' and (row['scenario_id'], row['variant']) in cases), len(cases))}
    assert set(inputs) == set(frozen) == cases
    adapter = ReplayAdapter(PEER)
    for key in sorted(cases):
        result = adapter.solve(inputs[key], frozen[key])
        phases = result['tracks'][0]  # 三例均为 V1 故障，5 s 后不允许主动相位。
        assert [(p.start, p.end) for p in phases if p.kind == 'FAILED'] == [(5.0, 60.0)]
        assert all(p.end <= 5 for p in phases if p.kind != 'FAILED')
