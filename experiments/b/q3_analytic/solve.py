#!/usr/bin/env python3
"""Independent Q3 derivation: circular probabilities and first-order budgets.

Reads only the common scene and the official problem; writes only its own run dir.
SciPy integration and an independent projected-normal angular density agree at
checked points. No other Q3 implementation or result is imported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import scipy
from scipy.integrate import quad
from scipy.optimize import brentq
from scipy.special import ndtr
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = ROOT / "runs/b/q3_analytic"
RAD_TO_DEG = 180.0 / math.pi
NORMAL_CUTOFF = 10.0


def normal_density(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def wrap(angle):
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def linear_probability(sigma_m: float, range_m: float, tau_rad: float,
                       gimbal_sd_rad: float) -> float:
    sd = math.hypot(sigma_m / range_m, gimbal_sd_rad)
    return 1.0 if sd == 0 else math.erf(tau_rad / (math.sqrt(2.0) * sd))


def wrapped_gaussian_window(angle: float, sd: float, tau: float) -> float:
    """P(|wrap(angle+G)|<=tau), for angle in [-pi,pi] and tiny gimbal SD.

    Images -1,0,1 suffice here; all omitted intervals are over 500 SD away.
    """
    if sd == 0:
        return float(abs(float(wrap(angle))) <= tau)
    result = 0.0
    for image in (-1, 0, 1):
        lo = (-tau - angle + 2.0 * math.pi * image) / sd
        hi = (tau - angle + 2.0 * math.pi * image) / sd
        # Survival-function symmetry avoids 1 - 1 in an upper tail.
        if lo >= 0:
            result += float(ndtr(-lo) - ndtr(-hi))
        else:
            result += float(ndtr(hi) - ndtr(lo))
    return result


def circular_probability(sigma_m: float, range_m: float, tau_rad: float,
                         gimbal_sd_rad: float) -> float:
    """Fast cone/half-plane quadrature, within stated negligible tail bound.

    This is NOT globally exact at arbitrary sigma. With |G|<=10*g and
    tau+10*g < pi/2, the omitted opposite cone has mass <= Phi(-range/sigma).
    The other omitted mass is <= 2*Phi(-10). In the operating bracket
    sigma<=2 m at range=40 m this is <1.53e-23 before integration roundoff.
    """
    if sigma_m == 0:
        return wrapped_gaussian_window(0.0, gimbal_sd_rad, tau_rad)
    if tau_rad + NORMAL_CUTOFF * gimbal_sd_rad >= math.pi / 2:
        raise ValueError("Cone tail bound requires tau + 10*gimbal_sd < pi/2")
    kappa = range_m / sigma_m
    if gimbal_sd_rad == 0:
        return math.erf(kappa * math.sin(tau_rad) / math.sqrt(2.0))

    def integrand(z):
        p = (ndtr(kappa * math.sin(tau_rad - gimbal_sd_rad * z))
             + ndtr(kappa * math.sin(tau_rad + gimbal_sd_rad * z)) - 1.0)
        return normal_density(z) * p

    return float(quad(integrand, -NORMAL_CUTOFF, NORMAL_CUTOFF,
                      epsabs=2e-13, epsrel=2e-13, limit=200)[0])


def cone_error_bound(sigma_m: float, range_m: float, gimbal_sd_rad: float) -> float:
    return float(ndtr(-range_m / sigma_m)
                 + (2 * ndtr(-NORMAL_CUTOFF) if gimbal_sd_rad else 0.0))


def projected_normal_density(angle: float, kappa: float) -> float:
    """Density of Arg((kappa+X)+iY), X,Y iid N(0,1)."""
    c = kappa * math.cos(angle)
    base = math.exp(-0.5 * kappa * kappa) / (2.0 * math.pi)
    term = (c / math.sqrt(2.0 * math.pi)
            * math.exp(-0.5 * (kappa * math.sin(angle)) ** 2) * ndtr(c))
    value = base + term
    # Cancellation can make a negligible negative-cosine tail slightly negative.
    return max(0.0, float(value))


def angular_probability(sigma_m: float, range_m: float, tau_rad: float,
                        gimbal_sd_rad: float) -> tuple[float, float]:
    """Independent full-angle quadrature of projected normal * wrapped Gaussian.

    It includes the opposite cone omitted by circular_probability and remains
    valid at large sigma. Symmetry reduces [-pi,pi] to [0,pi].
    """
    if sigma_m == 0:
        return wrapped_gaussian_window(0.0, gimbal_sd_rad, tau_rad), 0.0
    kappa = range_m / sigma_m
    if gimbal_sd_rad == 0:
        end = tau_rad
        points = [sigma_m / range_m, 4 * sigma_m / range_m]
        integrand = lambda angle: projected_normal_density(angle, kappa)
    else:
        end = math.pi
        points = [tau_rad, sigma_m / range_m, 4 * sigma_m / range_m,
                  12 * sigma_m / range_m, tau_rad + 10 * gimbal_sd_rad]
        integrand = lambda angle: (projected_normal_density(angle, kappa)
                                   * wrapped_gaussian_window(angle, gimbal_sd_rad, tau_rad))
    points = sorted({p for p in points if 0 < p < end})
    value, error = quad(integrand, 0.0, end, points=points,
                        epsabs=2e-12, epsrel=2e-12, limit=300)
    return 2 * value, 2 * error


def wilson_interval(successes: int, n: int) -> list[float]:
    z = float(norm.ppf(0.975))
    p = successes / n
    denominator = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [center - half, center + half]


def simulate(q: dict, s: float, n: int, seed: int) -> dict:
    """Use every original error component, not a pre-aggregated bearing normal."""
    rng = np.random.default_rng(seed)
    sigma = q["position_sd_intercept_m"] + q["position_sd_slope_m_per_m"] * s
    r = q["range_m"]
    tau = math.radians(q["tolerance_deg"])
    sigma_zero = math.radians(q["zero_sd_deg"])
    sigma_control = math.radians(q["control_sd_deg"])
    counts = {"command": 0, "actual": 0, "actual_linear": 0}
    paired_sum = paired_square_sum = 0.0
    remaining = n
    while remaining:
        m = min(remaining, 200_000)
        # X,Y are longitudinal/transverse errors; isotropy makes this WLOG.
        u, v, ez, ec = rng.standard_normal((4, m))
        u *= sigma
        v *= sigma
        ez *= sigma_zero
        ec *= sigma_control
        command = np.arctan2(-v, r - u)
        actual = wrap(command + ez + ec)
        linear = -v / r + ez + ec
        ok_command = np.abs(command) <= tau
        ok_actual = np.abs(actual) <= tau
        ok_linear = np.abs(linear) <= tau
        counts["command"] += int(np.count_nonzero(ok_command))
        counts["actual"] += int(np.count_nonzero(ok_actual))
        counts["actual_linear"] += int(np.count_nonzero(ok_linear))
        difference = ok_actual.astype(float) - ok_linear.astype(float)
        paired_sum += float(difference.sum())
        paired_square_sum += float(np.square(difference).sum())
        remaining -= m
    delta = paired_sum / n
    delta_se = math.sqrt(max(0.0, paired_square_sum / n - delta * delta) / (n - 1))
    return {
        "travel_m": s, "samples": n, "seed": seed,
        "estimates": {name: {"probability": count / n,
                             "wilson_95": wilson_interval(count, n)}
                      for name, count in counts.items()},
        "paired_actual_minus_linear": {"mean": delta, "standard_error": delta_se},
    }


def extract_source(source: Path, out: Path) -> None:
    from pypdf import PdfReader
    reader = PdfReader(str(source))
    text = "\n\n".join(f"OFFICIAL PDF PAGE {i + 1}\n{reader.pages[i].extract_text()}"
                         for i in (3, 4))
    (out / "english_pages_4_5.txt").write_text(text, encoding="utf-8", errors="backslashreplace")


def solve(out: Path, mc_samples: int, seed: int, save_source: bool) -> dict:
    scene_path = ROOT / "experiments/b/scene.json"
    scene_bytes = scene_path.read_bytes()
    scene = json.loads(scene_bytes)
    q = scene["q3"]
    source = ROOT / scene["source"]
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_hash != scene["sha256"]:
        raise ValueError("Official PDF SHA256 differs from scene source fingerprint")
    out.mkdir(parents=True, exist_ok=True)
    if save_source:
        extract_source(source, out)

    r = q["range_m"]
    a = q["position_sd_intercept_m"]
    b = q["position_sd_slope_m_per_m"]
    tau = math.radians(q["tolerance_deg"])
    coverage = q["coverage"]
    design_s = q["design_travel_m"]
    g = math.radians(math.hypot(q["zero_sd_deg"], q["control_sd_deg"]))
    z = float(norm.ppf((1.0 + coverage) / 2.0))
    c = RAD_TO_DEG / r
    summaries = {}
    for name, g_sd in (("actual", g), ("command", 0.0)):
        linear_cap = r * math.sqrt((tau / z) ** 2 - g_sd ** 2)
        # Strictly local operating bracket with quantified negligible cone tails.
        circular_cap = brentq(lambda sig: circular_probability(sig, r, tau, g_sd) - coverage,
                              0.1, 2.0, xtol=1e-13, rtol=1e-14)
        methods = {}
        for method, cap in (("first_order", linear_cap), ("circular", circular_cap)):
            methods[method] = {
                "sigma_cap_m": cap,
                "max_travel_m": (cap - a) / b,
                "region_80m": {
                    "constraint": f"a >= 0; b >= 0; a + {design_s:g} b <= sigma_cap_m",
                    "a_intercept_m": cap,
                    "b_intercept_m_per_m": cap / design_s,
                    "b_max_if_a_0p3_m_per_m": (cap - a) / design_s,
                    "a_max_if_b_0p02_m": cap - b * design_s,
                },
            }
        methods["circular"].update({
            "probability_at_cap": circular_probability(circular_cap, r, tau, g_sd),
            "independent_angular_probability_at_cap": angular_probability(circular_cap, r, tau, g_sd)[0],
            "cone_tail_bound_at_cap": cone_error_bound(circular_cap, r, g_sd),
        })
        methods["linear_overstates_max_travel_m"] = (linear_cap - circular_cap) / b
        summaries[name] = methods

    distances = [0.0, 10.0, 20.0, 40.0, 60.0,
                 summaries["actual"]["circular"]["max_travel_m"],
                 summaries["command"]["circular"]["max_travel_m"], 80.0]
    rows = []
    for s in distances:
        sig = a + b * s
        parts = [(c * sig) ** 2, q["zero_sd_deg"] ** 2, q["control_sd_deg"] ** 2]
        total = sum(parts)
        rows.append({
            "travel_m": s, "position_component_sd_m": sig,
            "linear_variance_deg2": dict(zip(("localization", "zero", "control", "total"), parts + [total])),
            "linear_fraction": dict(zip(("localization", "zero", "control"), [p / total for p in parts])),
            "linear_actual_sd_deg": math.sqrt(total),
            "actual_p_linear": linear_probability(sig, r, tau, g),
            "actual_p_circular": circular_probability(sig, r, tau, g),
            "command_p_linear": linear_probability(sig, r, tau, 0),
            "command_p_circular": circular_probability(sig, r, tau, 0),
        })

    checks = []
    for s in distances:
        sig = a + b * s
        for name, g_sd in (("actual", g), ("command", 0.0)):
            angular, integration_error = angular_probability(sig, r, tau, g_sd)
            cone = circular_probability(sig, r, tau, g_sd)
            checks.append({"travel_m": s, "reading": name, "cone_probability": cone,
                           "angular_probability": angular, "absolute_difference": abs(cone - angular),
                           "angular_quad_error_estimate": integration_error})
    max_diff = max(item["absolute_difference"] for item in checks)
    assert max_diff < 1e-10, f"Independent quadratures disagree: {max_diff}"

    monotone = {}
    sigma_grid = np.linspace(0.1, 2.0, 201)
    for name, g_sd in (("actual", g), ("command", 0.0)):
        p = np.array([circular_probability(float(sig), r, tau, g_sd) for sig in sigma_grid])
        increments = np.diff(p)
        assert float(increments.max()) <= 1e-12
        monotone[name] = {"sigma_grid_m": [0.1, 2.0], "points": len(sigma_grid),
                          "largest_positive_increment": max(0.0, float(increments.max()))}

    large_sigma_checks = []
    for kappa in (1.0, 0.1, 0.01, 0.001):
        sig = r / kappa
        large_sigma_checks.append({"range_over_sigma": kappa,
                                   "command_p": angular_probability(sig, r, tau, 0.0)[0],
                                   "actual_p": angular_probability(sig, r, tau, g)[0]})

    result = {
        "interpretation": {"main_report": "actual physical pointing: wrap(command deviation + zero + control)",
                           "literal_delta_psi": "command deviation only; zero/control do not enter it",
                           "main_is_explicit_engineering_interpretation_not_literal_redefinition": True},
        "inputs": q,
        "source": {"pdf": scene["source"], "pdf_sha256": source_hash,
                   "scene_sha256": hashlib.sha256(scene_bytes).hexdigest(),
                   "pages_read": [4, 5],
                   "translation": "problem/translations/B题-协同观测与调度.md:102-122,151"},
        "constants": {"z_0p975": z, "max_total_sd_deg": q["tolerance_deg"] / z,
                      "max_total_variance_deg2": (q["tolerance_deg"] / z) ** 2,
                      "position_sd_to_bearing_sd_deg_per_m": c,
                      "combined_gimbal_sd_deg": math.degrees(g)},
        "thresholds": summaries,
        "distance_decomposition": rows,
        "crossover_travel_m": {
            "position_equals_zero": (q["zero_sd_deg"] / c - a) / b,
            "position_equals_control": (q["control_sd_deg"] / c - a) / b,
            "position_equals_both_gimbal_terms": (math.degrees(g) / c - a) / b,
        },
        "validation": {
            "independent_quadrature_max_abs_difference": max_diff,
            "cone_domain_sigma_m": [0.1, 2.0],
            "max_cone_tail_bound_in_domain": cone_error_bound(2.0, r, g),
            "monotonicity_grid": monotone,
            "sigma_zero_actual_probability": wrapped_gaussian_window(0.0, g, tau),
            "sigma_zero_command_probability": 1.0,
            "sigma_infinite_both_probability": tau / math.pi,
            "large_sigma_angular_checks": large_sigma_checks,
            "safe_practical_travel_66p8m_probability": circular_probability(a + b * 66.8, r, tau, g),
            "original_model_80m_fails_even_perfect_gimbal": circular_probability(a + b * design_s, r, tau, 0.0) < coverage,
        },
        "runtime": {"numpy": np.__version__, "scipy": scipy.__version__},
    }
    (out / "quadrature_checks.json").write_text(json.dumps(checks, indent=2) + "\n", encoding="utf-8")
    if mc_samples:
        monte_carlo = [simulate(q, s, mc_samples, seed + index)
                       for index, s in enumerate((0.0, summaries["actual"]["circular"]["max_travel_m"], 80.0))]
        result["validation"]["monte_carlo"] = monte_carlo
    (out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    compact = {
        "main": "actual", "source_sha256": source_hash,
        "actual": summaries["actual"], "command_literal": summaries["command"],
        "p_80m": {k: rows[-1][k] for k in ("actual_p_linear", "actual_p_circular", "command_p_linear", "command_p_circular")},
        "max_quadrature_difference": max_diff,
    }
    (out / "compact.json").write_text(json.dumps(compact, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--mc-samples", type=int, default=1_000_000,
                        help="Independent samples per checked mileage; 0 skips Monte Carlo")
    parser.add_argument("--seed", type=int, default=20261120)
    parser.add_argument("--extract-source", action="store_true", help="Also retain PDF pages 4-5 text (requires pypdf)")
    args = parser.parse_args()
    allowed = (ROOT / "runs/b/q3_analytic", ROOT / "experiments/b/q3_analytic")
    out = args.out.resolve()
    if not any(out == path or path in out.parents for path in allowed):
        parser.error("Output must stay inside runs/b/q3_analytic or experiments/b/q3_analytic")
    if args.mc_samples < 0 or args.mc_samples == 1:
        parser.error("--mc-samples must be 0 (skip) or at least 2")
    result = solve(out, args.mc_samples, args.seed, args.extract_source)
    print(json.dumps({"out": str(out),
                      "actual_smax_m": result["thresholds"]["actual"]["circular"]["max_travel_m"],
                      "command_smax_m": result["thresholds"]["command"]["circular"]["max_travel_m"],
                      "independent_quadrature_max_abs_difference": result["validation"]["independent_quadrature_max_abs_difference"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
