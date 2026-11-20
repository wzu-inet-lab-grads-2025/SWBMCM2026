"""Recheck the two Q3 mileage limits in a Cartesian frame rotated by the gimbal error."""

import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import brentq
from scipy.special import ndtr, roots_hermitenorm


def mileage_limit(config, gimbal_sd_deg, order):
    nodes, weights = roots_hermitenorm(order)
    weights /= math.sqrt(2 * math.pi)
    noise_angle = math.radians(gimbal_sd_deg) * nodes[:, None]
    mean_x = config['range_m'] * np.cos(noise_angle)
    mean_y = config['range_m'] * np.sin(noise_angle)
    slope = math.tan(math.radians(config['tolerance_deg']))

    def coverage(travel):
        sigma = config['position_sd_intercept_m'] + config['position_sd_slope_m_per_m'] * travel
        x = mean_x + sigma * nodes[None, :]
        edge = x * slope
        # Conditional on x and the rotation, Y is an independent one-dimensional normal.
        mass = ndtr((edge - mean_y) / sigma) - ndtr((-edge - mean_y) / sigma)
        return float(weights @ np.where(x >= 0, mass, 0.) @ weights)

    cap = brentq(lambda travel: coverage(travel) - config['coverage'], 0., config['design_travel_m'], xtol=1e-12)
    return cap, coverage(config['design_travel_m'])


if __name__ == '__main__':
    here = Path(__file__).resolve().parent
    config = json.loads((here / 'scene.json').read_text())['q3']
    reference = json.loads((here / 'q3_quadrature/results.json').read_text())
    total = math.hypot(config['zero_sd_deg'], config['control_sd_deg'])
    for name, tau in [('total_pointing', total), ('command_only', 0.)]:
        for order in (32, 64):
            cap, coverage80 = mileage_limit(config, tau, order)
            if abs(cap - reference[name]['max_travel_m']) > 1e-8 or abs(coverage80 - reference[name]['at_design_travel']['coverage']) > 1e-10:
                raise ValueError(f'{name} disagrees with reference at order {order}')
        print(f'{name}: maximum mileage {cap:.8f} m, coverage at 80 m {coverage80:.12f}')
