#!/bin/sh
set -eu
cd "$(dirname "$0")/../../.."
python3 -B experiments/b/q4_reschedule/reschedule.py --manifest experiments/b/q4_scenarios/manifest.json -o runs/b/q4_reschedule/main.jsonl
python3 -B experiments/b/q4_reschedule/reschedule.py --manifest experiments/b/q4_scenarios/sensitivity.json -o runs/b/q4_reschedule/sensitivity.jsonl
python3 -B experiments/b/q4_reschedule/verify.py
