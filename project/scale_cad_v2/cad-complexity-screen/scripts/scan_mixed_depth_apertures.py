"""Find thin parts combining slots, round holes and depth-dependent pockets.

A flat plate with many through-holes is not sufficient: the opening pattern
must also change at interior thickness levels (for example counterbores).
"""

import json
from pathlib import Path
from config import WORK

import numpy as np
import trimesh

from flex_probe import loop_metrics
from run_complexity_screen import current_case_source



def section_features(mesh, axis, fraction):
    origin = mesh.bounds[0].copy()
    origin[axis] += mesh.extents[axis] * fraction
    section = mesh.section(plane_origin=origin, plane_normal=np.eye(3)[axis])
    if section is None:
        return []
    result = []
    for path in section.discrete:
        feature = loop_metrics(path, axis)
        if feature is None:
            continue
        uv = np.asarray(path)[:, [i for i in range(3) if i != axis]]
        span = np.ptp(uv, axis=0)
        feature['aspect'] = float(max(span) / max(min(span), 1e-6))
        result.append(feature)
    return sorted(result, key=lambda item: item['area'], reverse=True)


selected = json.loads((WORK / 'selected.json').read_text())
rows = []
examined = 0
for case in selected:
    root, spec = current_case_source(case)
    occurrences = {row['id']: row for row in spec['occurrences']}
    for family in spec['families']:
        representative = occurrences[family['occurrence_ids'][0]]
        metric = representative['mechanical_metrics']
        dims = sorted(family['sorted_size_mm'])
        if not (metric['watertight'] and metric['euler_number'] <= -8
                and dims[0] / dims[1] <= .4):
            continue
        examined += 1
        mesh = trimesh.load_mesh(root / 'aligned' / f"{family['id']}.stl",
                                 process=False)
        axis = int(np.argmin(mesh.extents))
        middle = section_features(mesh, axis, .5)
        if len(middle) < 9:
            continue
        outer_area = middle[0]['area']
        holes = [item for item in middle[1:] if item['area'] < .25 * outer_area]
        if len(holes) < 8:
            continue
        slots = [item for item in holes
                 if item['aspect'] >= 3 and item['circularity'] <= .75]
        rounds = [item for item in holes
                  if item['aspect'] <= 1.3 and item['circularity'] >= .8]
        areas = [item['area'] for item in holes]
        sections = [section_features(mesh, axis, fraction)
                    for fraction in (.2, .5, .8)]
        if any(len(items) < 2 for items in sections):
            continue
        hole_counts = [len(items) - 1 for items in sections]
        hole_areas = [sum(item['area'] for item in items[1:]) for items in sections]
        area_change = max(hole_areas) / max(min(hole_areas), 1e-6)
        count_change = max(hole_counts) - min(hole_counts)
        mixed_slots = (len(slots) >= 2 and len(rounds) >= 4
                       and max(areas) / min(areas) >= 5
                       and (count_change >= 2 or area_change >= 1.15))
        layered_rounds = (len(rounds) >= 8 and count_change >= 4
                          and area_change >= 2)
        if not (mixed_slots or layered_rounds):
            continue
        rows.append({
            'case': case['name'], 'index': case['index'], 'family': family['id'],
            'quantity': family['quantity'], 'euler': metric['euler_number'],
            'through_openings_mid': len(holes), 'elongated_slots_mid': len(slots),
            'round_openings_mid': len(rounds),
            'opening_area_ratio_mid': round(max(areas) / min(areas), 3),
            'hole_counts_by_depth': hole_counts,
            'hole_area_change_ratio': round(area_change, 3),
            'subtype': 'mixed_slots' if mixed_slots else 'layered_rounds',
        })
    print(case['name'], 'done', flush=True)

(WORK / 'mixed_depth_apertures_scan.json').write_text(
    json.dumps(rows, ensure_ascii=False, indent=2))
print('examined', examined, 'hits', len(rows))
for row in rows:
    print(row)
