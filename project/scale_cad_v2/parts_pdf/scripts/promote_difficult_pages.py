#!/usr/bin/env python3
"""Replace incomplete medium drawing runs with screened STEP-first pages."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import fitz


FAMILY = re.compile(r"^(FAMILY_\d+)\b")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selected_for_case(path, case):
    rows = json.loads(path.read_text())
    if not isinstance(rows, list):
        raise ValueError("Screen output must be a JSON row array")
    return {row["family"] for row in rows if row["case"] == case}


def family_on_page(page):
    match = FAMILY.match(page.get_text())
    return match.group(1).lower() if match else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--medium-pdf", type=Path, required=True)
    parser.add_argument("--difficult-pdf", type=Path, required=True)
    parser.add_argument("--screen-json", type=Path, required=True)
    parser.add_argument("--step-files-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selected = selected_for_case(args.screen_json, args.case)
    source_hashes = {"medium_pdf": sha256(args.medium_pdf),
                     "difficult_pdf": sha256(args.difficult_pdf),
                     "screen_json": sha256(args.screen_json)}
    with fitz.open(args.medium_pdf) as medium, fitz.open(args.difficult_pdf) as difficult:
        difficult_pages = {}
        for page in difficult:
            family = family_on_page(page)
            if family in selected:
                if family in difficult_pages:
                    raise ValueError(f"Duplicate difficult family page: {family}")
                difficult_pages[family] = page.number
        medium_families = {family_on_page(page) for page in medium}
        promoted = sorted(selected & medium_families)
        for family in promoted:
            if family not in difficult_pages:
                raise ValueError(f"No STEP-first drawing for {family}")
            page = difficult[difficult_pages[family]]
            text = page.get_text()
            if ("3 VIEWS PROVIDED" not in text or
                    f"STEP PROVIDED: provided_part_files/{family}.step" not in text or
                    len(page.get_image_info()) != 3 or
                    not (args.step_files_dir / f"{family}.step").is_file()):
                raise ValueError(f"Invalid STEP-first page or missing STEP: {family}")
        result = fitz.open()
        inserted = set()
        for page in medium:
            family = family_on_page(page)
            if family in promoted:
                if family not in inserted:
                    result.insert_pdf(difficult, from_page=difficult_pages[family],
                                      to_page=difficult_pages[family])
                    inserted.add(family)
            else:
                result.insert_pdf(medium, from_page=page.number,
                                  to_page=page.number)
        if inserted != set(promoted):
            raise ValueError("Promoted family missing from medium book")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        result.set_metadata({"title": f"{args.case} detailed and STEP-first parts"})
        result.save(args.output, garbage=4, deflate=True)
        page_count = len(result)
        result.close()
    audit = {"case": args.case, "promoted_families": promoted,
             "pages": page_count, "sources_sha256": source_hashes}
    args.output.with_suffix(".promotion.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False))
    print(json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
