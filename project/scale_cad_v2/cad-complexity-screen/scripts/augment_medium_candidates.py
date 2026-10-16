#!/usr/bin/env python3
"""Refresh medium candidates using analytic coaxial-step geometry.

This lets a case's existing screen output feed both the detailed-page builder
and the four-tier assembler without a hand-maintained family override.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import trimesh

from screen_medium_parts import (coaxial_stepped_axis_tilt,
                                 compound_head_axis_tilt, recessed_head_drive,
                                 two_stage_coaxial_axis_tilt)
from scan_localized_serrations import localized_end_serrations


def requires_freeform_step(row):
    """Escalate a deeply concave, multiaxis body beyond planar dimensioning."""
    profile = row.get("long_axis_profile", {})
    circles = row.get("circles", {})
    return (profile.get("median_reflex", 0) >= 3
            and profile.get("median_deficit", 0) >= .45
            and profile.get("median_perimeter", 0) >= 1.16
            and circles.get("axis_directions", 0) >= 2
            and row.get("convex_hull_volume_deficit", 0) >= .55
            and (row.get("local_shape_change") or 0) >= .35)


def augment(candidates, features, spec, case, existing_difficult=(), meshes=None):
    rows = [row for row in candidates
            if row["case"] != case or not requires_freeform_step(row)]
    difficult = set(existing_difficult)
    difficult.update(row["family"] for row in candidates
                     if row["case"] == case and requires_freeform_step(row))
    if meshes is not None:
        for feature in features:
            family = feature["family"]
            if family in difficult:
                continue
            mesh = trimesh.load_mesh(Path(meshes) / f"{family}.stl",
                                     process=False)
            if localized_end_serrations(mesh):
                difficult.add(family)
        rows = [row for row in rows
                if row["case"] != case or row["family"] not in difficult]
    known = {(row["case"], row["family"]) for row in rows}
    quantities = {row["id"]: row["quantity"] for row in spec["families"]}
    for feature in features:
        if feature["family"] in difficult:
            continue
        key = case, feature["family"]
        tilt = coaxial_stepped_axis_tilt(feature["circles"])
        compound_head = False
        recessed_head = None
        two_stage = False
        if tilt is None:
            tilt = compound_head_axis_tilt(feature["circles"], feature["dimensions"])
            compound_head = tilt is not None
        if tilt is None and meshes is not None:
            recessed_head = recessed_head_drive(
                Path(meshes) / f"{feature['family']}.stl",
                feature["circles"], feature["dimensions"])
            if recessed_head is not None:
                tilt = 0.
        if tilt is None:
            tilt = two_stage_coaxial_axis_tilt(feature["circles"])
            two_stage = tilt is not None
        if tilt is None:
            continue
        # A longitudinal projection of a tilted axis does not preserve true
        # segment lengths.  Supply the exact B-rep instead of a misleading
        # dimension chain until this family is re-aligned and re-rendered.
        if tilt >= 3.0:
            difficult.add(feature["family"])
            rows = [row for row in rows if (row["case"], row["family"]) != key]
            known.discard(key)
            continue
        if key in known:
            continue
        rows.append({"case": case, "family": feature["family"],
                     "quantity": quantities[feature["family"]],
                     "pdf_page": feature["page"] + 1,
                     "tier": "普通",
                     "reasons": ["two_stage_coaxial_profile" if two_stage else
                                 "recessed_fastener_head" if recessed_head else
                                 "compound_axial_head" if compound_head else
                                 "coaxial_stepped_body"],
                     "coaxial_stepped": not two_stage,
                     "two_stage_coaxial_profile": two_stage,
                     "compound_axial_head": compound_head,
                     "recessed_fastener_head": bool(recessed_head),
                     "drive_section": recessed_head,
                     "coaxial_axis_tilt_deg": tilt})
        known.add(key)
    return rows, sorted(difficult)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--output", type=Path, required=True)
    existing = parser.add_mutually_exclusive_group(required=True)
    existing.add_argument("--step-files-dir", type=Path,
                          help="Existing difficult-family STEP files")
    existing.add_argument("--existing-difficult-json", type=Path,
                          help="Verified high-complexity family IDs before STEP export")
    parser.add_argument("--difficult-output", type=Path, required=True)
    parser.add_argument("--meshes", type=Path,
                        help="Aligned STL directory for non-round head recesses")
    args = parser.parse_args()
    existing_difficult = (json.loads(args.existing_difficult_json.read_text())
                          if args.existing_difficult_json else
                          [path.stem for path in args.step_files_dir.glob("family_*.step")])
    if (not isinstance(existing_difficult, list)
            or any(not isinstance(fid, str) for fid in existing_difficult)
            or len(existing_difficult) != len(set(existing_difficult))):
        raise ValueError("Existing difficult families must be a unique family-ID list")
    rows, difficult = augment(json.loads(args.candidates.read_text()),
                   json.loads(args.features.read_text()),
                   json.loads(args.spec.read_text()), args.case,
                   existing_difficult,
                   args.meshes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    args.difficult_output.write_text(json.dumps(difficult, indent=2))
    print(f"{args.case}: {sum(row['case'] == args.case for row in rows)} medium, "
          f"{len(difficult)} difficult")


if __name__ == "__main__":
    main()
