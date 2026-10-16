#!/usr/bin/env python3
"""Check that every STEP notice in parts.pdf has exactly one supplied family STEP."""

import argparse
import json
from pathlib import Path
import re

import fitz


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--families-json", required=True, type=Path)
    parser.add_argument("--step-files-dir", required=True, type=Path)
    view_mode = parser.add_mutually_exclusive_group()
    view_mode.add_argument("--require-three-views", dest="require_three_views",
                           action="store_true", default=True,
                           help="Default: require upper 3/4, opposite 3/4 and top Blender views")
    view_mode.add_argument("--allow-missing-three-views", dest="require_three_views",
                           action="store_false", help="Inspect a legacy bundle without this view check")
    args = parser.parse_args()
    selected = json.loads(args.families_json.read_text())
    if not isinstance(selected, list) or len(selected) != len(set(selected)):
        raise ValueError("Expected a unique family-ID array")
    actual_files = {p.name for p in args.step_files_dir.glob("*.step")}
    expected_files = {family + ".step" for family in selected}
    if actual_files != expected_files:
        raise ValueError(f"STEP file mismatch: missing={expected_files - actual_files}, "
                         f"extra={actual_files - expected_files}")
    if any((args.step_files_dir / name).stat().st_size < 1000
           for name in expected_files):
        raise ValueError("A supplied STEP file is unexpectedly small")
    doc = fitz.open(args.pdf)
    page_texts = [page.get_text() for page in doc]
    index_texts = []
    for text in page_texts:
        if text.startswith(("FAMILY_", "STANDARD PART FAMILY DETAILS")):
            break
        index_texts.append(text)
    if sum(text.count("STEP PROVIDED") for text in index_texts) != len(selected):
        raise ValueError("Cover STEP count differs from supplied files")
    notices = []
    for text in page_texts[1:]:
        notices.extend(re.findall(r"STEP PROVIDED: provided_part_files/(family_\d+\.step)", text))
        for stale in ("DETAIL A", "DETAIL B"):
            if stale in text:
                raise ValueError(f"Old detail dimension content remains: {stale}")
    if set(notices) != expected_files or len(notices) != len(selected):
        raise ValueError("Family-page notices do not match supplied STEP files")
    if args.require_three_views:
        for family in selected:
            matches = [page for page, page_text in zip(doc, page_texts)
                       if page_text.startswith(family.upper() + "\n")]
            if len(matches) != 1:
                raise ValueError(f"Expected one difficult page for {family}")
            page = matches[0]
            text = page_texts[page.number]
            labels = ("UPPER 3/4 VIEW", "OPPOSITE 3/4 VIEW",
                      "TOP VIEW | TRUE +Z")
            if ("3 VIEWS PROVIDED" not in text or "BLENDER" in text.upper()
                    or not all(text.count(label) == 1 for label in labels)
                    or any(label in text for label in ("FRONT\n", "SIDE\n", "DETAIL A", "DETAIL B"))
                    or len(page.get_image_info()) != 3):
                raise ValueError(f"{family}: page must contain three supplied views")
    print(json.dumps({"pdf": str(args.pdf), "pages": len(doc),
                      "step_families": len(selected)}))


if __name__ == "__main__":
    main()
