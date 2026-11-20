"""Paired, no-review FCFS/EDD pilot on the public validation split."""

import argparse
import json
from pathlib import Path
from statistics import fmean, stdev

from experiments.q3_prefix.data import write_csv
from .acceptance import verify_ledger
from .data import configuration, validation_trial
from .engine import Source, Trial, execution_quote, run_trial

# Frozen training-only estimates of annotation duration from c9f0d68 (source seconds).
TRAIN_MEDIAN_D = {"P": 4.0, "A": 3.2, "L": 3.6}
ORDERS = {"FCFS": "release_s", "EDD": "due_s"}
CASES = ("S1", "S2", "S3")
SEEDS = (2026111700, 2026111701, 2026111702)


def order_policy(order):
    priority_field = ORDERS[order]

    def decide(view):
        now = view["time"]
        horizon = view["case"]["horizon_s"]
        rules = view["rules"]
        done = view["completed"]
        feasible = []
        future_releases = []
        for job_id, job in view["jobs"].items():
            release = job["release_s"]
            if now < release < horizon:
                future_releases.append(release)
            # An unfinished attempted execution ends the trial at H, so at every
            # subsequent decision the previously attempted jobs are exactly done.
            if (
                release > now
                or job_id in done
                or any(p not in done for p in job["predecessors"])
            ):
                continue
            b = max(
                rules["reserve_min_s"],
                view["alpha"] * TRAIN_MEDIAN_D[job["family_id"]] + view["beta"],
            )
            start, fee = execution_quote(
                now, view["zone"], job["zone_id"], b, view["zones"], rules
            )
            if start + b <= horizon and fee <= view["budget_left"]:
                feasible.append((job[priority_field], job_id, b))
        if feasible:
            _, job_id, b = min(feasible)
            return {"action": "EXECUTE", "job_id": job_id, "b_s": b}
        if future_releases:
            return {"action": "WAIT", "seconds": min(future_releases) - now}
        return {"action": "STOP"}

    return decide


def run_pair(trial):
    """Draw once upstream. Check both policies against the same per-job offline assignment."""
    assignments = {j: (s.ref, s.duration) for j, s in trial.sources.items()}
    assert assignments.keys() == trial.jobs.keys()
    both = {}
    for order in ORDERS:
        log = run_trial(trial, order_policy(order))
        metrics = log["metrics"]
        # verify_ledger requires an accepted ledger entry for every command.
        # Keep metrics for a violating trial rather than dropping the pair.
        if metrics["valid_protocol"]:
            verify_ledger(log)
        assert metrics["observations"] == 0
        assert assignments == {j: (s.ref, s.duration) for j, s in trial.sources.items()}
        assert all(
            e["source_ref_offline"] == assignments[e["job_id"]][0]
            for e in log["ledger"]
            if e["action"] == "EXECUTE"
        )
        both[order] = log
    return both


def paired_summary(rows, metric_names):
    for case in CASES:
        pairs = {(r["seed"], r["order"]): r for r in rows if r["case"] == case}
        for name in metric_names:
            left = [pairs[seed, "FCFS"][name] for seed in SEEDS]
            right = [pairs[seed, "EDD"][name] for seed in SEEDS]
            deltas = [b - a for a, b in zip(left, right)]
            yield dict(
                case=case,
                metric=name,
                pairs=len(deltas),
                fcfs_mean=fmean(left),
                edd_mean=fmean(right),
                mean_edd_minus_fcfs=fmean(deltas),
                sd_paired_difference=stdev(deltas),
            )


def pilot(out):
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    metrics = None
    for case in CASES:
        for seed in SEEDS:
            trial = validation_trial(case, seed)
            assert (trial.alpha, trial.beta) == (1, 0.5)
            both = run_pair(trial)
            if metrics is None:
                metrics = tuple(both["FCFS"]["metrics"])
            for order, log in both.items():
                rows.append(
                    dict(
                        case=case,
                        seed=seed,
                        split="validation",
                        alpha=trial.alpha,
                        beta_s=trial.beta,
                        order=order,
                        matched_jobs=len(trial.jobs),
                        **log["metrics"],
                    )
                )
            if (case, seed) == (CASES[0], SEEDS[0]):
                if not all(log["metrics"]["valid_protocol"] for log in both.values()):
                    write_csv(out / "order_gate_failed.csv", rows)
                    raise RuntimeError(
                        "First pair failed; both outcomes saved to order_gate_failed.csv"
                    )
                print(
                    f"Gate passed: {case}/{seed}, {len(trial.jobs)} common assignments, two ledgers verified.",
                    flush=True,
                )
    write_csv(out / "order_trials.csv", rows)
    write_csv(out / "order_paired.csv", paired_summary(rows, metrics))
    verified = sum(r["valid_protocol"] for r in rows)
    print(
        f"{len(rows) // 2} pairs, {verified}/{len(rows)} verified ledgers; outputs in {out}"
    )


def synthetic_log():
    rules, zones = configuration()
    # Artificial tie in release times. A sorts first by ID; B has the earlier due date.
    jobs = {
        j: dict(
            job_id=j,
            family_id=family,
            release_s=0,
            due_s=due,
            priority_weight=1,
            zone_id="Z1",
            predecessors=[],
        )
        for j, family, due in [("A", "P", 20), ("B", "A", 6)]
    }
    sources = {
        j: Source("synthetic/" + j, d, [{"timestamp_s": 0}])
        for j, d in [("A", 5), ("B", 2)]
    }
    case = dict(rules["cases"][0], case_id="synthetic_order", jobs=len(jobs))
    both = run_pair(Trial(jobs, sources, zones, case, rules))
    return {
        order: dict(ledger=log["ledger"], metrics=log["metrics"])
        for order, log in both.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=["pilot", "synthetic"], nargs="?", default="pilot"
    )
    parser.add_argument("--out", type=Path, default=Path("runs/q3_order"))
    args = parser.parse_args()
    if args.mode == "synthetic":
        print(json.dumps(synthetic_log(), indent=2, allow_nan=False))
    else:
        pilot(args.out)


if __name__ == "__main__":
    main()
