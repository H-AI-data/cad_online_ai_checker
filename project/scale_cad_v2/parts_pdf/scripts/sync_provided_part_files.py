#!/usr/bin/env python3
"""Mirror supplied part STEPs into a separate Harbor verifier build context."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path


def sync(task_dir: Path) -> list[str]:
    source = task_dir / "environment" / "input" / "provided_part_files"
    tests_dir = task_dir / "tests"
    destination = tests_dir / "provided_part_files"
    spec_path = tests_dir / "reference" / "part-spec.json"
    if not tests_dir.is_dir() or not spec_path.is_file():
        raise FileNotFoundError("Task must contain tests/reference/part-spec.json")
    known = {item["family_id"] for item in json.loads(spec_path.read_text())["occurrences"]}
    files = [] if not source.is_dir() else sorted(
        path for path in source.iterdir() if path.suffix.lower() in {".step", ".stp"}
    )
    families: set[str] = set()
    for path in files:
        if not path.is_file() or not re.fullmatch(r"family_\d+", path.stem):
            raise ValueError(f"Invalid provided part file: {path}")
        if path.stem not in known or path.stem in families:
            raise ValueError(f"Unknown or duplicate provided family: {path.stem}")
        if path.stat().st_size == 0:
            raise ValueError(f"Empty provided part file: {path}")
        families.add(path.stem)
    override_path = spec_path.parent / "step_tier_overrides.json"
    if override_path.is_file():
        record = json.loads(override_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("schema") != "harbor/step-tier-overrides-v1":
            raise ValueError("Invalid STEP tier override manifest")
        if (record.get("source_step_sha256") != hashlib.sha256(
                (spec_path.parent / "reference.step").read_bytes()).hexdigest()
                or record.get("part_spec_sha256") != hashlib.sha256(spec_path.read_bytes()).hexdigest()):
            raise ValueError("STEP tier override source or part spec mismatch")
        required = record.get("families")
        if (not isinstance(required, list)
                or any(not isinstance(fid, str) for fid in required)
                or len(required) != len(set(required))):
            raise ValueError("STEP tier override needs unique family IDs")
        if set(required) - families:
            raise ValueError(f"Required STEP files missing: {sorted(set(required) - families)}")
    manifest_path = spec_path.parent / "provided_step_manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict)
                or manifest.get("schema") != "harbor/all-family-steps-v1"
                or manifest.get("source_step_sha256") != hashlib.sha256(
                    (spec_path.parent / "reference.step").read_bytes()).hexdigest()
                or manifest.get("part_spec_sha256") != hashlib.sha256(spec_path.read_bytes()).hexdigest()):
            raise ValueError("Provided STEP manifest source or part spec mismatch")
        records = manifest.get("files")
        if not isinstance(records, dict) or set(records) != families:
            raise ValueError("Provided STEP manifest and public files differ")
        for path in files:
            if (not isinstance(records[path.stem], dict)
                    or records[path.stem].get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest()):
                raise ValueError(f"Provided STEP checksum mismatch: {path.name}")
    if destination.exists():
        shutil.rmtree(destination)
    if files:
        destination.mkdir()
        for path in files:
            shutil.copy2(path, destination / path.name)
    return sorted(families)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_dir", type=Path, help="Harbor task root")
    args = parser.parse_args()
    result = sync(args.task_dir)
    print(f"Mirrored {len(result)} provided part families: {', '.join(result)}")
