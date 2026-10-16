#!/usr/bin/env python3
"""Replace one existing family page with its supplied STEP/Blender page.

This edits the current drawing set in place conceptually: unrelated family
pages are copied from the input PDF, which preserves later manual corrections.
The caller supplies the matched family and a previously validated single-part
STEP; no case or family identity is embedded in this script.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re

import fitz

from build_four_tier_parts import COLORS, LABELS, draw_step


SUMMARY = re.compile(
    r"(\d+) basic\s*\|\s*(\d+) light\s*\|\s*"
    r"(\d+) detailed\s*\|\s*(\d+) STEP"
)


def replace(args):
    step_file = args.step_files_dir / f"{args.family}.step"
    if not step_file.is_file():
        raise FileNotFoundError(step_file)
    original_sha = hashlib.sha256(args.input.read_bytes()).hexdigest()
    source = fitz.open(args.input)
    original_page_count = len(source)
    family_pages = [page.number for page in source
                    if page.get_text().splitlines()[:1] == [args.family.upper()]]
    if len(family_pages) != 1:
        raise ValueError(f"Expected one primary page for {args.family}: {family_pages}")
    index = family_pages[0]
    old_text = source[index].get_text()
    if old_text.startswith(args.family.upper() + "  |  "):
        raise ValueError("A dimension supplement cannot be replaced alone")
    old_tier = ("light" if LABELS["light"] in old_text else
                "simple" if LABELS["simple"] in old_text else None)
    if old_tier is None:
        raise ValueError("Replacement currently supports one-page basic/light families")
    if any(page.get_text().startswith(args.family.upper() + "  |")
           for page in source):
        raise ValueError("Family also has supplemental pages; adjust page ranges first")
    qty_match = re.search(r"\bQTY\s+(\d+)\b", old_text)
    if not qty_match:
        raise ValueError("Quantity absent from original family page")
    quantity = int(qty_match.group(1))

    covers = []
    index_cover = None
    for page in source:
        if page.number >= index:
            break
        text = page.get_text()
        if "Four-tier drawing set" not in text:
            continue
        summary = SUMMARY.search(text)
        if not summary:
            continue
        covers.append((page.number, summary))
        if page.search_for(args.family.upper()):
            if index_cover is not None:
                raise ValueError("Family occurs in more than one index page")
            index_cover = page.number
    if index_cover is None or not covers:
        raise ValueError("Could not locate the family in the drawing-set index")

    counter_index = {"simple": 0, "light": 1}[old_tier]
    for page_number, summary in covers:
        counts = [int(value) for value in summary.groups()]
        if counts[counter_index] <= 0:
            raise ValueError("Cover tier count is inconsistent")
        counts[counter_index] -= 1
        counts[3] += 1
        page = source[page_number]
        page.add_redact_annot(fitz.Rect(30, 103, 650, 121), fill=(1, 1, 1))
        page.apply_redactions(images=0, graphics=0, text=0)
        label = (f"{counts[0]} basic   |   {counts[1]} light   |   "
                 f"{counts[2]} detailed   |   {counts[3]} STEP")
        page.insert_text((31, 116), label, fontsize=10, fontname="hebo")

    page = source[index_cover]
    matches = page.search_for(args.family.upper())
    if len(matches) != 1:
        raise ValueError("Index row is ambiguous")
    family_rect = matches[0]
    baseline = family_rect.y1 - 2.456
    row_index = round((baseline - 161) / 18)
    background = (.96, .97, .98) if row_index % 2 == 0 else (1, 1, 1)
    page.add_redact_annot(fitz.Rect(236, baseline - 13, 704, baseline + 4),
                          fill=background)
    page.apply_redactions(images=0, graphics=0, text=0)
    page.insert_text((237, baseline), LABELS["step"], fontsize=8,
                     fontname="hebo", color=COLORS["step"])

    replacement = fitz.open()
    draw_step(replacement, args.family, quantity, args.renders)
    source.delete_page(index)
    source.insert_pdf(replacement, from_page=0, to_page=0, start_at=index)
    if len(source) != original_page_count:
        raise ValueError("Page count changed during single-page replacement")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    source.save(args.output, garbage=4, deflate=True)
    source.close()
    replacement.close()
    print({"family": args.family, "page": index + 1,
           "source_sha256": original_sha, "output": str(args.output)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--family", required=True)
    parser.add_argument("--step-files-dir", type=Path, required=True)
    parser.add_argument("--renders", type=Path, required=True)
    replace(parser.parse_args())


if __name__ == "__main__":
    main()
