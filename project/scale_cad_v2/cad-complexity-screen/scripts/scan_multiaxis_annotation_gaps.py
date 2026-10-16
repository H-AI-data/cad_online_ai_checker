"""Find parts whose crossed, stepped openings defeat a planar drawing.

Use analytic STEP circles to recognize repeated coaxial diameter steps, then
check that the selected mesh profile has multiple interior contours not
explained by those circles.  This is a geometric difficulty signal, not a
family-name or screenshot rule.
"""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import sys

from config import WORK
from run_complexity_screen import current_case_source


sys.path.insert(0, str(Path(__file__).resolve().parents[2] /
                       "parts_pdf" / "scripts"))
from general_dimension_engine import analyze_family, grouped_circles  # noqa: E402


def stepped_centers(circles):
    by_center = defaultdict(set)
    for circle in circles:
        if abs(circle["axis"][2]) > .995:
            key = tuple(round(float(value), 1) for value in circle["center"][:2])
            by_center[key].add(round(float(circle["diameter"]), 2))
    return sum(len(diameters) >= 2 for diameters in by_center.values())


def main():
    hits = []
    examined = 0
    for case in json.loads((WORK / "selected.json").read_text()):
        root, spec = current_case_source(case)
        features_path = root / "feature-annotations.json"
        geometry_path = root / "analytic/manual_geometry.json"
        if not features_path.is_file() or not geometry_path.is_file():
            raise FileNotFoundError(f"STEP analytic features missing: {root}")
        features = {item["family"]: item
                    for item in json.loads(features_path.read_text())}
        known = {family["id"] for family in spec["families"]}
        if set(features) - known:
            raise ValueError(f"Unknown analytic family: {case['name']}")
        for family in spec["families"]:
            fid = family["id"]
            # The STEP feature extractor may omit an unparseable family.  Its
            # absence cannot be counted as this specific multi-axis signal.
            if fid not in features:
                continue
            feature = features[fid]
            circles = grouped_circles(feature["circles"])
            axial = [circle for circle in circles
                     if abs(circle["axis"][2]) > .995]
            transverse = [circle for circle in circles
                          if max(abs(circle["axis"][0]),
                                 abs(circle["axis"][1])) > .995]
            steps = stepped_centers(circles)
            if len(axial) < 6 or len(transverse) < 2 or steps < 4:
                continue
            examined += 1
            try:
                plan = analyze_family(feature, root / "aligned" / f"{fid}.stl",
                                      features_path, geometry_path)
            except ValueError as error:
                # A few imported B-splines tessellate slightly outside their
                # analytic STEP envelope. This signal cannot be measured from
                # inconsistent frames; let the other complexity checks decide.
                if "mesh and STEP feature frames differ" not in str(error):
                    raise
                print(f"{case['name']} {fid}: skipped {error}", flush=True)
                continue
            profile = plan.get("profile") or {}
            unmatched = (profile.get("inner_loops", 0) -
                         profile.get("matched_inner_loops", 0))
            if unmatched < 2 or plan["status"] != "step_required":
                continue
            hits.append({"case": case["name"], "family": fid,
                         "quantity": family["quantity"],
                         "axial_cylinders": len(axial),
                         "transverse_cylinders": len(transverse),
                         "stepped_axis_centers": steps,
                         "unexplained_profile_loops": unmatched})
        print(case["name"], "done", flush=True)
    destination = WORK / "multiaxis_annotation_gap_scan.json"
    destination.write_text(json.dumps(hits, ensure_ascii=False, indent=2))
    print("examined", examined, "multiaxis profile-gap hits", len(hits))
    for row in hits:
        print(row)


if __name__ == "__main__":
    main()
