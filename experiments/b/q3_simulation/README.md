# Q3 independent forward simulation

Scope: independent analysis of Problem B, Question 3, based only on the English PDF, its Chinese translation, and `../scene.json`. No other Q3 work is read/imported. All files produced by this code remain in this directory (or an explicitly selected `runs/b/q3_simulation/` directory). No commit is made.

## Run

From the repository root:

```bash
python3 experiments/b/q3_simulation/test_simulation.py
python3 experiments/b/q3_simulation/simulation.py
```

Runtime dependencies: Python 3.10+, NumPy, SciPy. Recorded environment: Python 3.10.12, NumPy 2.2.4, SciPy 1.15.2. The optional `pypdf` package was used only to read the English question; it is not a simulation dependency.

Default simulation uses 4,000,000 root-fitting samples, 4,000,000 independent validation samples, and 500,000 diagnostic samples. Seed entropy is 2026112003. The three streams are distinct PCG64 generators from NumPy SeedSequence child keys `(0,)`, `(1,)`, and `(2,)`. The sample layout is row-major `(dx, dy, zero, control)` standard normals. Arrays of angles are evaluated in chunks of 250,000. Root fitting stores 128 MB of normal draws; normal peak memory is a few hundred MB. The recorded full run takes approximately 27 seconds on this machine with one BLAS thread; allow a few minutes on a slower CPU. This is not a large cluster calculation.

To run a smaller smoke experiment without overwriting the delivered full results:

```bash
python3 experiments/b/q3_simulation/simulation.py --fit-n 200000 --validation-n 200000 --check-n 50000 --output-dir runs/b/q3_simulation/smoke
```

`--seed`, `--chunk`, `--fit-n`, `--validation-n`, `--check-n`, `--output-dir` are supported. Changing sample sizes changes uncertainty and may alter whether an interval classifies a near-threshold point. Default results in `results.json` and `report.md` must be regenerated together. Runs with the same NumPy/SciPy versions, seeds and sample sizes reproduce the statistical results; elapsed wall time is naturally different.

## Files

- `simulation.py`: original-coordinate, original-`atan2` Monte Carlo; root fit; independent validation; geometry, independence and source checks; secondary independently derived polar-density quadrature. Generates both results files below.
- `test_simulation.py`: fast unit tests, including sign, wrap, rotation, translation, units, source composition, intervals, chunk invariance, and quadrature checks.
- `results.json`: all machine-readable input hashes, random streams, sample sizes, fitted roots/uncertainty, validation counts/intervals, source contributions, diagnostics and numerical integration comparison.
- `report.md`: Chinese report with results, assumptions and uncertainty cautions.

The `total` convention includes separately generated 0.8° zero and 0.6° control errors; `command_only` follows the literal command-minus-true definition and excludes them. Results must never be mixed between conventions.

Root-fitting and validation sets are separate. Reusing a fitting sample produces a fitted 95% coverage, not a fresh validation. Each independent-validation point is frozen using only the fitting sample. Validation points may share draws: pointwise Wilson intervals are labelled, and exact Bonferroni-adjusted Clopper–Pearson intervals protect the complete fixed family. Root intervals use a local delta approximation, not the binomial interval of an adaptively chosen root.

The secondary polar integral is evaluated only after the Monte Carlo design and validation are fixed. It does not feed the Monte Carlo roots or generate the angular errors. It checks the modest nonlinear correction to the first-order model and supplies high-precision `a+80b` limits within the stated Gaussian assumptions.
