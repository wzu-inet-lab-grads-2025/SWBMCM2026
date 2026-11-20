"""Deterministic circular bearing probabilities for ShuWei 2026 B, problem 3."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import platform

import numpy as np
import scipy
from scipy.integrate import quad
from scipy.optimize import brentq
from scipy.special import erfcx, ndtr, ndtri, roots_hermitenorm


HERE = Path(__file__).resolve().parent
SCENE = HERE.parent / "scene.json"
PI = math.pi
TURN = 2 * PI
SQRT2PI = math.sqrt(TURN)
QUAD_ABS = 1e-14
QUAD_REL = 3e-13
ROOT_ABS_M = 1e-13
N_SIMPSON = 32768
L_NORMAL = 10
FLOAT_ALLOWANCE = 2e-14  # engineering allowance for binary64 / libm, not interval arithmetic


def wrap(angle):
    return (angle + PI) % TURN - PI


def command_deviation(tx_minus_px, ty_minus_py, dx, dy):
    x, y = tx_minus_px - dx, ty_minus_py - dy
    if x == 0 and y == 0:
        raise ValueError("Estimated vehicle coincides with target; bearing undefined.")
    truth = math.atan2(ty_minus_py, tx_minus_px)
    return wrap(math.atan2(y, x) - truth)


def normal_interval(lo, hi):
    """Gaussian interval mass, using the survival tail on positive intervals."""
    if lo >= 0:
        return ndtr(-lo) - ndtr(-hi)
    return ndtr(hi) - ndtr(lo)


def wrapped_probability(t, h, tau, inside=True):
    """P(wrap(t + G) belongs to the acceptance arc or its complement)."""
    if tau == 0:
        return float((abs(wrap(t)) <= h) == inside)
    lo, hi = (-h, h) if inside else (h, TURN - h)
    # For |t|<=pi these five images cover all intervals relevant at tau=1 degree.
    return math.fsum(normal_interval((lo + k * TURN - t) / tau,
                                     (hi + k * TURN - t) / tau) for k in range(-2, 3))


def bearing_density(t, kappa):
    """Density of atan2(Y, kappa+X), X,Y iid standard normal; t in radians."""
    u = kappa * math.cos(t)
    base = math.exp(-0.5 * kappa**2) / TURN
    if u < 0:
        # Factor out exp(-kappa²/2) in the rear half-plane.
        return base * (1 + u * math.sqrt(PI / 2) * erfcx(-u / math.sqrt(2)))
    cross = kappa * math.sin(t)
    return base + u * ndtr(u) * math.exp(-0.5 * cross**2) / SQRT2PI


def polar_integral(sigma, R, h, tau, inside=True):
    """Fold the even density over [0,pi] and integrate success/failure directly."""
    if sigma == 0:
        return wrapped_probability(0., h, tau, inside), 0.
    kappa = R / sigma
    lo, hi = (0., PI) if tau else ((0., h) if inside else (h, PI))
    points = {lo, hi}
    # Resolve both a concentrated angular density and the Gaussian-smoothed arc edges.
    angular_scale = math.atan2(sigma, R)
    candidates = [PI / 2, h] + [j * angular_scale for j in (1, 2, 4, 8, 16, 32)]
    candidates += [h + j * tau for j in (-10, -5, -2, -1, 1, 2, 5, 10)]
    points.update(t for t in candidates if lo < t < hi)
    points = sorted(points)

    def integrand(t):
        weight = wrapped_probability(t, h, tau, inside) if tau else 1.
        return 2 * bearing_density(t, kappa) * weight

    values, errors = [], []
    abs_tol = QUAD_ABS
    if not tau and not inside:
        # Resolve the tiny s=0 command failure in relative terms as well.
        abs_tol = min(abs_tol, max(1e-300, ndtr(-kappa * math.sin(h)) * QUAD_REL))
    for left, right in zip(points, points[1:]):
        value, err = quad(integrand, left, right, epsabs=abs_tol / (len(points) - 1),
                          epsrel=QUAD_REL)
        values.append(value)
        errors.append(err)
    return math.fsum(values), math.fsum(errors)


def halfplane_check(sigma, R, h, tau):
    """Independent probability enclosure using two half-planes and Simpson's bound."""
    if sigma == 0:
        p = wrapped_probability(0., h, tau)
        return {"coverage_interval": [max(0., p - FLOAT_ALLOWANCE), min(1., p + FLOAT_ALLOWANCE)],
                "method": "degenerate position; wrapped gimbal probability"}
    kappa = R / sigma
    if not tau:
        marginal_sum = 2 * ndtr(-kappa * math.sin(h))
        rear, tail, quadrature = ndtr(-kappa), 0., 0.
    else:
        L, n = L_NORMAL, N_SIMPSON
        assert L * tau < PI / 2 and 0 < h < PI / 2
        z = np.linspace(-L, L, n + 1)
        step = 2 * L / n
        values = np.exp(-z**2 / 2) / SQRT2PI * ndtr(-kappa * np.sin(h - tau * z))
        marginal_sum = 2 * step / 3 * (values[0] + values[-1] + 4 * values[1:-1:2].sum()
                                       + 2 * values[2:-1:2].sum())
        tail, rear = 2 * ndtr(-L), ndtr(-kappa * math.cos(L * tau))
        # Fourier absolute-moment bounds for |normal_pdf^(j)|, j=0..4.
        B = [2**((j - 1) / 2) * math.gamma((j + 1) / 2) / PI for j in range(5)]
        d1, d2, d3, d4 = (kappa * tau**j for j in range(1, 5))
        F = [1., B[0] * d1, B[1] * d1**2 + B[0] * d2,
             B[2] * d1**3 + 3 * B[1] * d1 * d2 + B[0] * d3,
             B[3] * d1**4 + 6 * B[2] * d1**2 * d2 + B[1] * (3 * d2**2 + 4 * d1 * d3)
             + B[0] * d4]
        fourth = sum(math.comb(4, j) * B[4 - j] * F[j] for j in range(5))
        quadrature = 2 * (2 * L) * step**4 * fourth / 180
    center = 1 - marginal_sum
    return {"coverage_interval": [max(0., center - quadrature - tail - FLOAT_ALLOWANCE),
                                  min(1., center + quadrature + rear + FLOAT_ALLOWANCE)],
            "marginal_failure_sum_truncated": marginal_sum, "simpson_bound": quadrature,
            "omitted_gimbal_tail_bound": tail, "halfplane_overlap_bound": rear}


