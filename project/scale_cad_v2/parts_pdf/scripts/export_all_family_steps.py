#!/usr/bin/env python3
"""Provide every source-bound part family as a validated, centered STEP solid.

This entry point is for assembly-only Harbor tasks, which have no parts.pdf.
Run with the FreeCAD Python bundled with the host installation.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid_single_solid(path, Part):
    solids = Part.read(str(path)).Solids
    if len(solids) != 1 or not solids[0].isValid() or not solids[0].isClosed() or solids[0].Volume <= 0:
        raise ValueError(f"Exported STEP is not one valid closed solid: {path.name}")
    return solids[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--freecad-lib", type=Path)
    args = parser.parse_args()
    if args.freecad_lib:
        sys.path.insert(0, str(args.freecad_lib))
    import FreeCAD as App
    import Part

    spec = json.loads(args.spec.read_text())
    catalog = json.loads(args.catalog.read_text())
    source_sha = sha256(args.step)
    if catalog.get("source_sha256") != source_sha:
        raise ValueError("Catalog does not match source STEP")
    solids = Part.read(str(args.step)).Solids
    occurrences = spec["occurrences"]
    families = spec["families"]
    if len(solids) != len(occurrences) or len(solids) != len(catalog["parts"]):
        raise ValueError("Source solid, spec occurrence and catalog counts differ")
    by_occurrence = {item["id"]: item for item in occurrences}
    if len(by_occurrence) != len(occurrences):
        raise ValueError("Duplicate occurrence IDs")
    if len({item["id"] for item in families}) != len(families):
        raise ValueError("Duplicate family IDs")
    for solid in solids:
        if not solid.isValid() or not solid.isClosed() or solid.Volume <= 0:
            raise ValueError("Source assembly contains an invalid solid")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if any(args.output_dir.iterdir()):
        raise ValueError("Output directory must be empty")
    files = {}
    for number, family in enumerate(families, 1):
        family_id = family["id"]
        index = int(by_occurrence[family["occurrence_ids"][0]]["reference_index"])
        original = solids[index]
        expected_volume = float(catalog["parts"][index]["volume_mm3"])
        if not math.isclose(original.Volume, expected_volume, rel_tol=0.005, abs_tol=0.05):
            raise ValueError(f"{family_id}: catalog/source BRep volume mismatch")
        center = original.CenterOfMass
        destination = args.output_dir / f"{family_id}.step"
        attempt_errors = []
        with tempfile.TemporaryDirectory(dir=args.output_dir) as temp_dir:
            staged = Path(temp_dir) / destination.name
            exported = None
            method = None
            candidates = [("source", original)]
            while candidates:
                method_name, candidate = candidates.pop(0)
                try:
                    local = candidate.copy()
                    local.translate(App.Vector(-center.x, -center.y, -center.z))
                    local.exportStep(str(staged))
                    imported = valid_single_solid(staged, Part)
                    if not math.isclose(imported.Volume, original.Volume, rel_tol=0.005, abs_tol=0.01):
                        raise ValueError("volume changed on STEP roundtrip")
                    if not math.isclose(imported.Area, original.Area, rel_tol=0.03, abs_tol=0.1):
                        raise ValueError("surface area changed on STEP roundtrip")
                    # STEP roundtrips can shift the mass center slightly when
                    # the kernel heals a large solid; 0.1 mm stays well below
                    # the assembly placement tolerance.
                    if imported.CenterOfMass.Length > 0.1:
                        raise ValueError("center of mass is not at the local origin")
                    exported, method = imported, method_name
                    os.replace(staged, destination)
                    break
                except Exception as exc:
                    attempt_errors.append(f"{method_name}: {exc}")
                    if method_name == "source":
                        try:
                            refined = original.removeSplitter()
                            if (len(refined.Solids) == 1 and refined.isValid() and refined.isClosed()
                                    and math.isclose(refined.Volume, original.Volume, rel_tol=1e-5, abs_tol=0.01)
                                    and math.isclose(refined.Area, original.Area, rel_tol=1e-5, abs_tol=0.1)
                                    and (refined.CenterOfMass - center).Length < 0.01):
                                candidates.append(("refined", refined.Solids[0]))
                        except Exception:
                            pass
            if exported is None:
                raise ValueError(f"{family_id}: invalid STEP export: {'; '.join(attempt_errors)}")
        files[family_id] = {
            "reference_index": index,
            "sha256": sha256(destination),
            "bytes": destination.stat().st_size,
            "volume_mm3": round(exported.Volume, 6),
            "source_center_mm": [center.x, center.y, center.z],
            "export_method": method,
        }
        if number % 20 == 0 or number == len(families):
            print(f"validated {number}/{len(families)} family STEP files", flush=True)
    report = {
        "schema": "harbor/all-family-steps-v1",
        "source_step_sha256": source_sha,
        "part_spec_sha256": sha256(args.spec),
        "files": files,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"families": len(files), "manifest": str(args.manifest)}), flush=True)


if __name__ == "__main__":
    main()
