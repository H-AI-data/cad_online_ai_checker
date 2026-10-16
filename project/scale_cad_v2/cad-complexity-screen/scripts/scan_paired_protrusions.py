"""Find two separated lateral tabs projecting from opposite sides of a body."""

from __future__ import annotations

import json

import numpy as np
from scipy.stats import theilslopes
import trimesh

from config import WORK
from run_complexity_screen import current_case_source


FRACTIONS = np.linspace(.04, .96, 25)


def section_points(mesh: trimesh.Trimesh, axis: int) -> list[tuple[float, np.ndarray]]:
    """Return each cross-section in the two-dimensional lateral plane."""
    rows = []
    lateral = [index for index in range(3) if index != axis]
    for fraction in FRACTIONS:
        origin = mesh.bounds[0].copy()
        origin[axis] += fraction * mesh.extents[axis]
        section = mesh.section(plane_origin=origin, plane_normal=np.eye(3)[axis])
        if section is None or not section.discrete:
            continue
        vertices = np.concatenate(section.discrete)[:, lateral]
        rows.append((float(fraction), vertices))
    return rows


def runs(mask: np.ndarray) -> list[np.ndarray]:
    indices = np.flatnonzero(mask)
    if not len(indices):
        return []
    return np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1)


def local_tab_runs(fractions: np.ndarray, residual: np.ndarray) -> list[dict]:
    """Require a real interval, not one noisy cross-section."""
    tabs = []
    for segment in runs(residual >= .20):
        if len(segment) < 2 or fractions[segment[-1]] - fractions[segment[0]] > .25:
            continue
        peak = float(np.max(residual[segment]))
        if peak < .50:
            continue
        tabs.append({'center': float(np.mean(fractions[segment])),
                     'peak_body_widths': round(peak, 3),
                     'span_fraction': round(float(fractions[segment[-1]]
                                                  - fractions[segment[0]]), 3)})
    return tabs


def paired_protrusions(mesh: trimesh.Trimesh) -> dict | None:
    axis = int(np.argmax(mesh.extents))
    rows = section_points(mesh, axis)
    if len(rows) < 20:
        return None
    fractions = np.array([row[0] for row in rows])
    # A tab direction need not coincide with either aligned lateral axis.
    # Search projective directions in the transverse plane so rotating a
    # part about its main axis does not switch the signal off.
    for angle in range(0, 180, 15):
        theta = np.deg2rad(angle)
        direction = np.array([np.cos(theta), np.sin(theta)])
        envelopes = [points @ direction for _, points in rows]
        lower = np.array([values.min() for values in envelopes])
        upper = np.array([values.max() for values in envelopes])
        body_width = float(np.median(upper - lower))
        if body_width <= 0:
            continue
        lower_fit = theilslopes(lower, fractions)
        upper_fit = theilslopes(upper, fractions)
        negative = (lower_fit.intercept + lower_fit.slope * fractions - lower) / body_width
        positive = (upper - upper_fit.intercept - upper_fit.slope * fractions) / body_width
        negative_tabs = local_tab_runs(fractions, negative)
        positive_tabs = local_tab_runs(fractions, positive)
        for first in negative_tabs:
            for second in positive_tabs:
                # An apparent shoulder at a very end is not a local tab.
                if (.10 <= first['center'] <= .90
                        and .10 <= second['center'] <= .90
                        and abs(first['center'] - second['center']) >= .25):
                    return {'axis': axis, 'lateral_angle_deg': angle,
                            'body_width_mm': round(body_width, 3),
                            'negative_tab': first, 'positive_tab': second}
    return None


def main() -> None:
    cases = json.loads((WORK / 'selected.json').read_text())
    hits = []
    examined = 0
    for case in cases:
        root, spec = current_case_source(case)
        for family in spec['families']:
            sizes = family['sorted_size_mm']
            # Axis selection is unreliable for near-isotropic shapes. This
            # only limits where the longitudinal profile test is applicable;
            # the actual trigger is the pair of off-body protrusions.
            if sizes[-1] / sizes[0] < 1.5 or family['bbox_fill_ratio'] > .12:
                continue
            examined += 1
            mesh = trimesh.load_mesh(root / 'aligned' / f"{family['id']}.stl",
                                     process=False)
            signal = paired_protrusions(mesh)
            if signal:
                hits.append({'case': case['name'], 'family': family['id'],
                             'quantity': family['quantity'], **signal})
        print(case['name'], 'done', flush=True)
    (WORK / 'paired_protrusion_scan.json').write_text(
        json.dumps(hits, ensure_ascii=False, indent=2))
    print('examined', examined, 'paired-protrusion hits', len(hits))
    for row in hits:
        print(row['case'], row['family'], row['negative_tab'], row['positive_tab'])


if __name__ == '__main__':
    main()
