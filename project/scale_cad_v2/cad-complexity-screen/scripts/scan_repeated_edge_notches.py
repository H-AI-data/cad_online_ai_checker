"""Find repeated small notches on the inside edge of an elongated closed strip.

Such features need a pitch, depth, width and end-location scheme in addition to
the strip envelope.  The rule uses two actual mesh sections and does not depend
on the drawing page, case name or family number.
"""

from __future__ import annotations

import json

import numpy as np
from scipy.signal import find_peaks
import trimesh


def _closed_loops(section, planar):
    loops = []
    for path in section.discrete:
        points = np.asarray(path, dtype=float)[:, planar]
        if len(points) < 8 or np.linalg.norm(points[0] - points[-1]) > .1:
            continue
        area = .5 * abs(np.dot(points[:, 0], np.roll(points[:, 1], -1))
                          - np.dot(points[:, 1], np.roll(points[:, 0], -1)))
        if area > 0:
            loops.append((area, points))
    return sorted(loops, key=lambda item: item[0], reverse=True)


def _edge_notches(points, side):
    low, high = points.min(axis=0), points.max(axis=0)
    length, width = high - low
    if min(length, width) <= 0:
        return None
    near_end = ((points[:, 0] > low[0] + .1 * length)
                & (points[:, 0] < high[0] - .1 * length))
    if side == "upper":
        near_edge = points[:, 1] > low[1] + .72 * width
    else:
        near_edge = points[:, 1] < low[1] + .28 * width
    edge = points[near_end & near_edge]
    if len(edge) < 12:
        return None
    xs = np.round(edge[:, 0], 4)
    positions = np.unique(xs)
    if len(positions) < 12:
        return None
    values = np.array([np.max(edge[xs == x, 1]) if side == "upper"
                       else np.min(edge[xs == x, 1]) for x in positions])
    step = max(.05, min(.25, length / 1400))
    grid = np.arange(positions[0], positions[-1], step)
    if len(grid) < 20:
        return None
    samples = np.interp(grid, positions, values)
    baseline = np.percentile(samples, 90 if side == "upper" else 10)
    depth = baseline - samples if side == "upper" else samples - baseline
    minimum_depth = max(.6, .025 * width)
    peaks, properties = find_peaks(
        depth, prominence=minimum_depth,
        distance=max(1, int(max(1., .025 * width) / step)),
    )
    if not 4 <= len(peaks) <= 30:
        return None
    centers = grid[peaks]
    pitches = np.diff(centers)
    depths = depth[peaks]
    if (np.std(pitches) / np.mean(pitches) > .15
            or np.std(depths) / np.mean(depths) > .2
            or np.median(depths) > .18 * width
            or np.median(properties["prominences"]) < minimum_depth):
        return None
    return {"side": side, "count": len(peaks),
            "centers": centers, "pitch": float(np.median(pitches)),
            "depth": float(np.median(depths))}


def _section_metrics(mesh, thin_axis, long_axis, width_axis, fraction):
    origin = mesh.bounds[0].copy()
    origin[thin_axis] += mesh.extents[thin_axis] * fraction
    section = mesh.section(plane_origin=origin,
                           plane_normal=np.eye(3)[thin_axis])
    if section is None:
        return None
    loops = _closed_loops(section, [long_axis, width_axis])
    if len(loops) < 2:
        return None
    outer_area, outer = loops[0]
    outer_low, outer_high = outer.min(axis=0), outer.max(axis=0)
    outer_span = outer_high - outer_low
    for inner_area, inner in loops[1:]:
        inner_low, inner_high = inner.min(axis=0), inner.max(axis=0)
        inner_span = inner_high - inner_low
        if (inner_area / outer_area < .6
                or np.any(inner_low < outer_low - .1)
                or np.any(inner_high > outer_high + .1)
                or np.any(inner_span / outer_span < .7)):
            continue
        for side in ("upper", "lower"):
            result = _edge_notches(inner, side)
            if result:
                return result
    return None


def repeated_inner_edge_notches(mesh):
    extents = np.asarray(mesh.extents)
    thin_axis, width_axis, long_axis = np.argsort(extents)
    if (extents[long_axis] / extents[width_axis] < 2.5
            or extents[thin_axis] / extents[width_axis] > .5):
        return None
    sections = [_section_metrics(mesh, thin_axis, long_axis, width_axis, fraction)
                for fraction in (.35, .65)]
    first, second = sections
    if first is None or second is None:
        return None
    if (first["side"] != second["side"]
            or first["count"] != second["count"]
            or np.max(abs(first["centers"] - second["centers"])) >
            max(.35, .08 * first["pitch"])):
        return None
    return {"thin_axis": int(thin_axis), "long_axis": int(long_axis),
            "side": first["side"], "notch_count": first["count"],
            "pitch_mm": round(first["pitch"], 3),
            "depth_mm": round(first["depth"], 3)}


def main():
    from config import WORK
    from run_complexity_screen import current_case_source

    cases = json.loads((WORK / "selected.json").read_text())
    rows = []
    for case in cases:
        root, spec = current_case_source(case)
        for family in spec["families"]:
            mesh = trimesh.load_mesh(root / "aligned" / f"{family['id']}.stl",
                                     process=False)
            metrics = repeated_inner_edge_notches(mesh)
            if metrics:
                rows.append({"case": case["name"], "family": family["id"],
                             **metrics})
        print(case["name"], "repeated inner-edge notches scan done", flush=True)
    (WORK / "repeated_edge_notch_scan.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2))
    print("repeated inner-edge notch hits", len(rows), flush=True)


if __name__ == "__main__":
    main()
