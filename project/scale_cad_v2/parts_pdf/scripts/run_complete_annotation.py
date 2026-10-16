#!/usr/bin/env python3
"""Batch a medium-family PDF/STEP delivery with a fail-closed geometry audit.

For each selected family, the same matching STEP cache and aligned mesh are
used to plan dimensions and build the PDF.  A family whose three-dimensional
geometry cannot be reconstructed from the supported primitives automatically
receives a locally aligned single-part STEP.  No incomplete PDF is advertised
as a complete dimensional definition.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import os
import shutil

import fitz

from build_medium_detail_book import build
from general_dimension_engine import analyze_family


def checked_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_steps(args, selected, selected_file, step_dir):
    if not selected:
        return
    root = Path(__file__).resolve().parent
    freecad_python = str(args.freecad_python or os.environ.get('FREECAD_PYTHON', 'python'))
    freecad_lib = args.freecad_lib or os.environ.get('FREECAD_LIB')
    if not shutil.which(freecad_python):
        raise ValueError('FreeCAD Python not found; set FREECAD_PYTHON or pass --freecad-python')
    command = [
        str(freecad_python), str(root/"export_family_steps.py"),
        "--step", str(args.step.resolve()), "--spec", str(args.spec.resolve()),
        "--catalog", str(args.catalog.resolve()), "--geometry", str(args.geometry.resolve()),
        "--features", str(args.features.resolve()), "--families-json", str(selected_file.resolve()),
        "--output-dir", str(step_dir.resolve()),
    ]
    if freecad_lib:
        command.extend(["--freecad-lib", str(freecad_lib)])
    subprocess.run(command, check=True)
    expected = {family + ".step" for family in selected}
    actual = {path.name for path in step_dir.glob("*.step")}
    if actual != expected:
        raise ValueError(f"STEP directory mismatch: missing={expected-actual}; stale={actual-expected}")
    for name in expected:
        if (step_dir/name).stat().st_size < 1000:
            raise ValueError(f"STEP export too small: {name}")


def run(args):
    args.work_dir.mkdir(parents=True, exist_ok=True)
    source_sha = checked_sha(args.step)
    catalog = json.loads(args.catalog.read_text())
    if source_sha != catalog["source_sha256"]:
        raise ValueError("Source STEP hash differs from the same-run mesh catalog")
    selected_rows = [row for row in json.loads(args.candidates.read_text())
                     if row["case"] == args.case]
    if not selected_rows:
        raise ValueError(f"No selected families for {args.case}")
    feature_map = {item["family"]: item
                   for item in json.loads(args.features.read_text())}
    plans = []
    for row in selected_rows:
        family = row["family"]
        if family not in feature_map:
            raise ValueError(f"No matching STEP features for {family}")
        plans.append(analyze_family(feature_map[family],
                                    args.meshes/(family+".stl"),args.features,
                                    args.geometry))
    plan_file = args.work_dir/"dimension-plan.json"
    plan_file.write_text(json.dumps(plans, indent=2, ensure_ascii=False))
    step_families = sorted(p["family"] for p in plans
                           if p["status"] == "step_required")
    selected_file = args.work_dir/"step-required.json"
    selected_file.write_text(json.dumps(step_families, indent=2))
    step_dir = args.work_dir/"provided_part_files"
    step_dir.mkdir(exist_ok=True)
    export_steps(args, step_families, selected_file, step_dir)

    pdf_path = args.work_dir/"dimensioned_parts.pdf"
    drawing_args = argparse.Namespace(case=args.case, input=args.input,
                                      candidates=args.candidates,
                                      features=args.features, meshes=args.meshes,
                                      geometry=args.geometry,
                                      renders=args.renders, output=pdf_path,
                                      step_files_dir=step_dir)
    build(drawing_args)
    audit=json.loads(pdf_path.with_suffix(".audit.json").read_text())
    if len(audit)!=len(plans):
        raise ValueError("Dimension audit/family count mismatch")
    for entry,plan in zip(audit,plans):
        if (entry["family"]!=plan["family"] or
                entry["additional_cylindrical_groups"]+
                entry["oblique_cylindrical_groups_in_step"] !=
                plan["circular_coverage"]["secondary"]):
            raise ValueError(f"Unaccounted cylindrical groups: {plan['family']}")
        if (entry["oblique_cylindrical_groups_in_step"] and
                plan["status"]!="step_required"):
            raise ValueError(f"Oblique features need STEP: {plan['family']}")
    expected_pages=sum(1+item.get("supplement_pages",1)+
                       item["additional_projection_pages"] for item in audit)
    with fitz.open(pdf_path) as pdf:
        if len(pdf) != expected_pages:
            raise ValueError("Unexpected dimensional PDF page count")
        notices = "\n".join(page.get_text() for page in pdf)
        for family in step_families:
            if f"STEP PROVIDED: provided_part_files/{family}.step" not in notices:
                raise ValueError(f"PDF has no STEP notice for {family}")
    manifest = {
        "case": args.case,
        "source_step_sha256": source_sha,
        "pdf": str(pdf_path),
        "family_count": len(plans),
        "families": [{
            "family": plan["family"],
            "delivery": ("dimensioned_pdf" if plan["status"] == "geometry_certified"
                         else "dimensioned_pdf_plus_step"),
            "certificate": plan["certificate"],
            "secondary_cylindrical_groups": plan["circular_coverage"]["secondary"],
            "additional_projection_pages": audit[i]["additional_projection_pages"],
            "supplement_pages": audit[i].get("supplement_pages",1),
            "oblique_cylindrical_groups_in_step": audit[i]["oblique_cylindrical_groups_in_step"],
            "unresolved": plan["unresolved"],
            "step": (str(step_dir/(plan["family"]+".step"))
                     if plan["status"] == "step_required" else None),
        } for i,plan in enumerate(plans)],
    }
    result_file = args.work_dir/"delivery-manifest.json"
    result_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(result_file)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("step", "spec", "catalog", "geometry", "features", "meshes",
                 "renders", "candidates", "input", "work_dir"):
        parser.add_argument("--"+name.replace("_", "-"), required=True, type=Path)
    parser.add_argument("--case", required=True)
    parser.add_argument("--freecad-python", type=Path)
    parser.add_argument("--freecad-lib", type=Path)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
