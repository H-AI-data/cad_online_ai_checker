"""One-pass screens for difficult longitudinal member and tube geometry.

Uses the family spec that exactly matches each current Harbor parts.pdf.
The four signals are independent and may coexist on one family.
"""

import json
from pathlib import Path
from config import WORK

import numpy as np
import trimesh

from flex_probe import sections as section_profile
from run_complexity_screen import current_case_source
from scan_curved_end_tubes import end_signal
from section_geometry import solid_sections

END_FRACTIONS = (.005, .01, .02, .03, .05, .08, .1, .15, .2, .25)


def end_blocks(mesh, axis, middle_area, max_fraction, min_ratio):
    blocks = []
    for reverse in (False, True):
        samples = []
        for fraction in END_FRACTIONS:
            if fraction > max_fraction:
                continue
            position = 1 - fraction if reverse else fraction
            section = solid_sections(mesh, axis, position)
            if len(section) == 1:
                samples.append((position, section[0]))
        if not samples:
            return None
        blocks.append(max(samples, key=lambda row: row[1]['area']))
    ratio = min(item['area'] for _, item in blocks) / middle_area
    if ratio < min_ratio:
        return None
    return {'end_positions': [round(float(position), 3) for position, _ in blocks],
            'end_to_middle_area_ratio': round(float(ratio), 3)}


def paired_members(mesh, axis, middle):
    if any(len(section) != 2 for section in middle):
        return None
    for section in middle:
        circularities = [item['circularity'] for item in section]
        areas = [item['area'] for item in section]
        if not (max(circularities) >= .88 and min(circularities) <= .55
                and min(areas) / sum(areas) >= .12):
            return None
    middle_area = float(np.median([sum(item['area'] for item in section)
                                   for section in middle]))
    return end_blocks(mesh, axis, middle_area, .1, 1.7)


def multiple_rods(mesh, axis, middle):
    for section in middle:
        if not 4 <= len(section) <= 10:
            return None
        main, rods = section[0], section[1:]
        areas = [item['area'] for item in section]
        if not (main['circularity'] >= .88 and main['area'] / sum(areas) >= .55
                and all(item['circularity'] >= .85 for item in rods)
                and max(item['area'] for item in rods) /
                min(item['area'] for item in rods) <= 1.5):
            return None
    middle_area = float(np.median([sum(item['area'] for item in section)
                                   for section in middle]))
    block = end_blocks(mesh, axis, middle_area, .25, 1.3)
    if block:
        block['member_count'] = len(middle[1])
    return block


def gradual_tail(samples, extent, reverse):
    ordered = samples[::-1] if reverse else samples
    for count in (7, 5):
        end = ordered[-count:]
        if len(end) != count or any(len(loops) != 1 for _, loops in end):
            continue
        metrics = [loops[0] for _, loops in end]
        diameters = np.asarray([item['diameter'] for item in metrics])
        diameter = float(np.median(diameters))
        circularities = [item['circularity'] for item in metrics]
        if (diameter <= 0 or min(circularities) < .9
                or max(diameters) / min(diameters) > 1.2):
            continue
        span = .05 * (count - 1) * extent
        if span / diameter < 1.5:
            continue
        opposite = ordered[:-count]
        body_area = max((loops[0]['area'] for _, loops in opposite if loops),
                        default=0)
        area_ratio = body_area / np.median([item['area'] for item in metrics])
        if area_ratio < 4:
            continue
        centers = np.asarray([item['center'] for item in metrics])
        x = np.arange(count)
        predicted = np.stack([
            np.polyval(np.polyfit(x, centers[:, i], 1), x)
            for i in range(2)
        ], axis=1)
        drift = float(np.linalg.norm(centers[-1] - centers[0])) / diameter
        curvature = float(np.sqrt(np.mean(np.sum((centers - predicted) ** 2,
                                                   axis=1)))) / diameter
        if drift >= .1 and curvature >= .008:
            return {'end': 'min' if reverse else 'max',
                    'sample_count': count,
                    'tail_diameter_mm': round(diameter, 3),
                    'body_tail_area_ratio': round(float(area_ratio), 3),
                    'drift_diameters': round(drift, 3),
                    'curvature_diameters': round(curvature, 4)}
    return None


def main():
    cases = json.loads((WORK / 'selected.json').read_text())
    rows = []
    examined = 0
    total = 0
    for case in cases:
        root, spec = current_case_source(case)
        occurrences = {item['id']: item for item in spec['occurrences']}
        for family in spec['families']:
            total += 1
            sizes = family['sorted_size_mm']
            aspect = sizes[-1] / sizes[0]
            metric = occurrences[family['occurrence_ids'][0]]['mechanical_metrics']
            # Imported surface bodies can have imperfect watertight flags;
            # coherent cross-sections remain useful high-recall evidence.
            if aspect < 2:
                continue
            examined += 1
            mesh = trimesh.load_mesh(root / 'aligned' / f"{family['id']}.stl",
                                     process=False)
            axis = int(np.argmax(mesh.extents))
            signals = {}
            if aspect >= 3 and (not metric['watertight']
                                or metric['euler_number'] <= 0):
                middle = [solid_sections(mesh, axis, fraction)
                          for fraction in (.3, .5, .7)]
                paired = paired_members(mesh, axis, middle)
                if paired:
                    signals['parallel_members'] = paired
            else:
                middle = None
            if aspect >= 2.5:
                if middle is None:
                    middle = [solid_sections(mesh, axis, fraction)
                              for fraction in (.3, .5, .7)]
                rods = multiple_rods(mesh, axis, middle)
                if rods:
                    signals['multi_rod_end_blocks'] = rods
                for reverse in (False, True):
                    bend = end_signal(mesh, axis, reverse)
                    if bend:
                        signals['curved_end_tube'] = bend
                        break
            _, profile_samples = section_profile(mesh)
            for reverse in (False, True):
                gradual = gradual_tail(profile_samples, mesh.extents[axis], reverse)
                if gradual:
                    signals['flexible_tail'] = gradual
                    break
            if signals:
                rows.append({'case': case['name'], 'family': family['id'],
                             'quantity': family['quantity'],
                             'aspect': round(aspect, 3), 'signals': signals})
        print(case['name'], 'done', flush=True)
    (WORK / 'longitudinal_geometry_scan.json').write_text(
        json.dumps(rows, ensure_ascii=False, indent=2))
    print('families', total, 'examined', examined, 'hits', len(rows))
    for row in rows:
        print(row['case'], row['family'], list(row['signals']))


if __name__ == '__main__':
    main()
