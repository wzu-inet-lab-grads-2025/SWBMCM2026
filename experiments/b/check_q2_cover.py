"""Recheck the analytic reuse-chain cover with independent box angle and visibility bounds."""

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / 'q2_analytic'))
import certificate


def box_corners(lo, hi):
    return [(lo[:, 0], lo[:, 1]), (lo[:, 0], hi[:, 1]),
            (hi[:, 0], lo[:, 1]), (hi[:, 0], hi[:, 1])]


def slew_bound(lo, hi, target):
    x, y, height = certificate.TGT[target]
    # Pad the box to include the input-coordinate rounding uncertainty.
    lo, hi = lo - 1e-10, hi + 1e-10
    angles, distances = [], []
    for sx, sy in box_corners(lo, hi):
        angles.append(np.abs(np.arctan2(y - sy, x - sx)))
        distances.append(np.hypot(x - sx, y - sy))
    # Minimum absolute bearing lies on a corner unless the box meets the
    # westward ray from the target, where a due-east bearing is possible.
    east_visible = (lo[:, 0] <= x) & (lo[:, 1] <= y) & (hi[:, 1] >= y)
    yaw = np.where(east_visible, 0, np.min(angles, axis=0))
    tilt = np.arctan2(height - certificate.H0, np.max(distances, axis=0))
    return np.maximum(0., np.rad2deg(np.maximum(yaw, tilt)) / certificate.SCENE['axis_speed_deg_s'] - 1e-10)


def ray_hits_inset(start_x, start_y, target, box):
    tx, ty, h = certificate.TGT[target]
    w, s, e, n, roof = box
    pad = 1e-7
    # Intersect the ray with the rectangle before it crosses the inset roof.
    entry = np.zeros_like(start_x)
    leave = np.full_like(start_x, (roof - pad - certificate.H0) / (h - certificate.H0))
    exists = np.ones(len(start_x), dtype=bool)
    for source, dest, lower, upper in [(start_x, tx, w + pad, e - pad), (start_y, ty, s + pad, n - pad)]:
        delta = dest - source
        parallel = delta == 0
        exists &= ~parallel | ((source >= lower) & (source <= upper))
        denominator = np.where(parallel, 1., delta)
        a, b = (lower - source) / denominator, (upper - source) / denominator
        entry = np.maximum(entry, np.where(parallel, 0., np.minimum(a, b)))
        leave = np.minimum(leave, np.where(parallel, 1., np.maximum(a, b)))
    return exists & (entry + 1e-12 < leave)


def whole_box_hidden(lo, hi, target):
    points = box_corners(lo, hi)
    hidden = np.zeros(len(lo), dtype=bool)
    for building in certificate.BLD.values():
        hits = np.ones(len(lo), dtype=bool)
        for x, y in points:
            hits &= ray_hits_inset(x, y, target, building)
        hidden |= hits
    return hidden


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--goal', type=float, default=certificate.GOAL, help='Time to exclude, in seconds')
    args = parser.parse_args()
    with patch.object(certificate, 'slew_lower', slew_bound), patch.object(certificate, 'blocked', whole_box_hidden):
        result = certificate.run(args.goal)
    counts = list(result['companion_covers'].values()) + list(result['reuse_covers'].values())
    for c in counts:
        leaves = c['time_pruned'] + c['shadow_pruned']
        if c['tested'] != 2 * leaves - 1:
            raise ValueError('Incomplete binary box cover')
    print(f"{len(counts)} covered roots, {sum(c['tested'] for c in counts)} boxes; lower bound {result['lower_bound_s']:.9f} s")