def direct_atan2_gh(sigma, R, h, tau, order):
    """Tensor Gauss-Hermite on both positioning coordinates, analytic gimbal probability."""
    x, weights = roots_hermitenorm(order)
    theta = wrap(np.arctan2(-sigma * x[:, None], R - sigma * x[None, :]))
    inside = np.zeros_like(theta)
    for k in range(-2, 3):
        lo, hi = (k * TURN - h - theta) / tau, (k * TURN + h - theta) / tau
        inside += np.where(lo > 0, ndtr(-lo) - ndtr(-hi), ndtr(hi) - ndtr(lo))
    return float(weights @ inside @ weights / TURN)


def first_order(sigma, R, h, tau):
    sd = math.hypot(sigma / R, tau)
    return 1. if sd == 0 else 1 - 2 * ndtr(-h / sd)


def solve_case(q, tau):
    R, h = q["range_m"], math.radians(q["tolerance_deg"])
    target = q["coverage"]
    a0, b0, design = q["position_sd_intercept_m"], q["position_sd_slope_m_per_m"], q["design_travel_m"]

    def residual(sigma):
        return polar_integral(sigma, R, h, tau)[0] - target

    sigma_star = brentq(residual, 0., R, xtol=ROOT_ABS_M, rtol=1e-15)
    s_star = (sigma_star - a0) / b0
    z = ndtri((1 + target) / 2)
    linear_sigma = R * math.sqrt((h / z)**2 - tau**2)
    linear_travel = (linear_sigma - a0) / b0

    def evaluate(s):
        sigma = a0 + b0 * s
        p, err = polar_integral(sigma, R, h, tau)
        fail, err_fail = polar_integral(sigma, R, h, tau, False)
        check = halfplane_check(sigma, R, h, tau)
        lower, upper = check["coverage_interval"]
        assert lower <= p + 5e-15 and p - 5e-15 <= upper
        assert abs(p + fail - 1) < 5e-14
        out = {"travel_m": s, "sigma_m": sigma, "coverage": min(1., p), "failure": fail,
               "quad_error_estimate_coverage": err, "quad_error_estimate_failure": err_fail,
               "normalization_residual": p + fail - 1, "linear_coverage": first_order(sigma, R, h, tau),
               "independent_halfplane": check}
        if tau:
            out["direct_atan2_gh_coverage"] = {n: direct_atan2_gh(sigma, R, h, tau, n) for n in (128, 256)}
            assert abs(out["direct_atan2_gh_coverage"][256] - p) < 5e-13
        return out

    bracket = [evaluate(s_star - 1e-7), evaluate(s_star + 1e-7)]
    assert bracket[0]["independent_halfplane"]["coverage_interval"][0] > target
    assert bracket[1]["independent_halfplane"]["coverage_interval"][1] < target
    return {"gimbal_sd_deg": math.degrees(tau), "max_sigma_m": sigma_star, "max_travel_m": s_star,
            "region_at_design": {"a_min_m": 0., "b_min_m_per_m": 0., "b_multiplier_m": design,
                                 "a_plus_design_times_b_max_m": sigma_star, "boundary_inclusive": True,
                                 "b_axis_intercept_m_per_m": sigma_star / design,
                                 "b_max_if_a_original": (sigma_star - a0) / design,
                                 "a_max_if_b_original": sigma_star - design * b0},
            "linearized": {"max_sigma_m": linear_sigma, "max_travel_m": linear_travel,
                           "travel_overestimate_m": linear_travel - s_star,
                           "exact_coverage_at_linear_threshold": evaluate(linear_travel)["coverage"]},
            "at_zero_travel": evaluate(0.), "at_design_travel": evaluate(design), "at_root": evaluate(s_star),
            "root_enclosure": bracket, "travel_0p01m_either_side": [evaluate(s_star - .01), evaluate(s_star + .01)],
            "at_zero_position_sd": polar_integral(0., R, h, tau)[0]}


