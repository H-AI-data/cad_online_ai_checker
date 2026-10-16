"""Select opaque part views from full-resolution, locally aligned meshes."""
from __future__ import annotations

import numpy as np
import trimesh

DEPTH_VOLUME_DEFICIT_MIN = 0.05
DEPTH_COMPLEXITY_RATIO_MIN = 0.10
LOCAL_SPAN_CHANGE_MIN = 0.03
LOCAL_PERIMETER_CHANGE_MIN = 0.05
LOCAL_CHANGE_ABSOLUTE_MM = 0.02
DEPTH_RULE_VERSION = "volume-area-profile-v2-any-axis"


def _axis_profile_change(mesh: trimesh.Trimesh, axis: int) -> dict:
    """Detect varying sections, not semantic STEP fillets/chamfers.

    End-biased samples catch short end features. The second-lowest/highest
    measurements require support from two sections and suppress isolated noise.
    A constant section remains negative along this axis.
    """
    bounds = np.asarray(mesh.bounds, float)
    spans = np.ptp(bounds, axis=0)
    transverse = [i for i in range(3) if i != axis]
    ends = np.array([0.002, 0.005, 0.01, 0.02, 0.035, 0.05])
    fractions = np.unique(np.r_[ends, np.linspace(0.08, 0.92, 33), 1 - ends])
    normal = np.eye(3)[axis]
    profiles = []
    for fraction in fractions:
        origin = bounds.mean(axis=0)
        origin[axis] = bounds[0, axis] + fraction * spans[axis]
        segments = trimesh.intersections.mesh_plane(mesh, normal, origin)
        if len(segments) == 0:
            continue
        points = segments.reshape(-1, 3)[:, transverse]
        section_spans = np.ptp(points, axis=0)
        perimeter = float(np.linalg.norm(segments[:, 1] - segments[:, 0], axis=1).sum())
        profiles.append([float(fraction), *section_spans.tolist(), perimeter])
    if len(profiles) < 4:
        return {"detected": False, "status": "insufficient_sections", "axis": axis,
                "profiles": profiles}
    values = np.asarray(profiles)[:, 1:]
    ordered = np.sort(values, axis=0)
    low, high = ordered[1], ordered[-2]
    delta = high - low
    relative = delta / np.maximum(high, 1e-9)
    width_changed = bool(np.any(
        (relative[:2] > LOCAL_SPAN_CHANGE_MIN)
        & (delta[:2] > LOCAL_CHANGE_ABSOLUTE_MM)
    ))
    perimeter_changed = bool(
        relative[2] > LOCAL_PERIMETER_CHANGE_MIN
        and delta[2] > LOCAL_CHANGE_ABSOLUTE_MM
    )
    return {
        "detected": width_changed or perimeter_changed,
        "status": "ok",
        "axis": axis,
        "span_change_ratios": relative[:2].tolist(),
        "perimeter_change_ratio": float(relative[2]),
        "profiles": profiles,
    }


def local_shape_change(mesh: trimesh.Trimesh) -> dict:
    """Prefer recall: variation along any exterior axis requests opaque views.

    A constant thickness section must not veto holes seen along another axis.
    Some simple curved primitives will also qualify; this is intentional.
    """
    profiles = []
    for axis in np.argsort(-mesh.extents):
        result = _axis_profile_change(mesh, int(axis))
        profiles.append(result)
        if result["status"] == "ok" and result["detected"]:
            return {**result, "tested_axis_count": len(profiles)}
    valid = [item for item in profiles if item["status"] == "ok"]
    if not valid:
        return {**profiles[0], "tested_axis_count": len(profiles)}
    best = max(valid, key=lambda item: max(
        *item["span_change_ratios"], item["perimeter_change_ratio"]
    ))
    return {**best, "tested_axis_count": len(profiles)}


def depth_view_gate(volume_deficit: float | None, area_gap: float,
                    local_change: bool) -> tuple[bool, list[str]]:
    reasons = []
    if volume_deficit is not None and volume_deficit > DEPTH_VOLUME_DEFICIT_MIN:
        reasons.append("convex_hull_volume_deficit")
    if area_gap > DEPTH_COMPLEXITY_RATIO_MIN:
        reasons.append("surface_hull_area_gap")
    if local_change:
        reasons.append("local_shape_change")
    return bool(reasons), reasons


def depth_view_features(mesh: trimesh.Trimesh) -> dict:
    """Compute selection before display simplification; never scale geometry."""
    hull = mesh.convex_hull
    area, hull_area = float(mesh.area), float(hull.area)
    area_gap = max(area - hull_area, 0.0) / max(hull_area, 1e-9)
    volume_valid = bool(mesh.is_volume and hull.volume > 1e-9)
    volume = float(abs(mesh.volume)) if volume_valid else None
    volume_deficit = max(0.0, 1.0 - volume / hull.volume) if volume_valid else None
    local = local_shape_change(mesh)
    show, reasons = depth_view_gate(volume_deficit, area_gap, local["detected"])
    return {
        "depth_rule_version": DEPTH_RULE_VERSION,
        "surface_area_mm2": area,
        "convex_hull_area_mm2": hull_area,
        "surface_hull_area_gap_ratio": area_gap,
        "volume_mm3": volume,
        "convex_hull_volume_mm3": float(hull.volume),
        "convex_hull_volume_deficit_ratio": volume_deficit,
        "volume_metric_valid": volume_valid,
        "local_shape_change": local,
        "show_depth_views": show,
        "depth_view_reasons": reasons,
    }
