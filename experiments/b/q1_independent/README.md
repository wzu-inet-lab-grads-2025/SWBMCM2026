# Q1 independent experiment

Independently derives the observable region, proves the continuous optimum,
selects a station by a declared travel-distance tie-break, and cross-checks the
3D geometry. Authoritative inputs: English original pages 1–3; Chinese used only
for cross-checking. Coordinates and lengths are metres, angles degrees, Q is m⁻¹.

## Files

- `scene.json`: independent transcription, source paths, boundary policy.
- `solve.py`: analytic solution, three visibility oracles, boundary tests,
  seeded validation, continuous numerical corroboration, area checks and plots.
- `audit_source.py`: preserves English text and renders original pages 1–3.
- `REPORT.md`: derivation, proof, complete optimal arc, numeric certificates,
  results, limitations and proposed modeling decisions.
- `requirements.txt`: pinned direct dependencies for this run (Python 3.10.12).

No other experimental implementation is imported. Outputs, dependency installs
and rendering caches remain in `runs/b/q1_independent/`; nothing is committed.

## Reproduce from the repository root

If dependencies are already available:

```bash
python3 experiments/b/q1_independent/audit_source.py
python3 experiments/b/q1_independent/solve.py
```

To install dependencies entirely within the allowed local run directory:

```bash
mkdir -p runs/b/q1_independent/_deps runs/b/q1_independent/_tmp
TMPDIR="$PWD/runs/b/q1_independent/_tmp" PIP_DISABLE_PIP_VERSION_CHECK=1 python3 -m pip install --no-cache-dir --target runs/b/q1_independent/_deps -r experiments/b/q1_independent/requirements.txt
python3 experiments/b/q1_independent/audit_source.py
python3 experiments/b/q1_independent/solve.py
```

Scripts automatically detect `_deps`, so no `PYTHONPATH` changes are required.
An existing `_deps` installation need not be reinstalled on each run. Pypdfium2
is only needed for `audit_source.py`, not the numerical solution. Source auditing
renders the original PDF, not newly generated illustrations.

Actual environment had NumPy 2.2.4, SciPy 1.15.2 and pypdf 6.21.4 preinstalled;
matplotlib 3.10.8, NumPy 2.2.4 and pypdfium2 4.30.0 were installed locally using
the same `--no-cache-dir --target` convention.

Default numerical run:

```bash
python3 experiments/b/q1_independent/solve.py --seed 20261120 --samples 250000 --plot-step 0.1 --grid-steps 0.2 0.1 0.05
```

`--no-plot` runs the same mathematical checks without requiring matplotlib.
Explicit random seed, sample size, command, package versions, input/code hashes,
and repository HEAD are written into `result.json`. Local solver failures are
retained, not filtered out. The proof—not a grid or optimizer—is the global
optimality certificate.

## Main result

- Quality minimum-radius circle: `r*=43 cot(80°)=7.582060170464 m`.
- Maximum quality: `Q*=0.131890274875 m^-1`, elevation `80°`.
- Complete quality-optimal arc around T1':
  `[0°,160.747873328323°] ∪ [236.896046853831°,360°)`.
- Minimum feasible travel tie-break chooses V4 and
  `P*=(91.155710852759,66.121032434226) m`, travel `3.672741496410 m`.
- Area (auxiliary numerical check): approximately `5608.77 m²`.
- 1,250,000 shadow/slab comparisons: zero mismatches; 500 LP checks:
  zero mismatches; 37/37 explicit boundary tests pass.

## Output paths

All below are under `runs/b/q1_independent/`:

- `observable_region.png`, `.pdf`, `.svg`: scientific region map + optimal-arc
  close-up, equal axis scaling, units, legend and boundary-policy note.
- `result.json`: full independent certificate and raw validation results.
- `boundary_checks.csv`, `grid_convergence.csv`.
- `source_audit.json`, `english_pages_1_3.txt`, `english_page_1.png` through
  `english_page_3.png`.

## Limitations / decision impact

Nominal static geometry only; no Q2 timing, uncertainty, dynamic models or
additional use. Area and plot rasterization are not exact analytic area proofs.
Random tests cannot alone establish global correctness; they supplement the
set derivation and explicit boundary tests. The Q1 optimum is not unique before
the declared tie-break. Vehicles touching walls may be admitted under the
English wording; LOS contact is strictly rejected and 80° is included. Different
reasonable tie-breaks can give different equally optimal coordinates.

Proposed modeling decisions and corner cases are documented in `REPORT.md`;
parent-session docs have deliberately not been modified.
