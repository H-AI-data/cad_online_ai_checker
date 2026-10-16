"""Detect dense, repeated teeth on a compact circular part's outer rim.

The rule uses two actual mesh sections, not page line density or family IDs.
An ordinary turned ring has a nearly constant radial profile and stays out.
"""

from __future__ import annotations

import json

import numpy as np
from scipy.signal import find_peaks
import trimesh


SAMPLES = 720


def section_tooth_metrics(mesh, axis: int, fraction: float):
    origin = mesh.bounds[0].copy()
    origin[axis] += mesh.extents[axis] * fraction
    section = mesh.section(plane_origin=origin, plane_normal=np.eye(3)[axis])
    if section is None:
        return None
    planar = [index for index in range(3) if index != axis]
    center = np.mean(mesh.bounds[:, planar], axis=0)
    candidates = []
    for path in section.discrete:
        points = np.asarray(path)[:, planar]
        if len(points) < 100 or np.linalg.norm(points[0] - points[-1]) > .05:
            continue
        delta = points - center
        radius = np.linalg.norm(delta, axis=1)
        angle = np.mod(np.arctan2(delta[:, 1], delta[:, 0]), 2 * np.pi)
        indices = np.floor(angle * SAMPLES / (2 * np.pi)).astype(int) % SAMPLES
        occupied = np.unique(indices)
        if len(occupied) < .7 * SAMPLES:
            continue
        radial = np.full(SAMPLES, -np.inf)
        np.maximum.at(radial, indices, radius)
        known = np.flatnonzero(radial > -np.inf)
        extended_index = np.r_[known[-1] - SAMPLES, known, known[0] + SAMPLES]
        extended_radius = np.r_[radial[known[-1]], radial[known], radial[known[0]]]
        radial = np.interp(np.arange(SAMPLES), extended_index, extended_radius)
        radial = sum(np.roll(radial, shift) for shift in range(-2, 3)) / 5
        median_radius = float(np.median(radial))
        if median_radius <= 0:
            continue
        depth = float(np.percentile(radial, 95) - np.percentile(radial, 5))
        prominence = max(.15, .015 * median_radius)
        peaks, properties = find_peaks(np.tile(radial, 3), prominence=prominence,
                                      distance=8)
        central = (peaks >= SAMPLES) & (peaks < 2 * SAMPLES)
        peaks = peaks[central] - SAMPLES
        prominences = properties["prominences"][central]
        if len(peaks) < 2:
            continue
        pitches = np.diff(np.r_[peaks, peaks[0] + SAMPLES])
        candidates.append({
            "peak_count": int(len(peaks)),
            "pitch_cv": float(np.std(pitches) / np.mean(pitches)),
            "relative_depth": depth / median_radius,
            "relative_prominence": float(np.median(prominences) / median_radius),
            "mean_radius": median_radius,
        })
    return max(candidates, key=lambda item: item["mean_radius"], default=None)


def periodic_radial_profile(mesh):
    extents = np.asarray(mesh.extents)
    axis = int(np.argmin(extents))
    planar = sorted(extents[index] for index in range(3) if index != axis)
    if planar[1] / planar[0] > 1.2 or extents[axis] / planar[0] > .8:
        return None
    sections = [section_tooth_metrics(mesh, axis, fraction)
                for fraction in (.35, .65)]
    if any(item is None for item in sections):
        return None
    if not all(12 <= item["peak_count"] <= 80
               and item["pitch_cv"] <= .22
               and .03 <= item["relative_depth"] <= .25
               and item["relative_prominence"] >= .02
               for item in sections):
        return None
    if abs(sections[0]["peak_count"] - sections[1]["peak_count"]) > 1:
        return None
    return {"axis": axis, "sections": sections}


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
            metrics = periodic_radial_profile(mesh)
            if metrics:
                rows.append({"case": case["name"], "family": family["id"],
                             **metrics})
        print(case["name"], "periodic radial scan done", flush=True)
    (WORK / "periodic_radial_profile_scan.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2))
    print("periodic radial profile hits", len(rows), flush=True)


if __name__ == "__main__":
    main()
