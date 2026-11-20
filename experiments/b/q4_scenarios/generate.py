#!/usr/bin/env python3
"""Deterministic Q4 inputs. Standard library only; writes inside this directory."""
import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import itertools
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
VEHICLES = tuple(f"V{i}" for i in range(1, 6))
ALERTS = tuple((theta, radius, 50) for theta in (30, 150, 270) for radius in (30, 50))
MAIN_RECOVERIES = (10, 30)
SENSITIVITY_RECOVERIES = (8, 20, 35)
FIELDS = {"id", "new_alert", "failed_vehicle", "recovery_s"}


def combination(row):
    return "".join(letter for letter, key in (("A", "new_alert"), ("F", "failed_vehicle"), ("C", "recovery_s"))
                   if row[key] is not None) or "none"


def rows_for(recoveries, *, prefix, include_no_outage):
    rows = []
    for alert, failed, recovery in itertools.product(
            (None,) + ALERTS, (None,) + VEHICLES,
            ((None,) if include_no_outage else ()) + tuple(recoveries)):
        alert_code = "a-none" if alert is None else f"a-{alert[0]:03d}-{alert[1]:02d}"
        fault_code = "f-none" if failed is None else f"f-{failed}"
        comm_code = "c-none" if recovery is None else f"c-{recovery:02d}"
        rows.append({"id": f"{prefix}_{alert_code}_{fault_code}_{comm_code}",
                     "new_alert": list(alert) if alert is not None else None,
                     "failed_vehicle": failed, "recovery_s": recovery})
    return rows


def validate(rows, recoveries, *, include_no_outage):
    assert len({row["id"] for row in rows}) == len(rows), "duplicate ID"
    signatures = set()
    failure_groups = defaultdict(list)
    for row in rows:
        assert set(row) == FIELDS, row
        alert = row["new_alert"]
        if alert is not None:
            assert isinstance(alert, list) and len(alert) == 3
            theta, radius, height = alert
            assert 0 <= theta < 360 and 30 <= radius <= 50 and height == 50
        assert row["failed_vehicle"] in (None,) + VEHICLES
        assert row["recovery_s"] in ((None,) if include_no_outage else ()) + tuple(recoveries)
        key = (tuple(alert) if alert else None, row["failed_vehicle"], row["recovery_s"])
        assert key not in signatures, "duplicate physical scenario"
        signatures.add(key)
        if row["failed_vehicle"] is not None:
            failure_groups[(tuple(alert) if alert else None, row["recovery_s"])].append(row["failed_vehicle"])
    expected = set(itertools.product((None,) + ALERTS, (None,) + VEHICLES,
                                    ((None,) if include_no_outage else ()) + tuple(recoveries)))
    assert signatures == expected, "incomplete factor cross"
    for identities in failure_groups.values():
        assert Counter(identities) == Counter(VEHICLES), "not all five identities exactly once"
        assert sum((Fraction(1, 5) for _ in identities), Fraction(0)) == 1
    return {"count": len(rows), "combinations": dict(sorted(Counter(map(combination, rows)).items())),
            "failure_groups": len(failure_groups), "conditional_failure_weight": "1/5",
            "failure_identity_counts": dict(Counter(row["failed_vehicle"] for row in rows
                                                     if row["failed_vehicle"] is not None))}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def build():
    main = rows_for(MAIN_RECOVERIES, prefix="main", include_no_outage=True)
    extension = rows_for(SENSITIVITY_RECOVERIES, prefix="sensitivity", include_no_outage=False)
    main_checks = validate(main, MAIN_RECOVERIES, include_no_outage=True)
    extension_checks = validate(extension, SENSITIVITY_RECOVERIES, include_no_outage=False)
    assert len(main) == 126 <= 150
    assert main_checks["combinations"] == {"none": 1, "A": 6, "F": 5, "C": 2,
                                           "AF": 30, "AC": 12, "FC": 10, "AFC": 60}
    assert all(row["recovery_s"] is None or 10 <= row["recovery_s"] <= 30 for row in main)
    assert {row["recovery_s"] for row in main} == {None, 10, 30}
    assert set(row["id"] for row in main).isdisjoint(row["id"] for row in extension)
    assert all(tuple(alert) in ALERTS for row in main if (alert := row["new_alert"]) is not None)
    main_json = encoded({"scenarios": main})
    sensitivity_json = encoded({"scenarios": extension})
    notes = {
        "schema_version": 1,
        "disturbance_s": 5,
        "observation_window_s": [0, 60],
        "main_manifest": "manifest.json",
        "main_sha256": hashlib.sha256(main_json.encode()).hexdigest(),
        "sensitivity_manifest": "sensitivity.json",
        "sensitivity_sha256": hashlib.sha256(sensitivity_json.encode()).hexdigest(),
        "main_design": "Complete (1+3 azimuths x 2 ranges) x (1+5 failures) x (1+2 recoveries)",
        "new_alerts": [list(alert) for alert in ALERTS],
        "main_recovery_s": list(MAIN_RECOVERIES),
        "sensitivity_recovery_s": list(SENSITIVITY_RECOVERIES),
        "official_clarification": {
            "evidence_file": "public_evidence.json",
            "recovery_s": "(5,30]",
            "outage_duration_s": "(0,25]",
            "main_range_note": "[10,30] is a conservative design subset, not the full clarified domain."},
        "sensitivity_interpretation": {
            "8": "in clarified domain; outage duration = 3 s; outside original-text intersection",
            "20": "in clarified domain; interior timing check; outage duration = 15 s",
            "35": "OUT OF clarified domain; stress test of superseded duration reading; duration = 30 s"},
        "probability_policy": {
            "failure_identity_conditional_on_fixed_alert_and_recovery": {v: "1/5" for v in VEHICLES},
            "alert_occurrence_or_location": None,
            "outage_occurrence_or_recovery": None,
            "disturbance_combination": None,
            "note": "No population weights. Panel means describe this finite design only, not expected real-world performance."},
        "main_acceptance": main_checks,
        "sensitivity_acceptance": extension_checks,
    }
    return {"manifest.json": main_json, "sensitivity.json": sensitivity_json, "design.json": encoded(notes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="assert committed inputs equal deterministic regeneration")
    args = parser.parse_args()
    outputs = build()
    for name, content in outputs.items():
        path = HERE / name
        if args.check:
            assert path.read_text(encoding="utf-8") == content, f"regeneration differs: {path}"
        else:
            path.write_text(content, encoding="utf-8")
    print("PASS: main=126; sensitivity=126 (42 each at recovery 8,20,35 s); full five-identity fault blocks")


if __name__ == "__main__":
    main()
