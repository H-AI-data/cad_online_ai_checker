"""Validate source PDF drawings and bind them to this build's part families."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf


def prepare_source_drawings(step: Path, spec_path: Path, output: Path) -> set[str]:
    sidecar = step.parent / "source_drawings.json"
    if not sidecar.is_file():
        return set()
    data = json.loads(sidecar.read_text())
    if data.get("schema") != "harbor/source-drawings-v1":
        raise ValueError(f"Unsupported source drawing manifest: {sidecar}")
    source_sha = hashlib.sha256(step.read_bytes()).hexdigest()
    if data.get("source_step_sha256") != source_sha:
        raise ValueError("Source drawing manifest does not match the STEP")
    spec = json.loads(spec_path.read_text())
    by_index = {row["reference_index"]: row for row in spec["occurrences"]}
    by_id = {row["id"]: row for row in spec["occurrences"]}
    if set(by_index) != set(range(spec["occurrence_count"])):
        raise ValueError("Part spec reference indices are incomplete")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError("Source drawing entries must be a list")
    candidates = {}
    for entry in entries:
        index = entry.get("reference_index")
        if index not in by_index or index in candidates:
            raise ValueError(f"Unknown or repeated source drawing index: {index}")
        drawings = entry.get("pdfs")
        if not isinstance(drawings, list) or not drawings:
            raise ValueError(f"Index {index} has no PDF drawings")
        verified = []
        for drawing in drawings:
            relative = Path(drawing["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("PDF path must stay beneath the source STEP directory")
            path = (step.parent / relative).resolve()
            if not path.is_relative_to(step.parent.resolve()) or not path.is_file():
                raise ValueError(f"Source PDF is missing: {relative}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != drawing.get("sha256"):
                raise ValueError(f"Source PDF checksum differs: {relative}")
            with pymupdf.open(path) as pdf:
                if len(pdf) < 1:
                    raise ValueError(f"Source PDF is empty: {relative}")
                pages = len(pdf)
            verified.append({"path": str(path), "sha256": digest, "pages": pages})
        candidates[index] = verified
    families = {}
    for family in spec["families"]:
        occurrences = [by_id[occurrence_id] for occurrence_id in family["occurrence_ids"]]
        matching = [row for row in occurrences if row["reference_index"] in candidates]
        if matching:
            families[family["id"]] = candidates[matching[0]["reference_index"]]
    result = {"schema": "harbor/verified-source-drawings-v1",
              "source_step_sha256": source_sha,
              "part_spec_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
              "families": families}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return set(families)
