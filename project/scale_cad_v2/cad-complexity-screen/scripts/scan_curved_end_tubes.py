"""Detect a narrow tube that bends sharply near the end of a larger round body.

This is a geometry signal only; it does not infer material or flexibility.
"""

import json
from pathlib import Path
from config import WORK

import numpy as np
import trimesh

from run_complexity_screen import current_case_source
from section_geometry import solid_sections



def end_signal(mesh, axis, reverse):
    cache = {}

    def section(fraction):
        fraction = round(float(fraction), 3)
        if fraction not in cache:
            position = 1 - fraction if reverse else fraction
            cache[fraction] = solid_sections(mesh, axis, position)
        return cache[fraction]

    body = [section(f) for f in (.35, .45, .55, .65)]
    body_areas = [s[0]['area'] for s in body
                  if len(s) == 1 and s[0]['circularity'] >= .9]
    if len(body_areas) < 3:
        return None
    signals = []
    # Slide a three-section straight-tube window toward the end. The bend
    # can begin at different fractions of the overall part length.
    for start in np.arange(.55, .876, .025):
        positions = tuple(round(start + .025 * index, 3) for index in range(3))
        tail = [section(f) for f in positions]
        if any(len(s) != 1 or s[0]['circularity'] < .9 for s in tail):
            continue
        segments = [s[0] for s in tail]
        diameters = np.array([v['diameter'] for v in segments])
        diameter = float(np.median(diameters))
        if max(diameters) / min(diameters) > 1.25:
            continue
        body_tail_area_ratio = float(np.median(body_areas) /
                                     np.median([v['area'] for v in segments]))
        if body_tail_area_ratio < 4 or .05 * mesh.extents[axis] / diameter < .5:
            continue
        centers = np.array([v['center'] for v in segments])
        for terminal_position in (.9, .925, .95, .975):
            if terminal_position <= positions[-1] + .024:
                continue
            terminal = section(terminal_position)
            if len(terminal) != 1 or not .55 <= terminal[0]['circularity'] < .9:
                continue
            terminal_area_ratio = terminal[0]['area'] / np.median(
                [v['area'] for v in segments])
            if not .5 <= terminal_area_ratio <= 4:
                continue
            predicted = np.array([
                np.polyval(np.polyfit(positions, centers[:, i], 1), terminal_position)
                for i in range(2)
            ])
            drift_ratio = float(np.linalg.norm(
                np.array(terminal[0]['center']) - predicted) / diameter)
            if drift_ratio >= .4:
                signals.append({
                    'end': 'min' if reverse else 'max',
                    'tail_diameter_mm': round(diameter, 3),
                    'body_tail_area_ratio': round(body_tail_area_ratio, 3),
                    'straight_window': positions,
                    'bend_section': terminal_position,
                    'terminal_circularity': round(terminal[0]['circularity'], 3),
                    'terminal_center_deviation_diameters': round(drift_ratio, 3),
                })
    return max(signals, key=lambda signal: signal['terminal_center_deviation_diameters'],
               default=None)


def main():
    selected = json.loads((WORK / 'selected.json').read_text())
    rows = []
    examined = 0
    for case in selected:
        root, spec = current_case_source(case)
        occurrence = {r['id']: r for r in spec['occurrences']}
        for family in spec['families']:
            sizes = family['sorted_size_mm']
            aspect = sizes[-1] / sizes[0]
            metric = occurrence[family['occurrence_ids'][0]]['mechanical_metrics']
            if not (metric['watertight'] and aspect >= 2.5):
                continue
            examined += 1
            mesh = trimesh.load_mesh(root / 'aligned' / f"{family['id']}.stl",
                                     process=False)
            axis = int(np.argmax(mesh.extents))
            for reverse in (False, True):
                signal = end_signal(mesh, axis, reverse)
                if signal:
                    rows.append({'case': case['name'], 'family': family['id'],
                                 'quantity': family['quantity'],
                                 'aspect': round(aspect, 3), **signal})
        print(case['name'], 'done', flush=True)
    (WORK / 'curved_end_tubes_scan.json').write_text(
        json.dumps(rows, ensure_ascii=False, indent=2))
    print('examined', examined, 'hits', len(rows))
    for row in rows:
        print(row)


if __name__ == '__main__':
    main()