def variance_budget(q, s):
    sigma = q["position_sd_intercept_m"] + s * q["position_sd_slope_m_per_m"]
    R, h = q["range_m"], math.radians(q["tolerance_deg"])
    terms = {"position": math.degrees(sigma / R)**2,
             "zero_offset": q["zero_sd_deg"]**2, "control": q["control_sd_deg"]**2}
    tau0, tauc = math.radians(q["zero_sd_deg"]), math.radians(q["control_sd_deg"])
    ablations = {"position": polar_integral(0., R, h, math.hypot(tau0, tauc))[0],
                 "zero_offset": polar_integral(sigma, R, h, tauc)[0],
                 "control": polar_integral(sigma, R, h, tau0)[0]}
    return {"travel_m": s, "variance_deg2": terms, "fraction": {k: v / sum(terms.values()) for k, v in terms.items()},
            "exact_coverage_when_named_source_removed": ablations}


def wrap_examples(R):
    examples = []

    def add(name, true_deg, x, y, g_deg, expected_deg):
        truth = math.radians(true_deg)
        cmd_error = command_deviation(R * math.cos(truth), R * math.sin(truth), x, y)
        total = wrap(cmd_error + math.radians(g_deg))
        assert math.isclose(math.degrees(total), expected_deg, abs_tol=1e-9)
        examples.append({"case": name, "true_bearing_deg": true_deg, "dx_m": x, "dy_m": y,
                         "gimbal_error_deg": g_deg, "command_error_deg": math.degrees(cmd_error),
                         "total_error_deg": math.degrees(total)})

    add("estimate_beyond_target", 0., R + 5., 0., 0., -180.)
    add("rear_quadrant", 0., R + 1., -1., 0., 135.)
    add("just_at_transverse_plane", 0., R, -1., 0., 90.)
    add("almost_coincident", 0., R, -1e-12, 0., 90.)
    add("cross_absolute_bearing_cut", 179., 0., 2 * R * math.sin(math.radians(1)), 0., 2.)
    add("gimbal_crosses_cut", 0., R + 1., -math.tan(math.radians(1)), 3., -178.)
    add("full_turn_plus_one_degree", 0., 0., 0., 361., 1.)
    try:
        command_deviation(R, 0., R, 0.)
    except ValueError:
        examples.append({"case": "exactly_coincident", "dx_m": R, "dy_m": 0.,
                         "command_error_deg": None, "probability": 0.})
    else:
        raise AssertionError("Bearing at coincidence must be undefined")
    return examples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "results.json")
    args = parser.parse_args()
    scene = json.loads(SCENE.read_text())
    q = scene["q3"]
    R, h = q["range_m"], math.radians(q["tolerance_deg"])
    tau = math.radians(math.hypot(q["zero_sd_deg"], q["control_sd_deg"]))
    total = solve_case(q, tau)
    command = solve_case(q, 0.)
    stress = []
    for sigma in [R, 10 * R, 1e5 * R]:
        rear = 2 * quad(lambda t: bearing_density(t, R / sigma), PI / 2, PI, epsabs=QUAD_ABS)[0]
        assert abs(rear - ndtr(-R / sigma)) < 3e-15
        stress.append({"sigma_m": sigma, "rear_probability_polar": rear,
                       "rear_probability_cartesian": ndtr(-R / sigma),
                       "total_coverage": polar_integral(sigma, R, h, tau)[0],
                       "command_coverage": polar_integral(sigma, R, h, 0.)[0]})
    results = {"scene": q, "source_pdf": scene["source"], "source_pdf_sha256": scene["sha256"],
               "scene_sha256": hashlib.sha256(SCENE.read_bytes()).hexdigest(),
               "versions": {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__},
               "settings": {"quadrature_epsabs_total": QUAD_ABS, "quadrature_epsrel": QUAD_REL,
                            "brent_xtol_sigma_m": ROOT_ABS_M, "brent_rtol": 1e-15,
                            "gimbal_image_indices": [-2, -1, 0, 1, 2], "simpson_panels": N_SIMPSON,
                            "simpson_normal_truncation": L_NORMAL, "float_allowance_halfplane": FLOAT_ALLOWANCE},
               "total_pointing": total, "command_only": command,
               "first_order_variance_budget": [variance_budget(q, s) for s in (0., total["max_travel_m"], q["design_travel_m"])],
               "position_equals_combined_gimbal_at_travel_m": (R * tau - q["position_sd_intercept_m"]) / q["position_sd_slope_m_per_m"],
               "extreme_sigma_checks": stress, "uniform_direction_coverage_limit": h / PI,
               "angle_examples": wrap_examples(R)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    for name, case in [("total", total), ("command", command)]:
        print(f"{name}: sigma*={case['max_sigma_m']:.12f} m, s*={case['max_travel_m']:.10f} m, "
              f"P(0)={case['at_zero_travel']['coverage']:.12f}, P(80)={case['at_design_travel']['coverage']:.12f}")


if __name__ == "__main__":
    main()
