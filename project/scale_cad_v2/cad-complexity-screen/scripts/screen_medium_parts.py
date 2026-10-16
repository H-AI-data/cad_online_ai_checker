"""Find parts likely to need richer dimension sheets after the high-complexity set.

Consumes the same PDF-matched family spec, STEP-derived circle cache and aligned
meshes as the existing complexity screen. This is a candidate screen, not a
claim that the resulting drawings are already dimension-complete.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path
import re
import statistics
import time

import numpy as np
from scipy.spatial import ConvexHull, QhullError
import trimesh

from profile_probe import closed_rdp


def circle_summary(circles: list[dict]) -> dict:
    """Collapse duplicate STEP rims and opposite faces onto one axis/center."""
    unique = set()
    centers = set()
    diameters = set()
    axes = set()
    for circle in circles:
        radius = float(circle["radius"])
        if radius < 1.5:  # matches the existing PDF's 3-mm diameter floor
            continue
        axis = np.asarray(circle["axis"], dtype=float)
        length = float(np.linalg.norm(axis))
        if length < 0.5:
            continue
        axis /= length
        first = int(np.argmax(np.abs(axis)))
        if axis[first] < 0:
            axis = -axis
        center = np.asarray(circle["center"], dtype=float)
        foot = center - float(center @ axis) * axis
        axis_key = tuple(np.round(axis, 2))
        foot_key = tuple(np.round(foot, 1))
        diameter = round(2 * radius, 1)
        centers.add((axis_key, foot_key))
        unique.add((axis_key, foot_key, diameter))
        diameters.add(diameter)
        axes.add(axis_key)
    return {
        "circle_features": len(unique),
        "circle_centers": len(centers),
        "diameter_types": len(diameters),
        "axis_directions": len(axes),
    }


def analytic_circles(part: dict) -> list[dict]:
    """Use the same STEP arc filter as the existing annotation preparation."""
    totals = defaultdict(float)
    for circle in part["circles"]:
        key = tuple(np.round([*circle["center"], circle["radius"],
                              *circle["axis"]], 4))
        totals[key] += circle.get(
            "arc_span", 2 * math.pi if circle.get("full_circle") else 0)
    return [circle for circle in part["circles"]
            if circle.get("cylindrical")
            and totals[tuple(np.round([*circle["center"], circle["radius"],
                                       *circle["axis"]], 4))] >= math.pi - 0.01]


def coaxial_stepped_axis_tilt(circles: list[dict]) -> float | None:
    """Detect several diameter changes along one axis, independent of family ID.

    Four distinct STEP radii on a common centerline, at separated axial
    stations, call for an axial dimension chain rather than sparse Ø labels.
    """
    if len(circles) < 4:
        return None
    parsed = []
    for circle in circles:
        axis = np.asarray(circle["axis"], float)
        length = np.linalg.norm(axis)
        if length < .5:
            continue
        axis /= length
        parsed.append((np.asarray(circle["center"], float), axis,
                       round(2 * float(circle["radius"]), 1)))
    if not parsed:
        return None
    for _, reference, _ in parsed:
        aligned = [(center, diameter) for center, axis, diameter in parsed
                   if abs(np.dot(axis, reference)) >= .985]
        if len({diameter for _, diameter in aligned}) < 4:
            continue
        feet = np.asarray([center - np.dot(center, reference) * reference
                           for center, _ in aligned])
        if np.max(np.linalg.norm(feet - feet[0], axis=1)) > .35:
            continue
        stations = {round(float(np.dot(center, reference)), 1)
                    for center, _ in aligned}
        if len(stations) >= 4 and max(stations) - min(stations) >= 10:
            return math.degrees(math.acos(min(1., float(np.max(np.abs(reference))))))
    return None


def coaxial_stepped_circles(circles: list[dict]) -> bool:
    return coaxial_stepped_axis_tilt(circles) is not None


def two_stage_coaxial_axis_tilt(circles: list[dict]) -> float | None:
    """Detect two adjacent, distinct-diameter axial stages on one centerline.

    A face-on pair of diameters leaves both depths undefined; those parts
    need a longitudinal dimension page even when the shape is otherwise small.
    """
    grouped = defaultdict(set)
    axes = {}
    for circle in circles:
        axis = np.asarray(circle["axis"], float)
        norm = float(np.linalg.norm(axis))
        if norm < .5 or float(circle["radius"]) < 1.5:
            continue
        axis /= norm
        major = int(np.argmax(np.abs(axis)))
        if axis[major] < 0:
            axis = -axis
        center = np.asarray(circle["center"], float)
        foot = center - float(np.dot(center, axis)) * axis
        key = (tuple(np.round(axis, 2)), tuple(np.round(foot, 1)),
               round(2 * float(circle["radius"]), 1))
        grouped[key].add(round(float(np.dot(center, axis)), 2))
        axes[key] = axis
    items = list(grouped.items())
    for index, (first, first_levels) in enumerate(items):
        if len(first_levels) < 2:
            continue
        a0, a1 = min(first_levels), max(first_levels)
        if a1 - a0 < 2.:
            continue
        for second, second_levels in items[index + 1:]:
            if first[:2] != second[:2] or len(second_levels) < 2:
                continue
            if abs(first[2] - second[2]) < max(1., .05 * max(first[2], second[2])):
                continue
            b0, b1 = min(second_levels), max(second_levels)
            if (b1 - b0 >= 2. and
                    (abs(a1 - b0) <= .15 or abs(b1 - a0) <= .15)):
                axis = axes[first]
                return math.degrees(math.acos(min(1., float(np.max(np.abs(axis))))))
    return None


def compound_head_axis_tilt(circles: list[dict], dimensions: list[float]) -> float | None:
    """Find a long, common-axis shaft carrying a larger non-cylindrical head.

    The STEP circle inventory often contains only the collar and shaft rims;
    a lobed or knurled head has no analytic circular edge.  Require one
    centerline, two cylindrical diameters, three axial stations and a wider
    transverse envelope so unrelated hole patterns cannot trigger this rule.
    """
    if len(circles) < 4:
        return None
    axis = np.asarray(circles[0]["axis"], float)
    axis /= np.linalg.norm(axis)
    major = int(np.argmax(np.abs(axis)))
    if abs(axis[major]) < .998:
        return None
    centers = np.asarray([circle["center"] for circle in circles], float)
    for circle in circles:
        other = np.asarray(circle["axis"], float)
        other /= np.linalg.norm(other)
        if abs(np.dot(axis, other)) < .995:
            return None
    transverse = [index for index in range(3) if index != major]
    if np.max(np.ptp(centers[:, transverse], axis=0)) > .35:
        return None
    diameters = sorted({round(2 * float(c["radius"]), 1) for c in circles})
    stations = sorted({round(float(c["center"][major]), 1) for c in circles})
    dims = np.asarray(dimensions, float)
    if len(diameters) != 2 or len(stations) < 3:
        return None
    if (dims[major] < 1.3 * max(dims[transverse]) or
            max(dims[transverse]) < 1.6 * diameters[-1] or
            stations[-1] - stations[0] < diameters[-1]):
        return None
    return math.degrees(math.acos(min(1., abs(float(axis[major])))))


def recessed_head_drive(mesh_path: Path, circles: list[dict],
                        dimensions: list[float]) -> dict | None:
    """Find a non-round drive recess inside a coaxial fastener head.

    Analytic circles describe the outside head and shaft but miss cross,
    polygonal and lobed sockets.  A transverse mesh section must show an
    internal loop with strongly varying radius, separate from the round rim.
    """
    if len(circles) < 4:
        return None
    axes = np.asarray([circle["axis"] for circle in circles], float)
    axes /= np.linalg.norm(axes, axis=1)[:, None]
    axis = axes[0]
    major = int(np.argmax(np.abs(axis)))
    if abs(axis[major]) < .998 or np.min(np.abs(axes @ axis)) < .995:
        return None
    transverse = [index for index in range(3) if index != major]
    centers = np.asarray([circle["center"] for circle in circles], float)
    if np.max(np.ptp(centers[:, transverse], axis=0)) > .35:
        return None
    diameters = sorted({round(2 * float(c["radius"]), 1) for c in circles})
    stations = {round(float(c["center"][major]), 2) for c in circles}
    dims = np.asarray(dimensions, float)
    if len(diameters) != 2 or len(stations) < 3:
        return None
    head_diameter = diameters[-1]
    if not .95 < max(dims[transverse]) / head_diameter < 1.1:
        return None
    if dims[major] > 4 * head_diameter:
        return None
    # Diameters above are rounded to one decimal place.  Select head circles
    # with that same precision so a radius on the half-step boundary is not
    # accidentally dropped before locating the head end.
    head_stations = [float(c["center"][major]) for c in circles
                     if round(2 * float(c["radius"]), 1) == head_diameter]
    if not head_stations:
        return None
    head_end = 0. if min(head_stations) < dims[major] - max(head_stations) else dims[major]
    station = head_end + (.15 if head_end == 0 else -.15) * head_diameter
    if not .05 < station < dims[major] - .05:
        return None
    mesh = trimesh.load_mesh(mesh_path, process=False)
    if not isinstance(mesh, trimesh.Trimesh):
        return None
    origin = np.median(mesh.vertices, axis=0)
    origin[major] = station
    section = mesh.section(plane_origin=origin, plane_normal=np.eye(3)[major])
    if section is None:
        return None
    center = np.median(centers[:, transverse], axis=0)
    candidates = []
    for loop in section.discrete:
        radii = np.linalg.norm(np.asarray(loop)[:, transverse] - center, axis=1)
        if (len(radii) >= 20 and radii.min() > .05 and
                .3 * head_diameter / 2 < radii.max() < .8 * head_diameter / 2 and
                radii.max() / radii.min() >= 1.6):
            candidates.append((float(radii.max() / radii.min()),
                               float(radii.max()), float(radii.min())))
    if not candidates:
        return None
    ratio, maximum, minimum = max(candidates)
    return {"axis": major, "section_station": round(float(station), 3),
            "drive_outer_span": round(2 * maximum, 2),
            "drive_inner_span": round(2 * minimum, 2),
            "radial_ratio": round(ratio, 2)}


def pdf_pages(path: Path) -> dict[str, int]:
    import fitz

    result = {}
    with fitz.open(path) as document:
        for page in document:
            if page.number == 0:
                continue
            page_text = page.get_text()
            if page_text.startswith("FAMILY INDEX"):
                continue
            for fid in set(re.findall(r"\bFAMILY_\d+\b", page_text)):
                key = fid.lower()
                if key in result:
                    raise ValueError(f"Family occurs on multiple PDF pages: {key}")
                result[key] = page.number + 1
    return result


def polygon_summary(points: np.ndarray) -> dict | None:
    if len(points) < 3:
        return None
    following = np.roll(points, -1, axis=0)
    area = float(np.sum(points[:, 0] * following[:, 1]
                        - points[:, 1] * following[:, 0]) / 2)
    if abs(area) < 1e-5:
        return None
    try:
        hull = ConvexHull(points)
    except QhullError:
        return None
    previous = np.roll(points, 1, axis=0)
    incoming = points - previous
    outgoing = following - points
    cross = incoming[:, 0] * outgoing[:, 1] - incoming[:, 1] * outgoing[:, 0]
    dot = np.einsum("ij,ij->i", incoming, outgoing)
    turn = np.arctan2(cross, dot) * math.copysign(1, area)
    perimeter = float(np.linalg.norm(outgoing, axis=1).sum())
    return {
        "area": abs(area),
        "corners": len(points),
        "reflex": int(np.count_nonzero(turn < -0.45)),
        "sharp_turns": int(np.count_nonzero(np.abs(turn) > 0.6)),
        "turn_excess": max(0.0, float(np.abs(turn).sum() - 2 * math.pi)),
        "perimeter_ratio": perimeter / float(hull.area),
        "area_deficit": max(0.0, 1 - abs(area) / float(hull.volume)),
    }


def thin_outline_summary(mesh_path: Path, dimensions: list[float]) -> dict:
    # Moderately thin formed parts also have informative transverse slices.
    # The former 0.45 gate skipped bent brackets and slotted profiles whose
    # thickness was just under the next-smallest span.
    if dimensions[0] / max(dimensions[1], 1e-9) > 0.60:
        return {"sampled": False}
    mesh = trimesh.load_mesh(mesh_path, process=False)
    if not isinstance(mesh, trimesh.Trimesh):
        return {"sampled": False, "error": "not_one_mesh"}
    axis = int(np.argmin(mesh.extents))
    other_axes = [i for i in range(3) if i != axis]
    epsilon = max(0.08, 0.004 * float(min(mesh.extents[other_axes])))
    normal = np.eye(3)[axis]
    summaries = []
    for fraction in (0.12, 0.5, 0.88):
        origin = mesh.bounds[0].copy()
        origin[axis] += mesh.extents[axis] * fraction
        section = mesh.section(plane_origin=origin, plane_normal=normal)
        if section is None:
            continue
        outlines = []
        for loop in section.discrete:
            projected = np.asarray(loop)[:, other_axes]
            if len(projected) > 12000:
                # Keep the sampled geometry dense enough for the outline gate.
                stride = int(math.ceil(len(projected) / 12000))
                projected = projected[::stride]
            simplified = closed_rdp(projected, epsilon)
            summary = polygon_summary(simplified)
            if summary:
                outlines.append(summary)
        if outlines:
            # The outer contour has greater area than any one hole loop.
            summaries.append(max(outlines, key=lambda value: value["area"]))
    if not summaries:
        return {"sampled": True, "valid_sections": 0}
    areas = [s["area"] for s in summaries]
    return {
        "sampled": True,
        "valid_sections": len(summaries),
        "max_reflex": max(s["reflex"] for s in summaries),
        "max_sharp_turns": max(s["sharp_turns"] for s in summaries),
        "max_turn_excess": round(max(s["turn_excess"] for s in summaries), 3),
        "max_perimeter_ratio": round(max(s["perimeter_ratio"] for s in summaries), 3),
        "max_area_deficit": round(max(s["area_deficit"] for s in summaries), 3),
        "area_change_ratio": round((max(areas) - min(areas)) / max(areas), 3),
    }


def classify(row: dict) -> list[str]:
    reasons = []
    circles = row["circles"]
    contour = row["thin_outline"]
    profile = row["long_axis_profile"]
    centers = circles["circle_centers"]
    types = circles["diameter_types"]
    euler = row["euler"]
    if row.get("coaxial_stepped"):
        reasons.append("coaxial_stepped_body")
    if row.get("two_stage_coaxial_profile"):
        reasons.append("two_stage_coaxial_profile")
    if ((centers >= 5 and types >= 2 and euler <= -2)
            or (centers >= 8 and euler <= -2)
            or (centers >= 6 and types >= 3)
            or (centers >= 3 and types >= 4
                and circles["circle_features"] >= 8)):
        reasons.append("multi_hole")
    if (profile["median_reflex"] >= 2
            and profile["median_deficit"] >= 0.05
            and profile["median_perimeter"] >= 1.04):
        reasons.append("concave_sections")
    if (profile["median_reflex"] >= 5
            and profile["median_perimeter"] >= 1.025
            and "concave_sections" not in reasons):
        reasons.append("many_section_turns")
    if (profile["median_reflex"] >= 1
            and profile["median_deficit"] >= 0.08
            and profile["median_perimeter"] >= 1.025
            and (centers >= 4 or
                 (row["local_shape_change"] is not None
                  and row["local_shape_change"] >= 0.45
                  and row["convex_hull_volume_deficit"] >= 0.25))
            and "concave_sections" not in reasons):
        reasons.append("formed_concave_transition")
    if contour.get("valid_sections", 0) >= 2:
        if (contour["max_reflex"] >= 2
                and contour["max_turn_excess"] >= 1.0):
            reasons.append("recessed_outline")
        elif contour["max_sharp_turns"] >= 8:
            reasons.append("many_outline_turns")
        if ("recessed_outline" not in reasons
                and contour["max_turn_excess"] >= 2.0
                and contour["max_perimeter_ratio"] >= 1.04):
            reasons.append("curved_or_irregular_outline")
        if (contour["area_change_ratio"] >= 0.03
                and (centers >= 2 or contour["max_reflex"] >= 1)):
            reasons.append("layered_outline")
    # A local span/perimeter change alone also catches simple tapers. It only
    # strengthens an independently detected feature, never selects by itself.
    variation = row["local_shape_change"]
    if (reasons and variation is not None and variation >= 0.25
            and row["convex_hull_volume_deficit"] >= 0.05):
        reasons.append("local_3d_variation")
    return reasons


LABELS = {
    "coaxial_stepped_body": "同轴多级变径",
    "two_stage_coaxial_profile": "相邻两级同轴直径需轴向尺寸",
    "multi_hole": "多孔/多孔径",
    "concave_sections": "沿长轴截面有多个凹角",
    "many_section_turns": "沿长轴截面转折多",
    "formed_concave_transition": "凹形截面与局部三维起伏",
    "recessed_outline": "板面轮廓有多个内凹转折",
    "many_outline_turns": "板面轮廓转折密集",
    "curved_or_irregular_outline": "非规则弧线/轮廓",
    "layered_outline": "厚度方向轮廓变化",
    "local_3d_variation": "局部三维起伏明显",
}


def run(manifest_path: Path, harbor_dir: Path, work_dir: Path,
        output_dir: Path) -> None:
    cases = json.loads(manifest_path.read_text())
    present = {path.name for path in harbor_dir.glob("cad_*") if path.is_dir()}
    if {case["name"] for case in cases} != present:
        raise ValueError("Manifest/Harbor case inventory mismatch")
    os.environ.update(CAD_COMPLEXITY_WORK=str(work_dir.resolve()),
                      CAD_COMPLEXITY_HARBOR=str(harbor_dir.resolve()),
                      CAD_COMPLEXITY_OUTPUT=str(output_dir.resolve()))
    from run_complexity_screen import current_case_source
    excluded = json.loads((work_dir / "complexity_auto_candidates.json").read_text())
    high = {(row["case"], row["family"]) for row in excluded}
    if len(high) != len(excluded):
        raise ValueError("Duplicate family keys in existing complexity data")
    multiaxis_path = work_dir / "multiaxis_annotation_gap_scan.json"
    if multiaxis_path.is_file():
        high.update((row["case"], row["family"])
                    for row in json.loads(multiaxis_path.read_text()))
    profiles = json.loads((work_dir / "profile_scan.json").read_text())
    by_profile = {(row["case"], row["family"]): row for row in profiles}
    if len(by_profile) != len(profiles):
        raise ValueError("Duplicate family keys in existing complexity data")
    expected = set()
    rows = []
    counts = Counter()
    started = time.perf_counter()
    for case in cases:
        case_name = case["name"]
        root, spec = current_case_source(case)
        root = Path(root)
        geometry = json.loads((root / "analytic/manual_geometry.json").read_text())
        pages = pdf_pages(harbor_dir / case_name / "environment/input/parts.pdf")
        aligned = {row["id"]: row for row in
                   json.loads((root / "aligned/manifest.json").read_text())["families"]}
        occurrence = {row["id"]: row for row in spec["occurrences"]}
        family_ids = {row["id"] for row in spec["families"]}
        if (set(pages) != family_ids or set(aligned) != family_ids
                or len(geometry["parts"]) != len(spec["occurrences"])):
            raise ValueError(f"STEP geometry/PDF/spec/mesh family mismatch: {case_name}")
        for family in spec["families"]:
            fid = family["id"]
            key = (case_name, fid)
            expected.add(key)
            if key in high:
                continue
            profile = by_profile[key]
            manifest = aligned[fid]
            representative = occurrence[family["occurrence_ids"][0]]
            # reference_index is the shared source-solid index. Manual STEP
            # geometry uses assembly axes, while the spec size is locally
            # aligned; their bounding boxes need not match after rotation.
            source_part = geometry["parts"][representative["reference_index"]]
            metrics = representative["mechanical_metrics"]
            dimensions = family["sorted_size_mm"]
            contour = thin_outline_summary(root / "aligned" / manifest["mesh"],
                                           dimensions)
            analytic = analytic_circles(source_part)
            row = {
                "case": case_name,
                "family": fid,
                "quantity": family["quantity"],
                "pdf_page": pages[fid],
                "size_mm": dimensions,
                "euler": int(metrics["euler_number"]),
                "circles": circle_summary(analytic),
                "coaxial_stepped": coaxial_stepped_circles(analytic),
                "coaxial_axis_tilt_deg": coaxial_stepped_axis_tilt(analytic),
                "two_stage_coaxial_profile":
                    two_stage_coaxial_axis_tilt(analytic) is not None,
                "long_axis_profile": {
                    "median_reflex": profile["median_reflex"],
                    "median_deficit": profile["median_deficit"],
                    "median_perimeter": profile["median_perimeter"],
                },
                "thin_outline": contour,
                "convex_hull_volume_deficit":
                    manifest["convex_hull_volume_deficit_ratio"] or 0,
                "local_shape_change":
                    (manifest["local_shape_change"].get("perimeter_change_ratio")
                     if manifest["local_shape_change"].get("status") == "ok" else None),
            }
            row["reasons"] = classify(row)
            if row["reasons"]:
                independent = [reason for reason in row["reasons"]
                               if reason != "local_3d_variation"]
                row["tier"] = ("优先" if len(independent) >= 2
                               or row["circles"]["circle_centers"] >= 10
                               or row["thin_outline"].get("max_reflex", 0) >= 4
                               else "普通")
                rows.append(row)
                counts[case_name] += 1
        print(f"{case_name}: {counts[case_name]} medium candidates", flush=True)
    if high - expected or set(by_profile) != expected:
        raise ValueError("Existing high-complexity/profile data does not match current families")
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "中等难度零件候选.json"
    markdown_path = output_dir / "中等难度零件候选.md"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    lines = [
        "# 需增加自动标注信息量的中等难度零件候选", "",
        f"本轮 {len(cases)} 题共 {len(expected)} 个 family；先排除 {len(high)} 个高难候选，",
        f"在剩余 {len(expected) - len(high)} 个中筛出 {len(rows)} 个中等难度候选。",
        "此清单用于增加尺寸标注信息量，不表示现有图纸已完整，也不自动决定 STEP 公开范围。", "",
        "规则不使用 case/family 白名单。圆特征按 STEP 解析圆边去重；圆心可能属于孔、沉孔或圆柱台阶，",
        "因此报告称为孔/圆特征候选。沿长轴截面的凹角来自既有全量扫描；对薄件另外沿厚度取三层切片，",
        "统计外轮廓的内凹角、显著转折及层间变化。局部截面变化只增强其他独立信号，不单独入选。", "",
        "主要触发门槛：至少 5 个圆特征中心且有两种直径并有通孔拓扑证据，或至少 8 个中心；"
        "沿长轴截面至少 2 个凹角、5% 凹度和 4% 周长增量；"
        "薄件外轮廓至少 2 个显著凹角，或至少 8 个显著转折。"
        "其他互补门槛和分级条件见脚本 `classify()`。", "",
        "|题目|原 PDF|高难排除|中等候选|", "|---|---:|---:|---:|",
    ]
    for case in cases:
        name = case["name"]
        pdf_path = harbor_dir / name / "environment/input/parts.pdf"
        family_count = case.get("family_count", sum(key[0] == name for key in expected))
        lines.append(f"|[{name}]({pdf_path})|{family_count}|"
                     f"{sum(key[0] == name for key in high)}|{counts[name]}|")
    lines += ["", "## 候选明细", "",
              "|题目|family|PDF 页|数量|优先级|圆特征中心/直径种类|板面凹角|原因|",
              "|---|---|---:|---:|---|---:|---:|---|"]
    for row in rows:
        circle = row["circles"]
        outline = row["thin_outline"]
        reason = "、".join(LABELS[code] for code in row["reasons"])
        lines.append(f"|{row['case']}|{row['family']}|{row['pdf_page']}|"
                     f"{row['quantity']}|{row['tier']}|"
                     f"{circle['circle_centers']}/{circle['diameter_types']}|"
                     f"{outline.get('max_reflex', '—')}|{reason}|")
    lines += ["", "每件的数值证据见 `中等难度零件候选.json`。"
              "本筛选的优先级只用于安排详细出图顺序；实际尺寸完整性仍需逐件校验。", ""]
    markdown_path.write_text("\n".join(lines))
    print(f"{len(expected)} total, {len(high)} high excluded, "
          f"{len(rows)} medium, {len(expected) - len(high) - len(rows)} remaining; "
          f"{time.perf_counter() - started:.1f}s")
    print(markdown_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--harbor-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args.manifest, args.harbor_dir, args.work_dir, args.output_dir)


if __name__ == "__main__":
    main()
