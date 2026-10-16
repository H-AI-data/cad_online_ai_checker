#!/usr/bin/env python3
"""Replace one family's pages and rebuild the index from the current PDF.

This keeps unrelated family pages from the latest delivered book, which is
important when other drawing edits are happening at the same time.
"""

from __future__ import annotations

import argparse
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import re

import fitz

from build_four_tier_parts import LABELS, cover_page_groups, draw_cover


def family_groups(document):
    groups = OrderedDict()
    for page in document:
        lines = page.get_text().splitlines()
        first = lines[0] if lines else ""
        match = re.match(r"^(FAMILY_\d+)\b", first)
        if not match:
            continue
        family = match.group(1).lower()
        if family not in groups:
            groups[family] = []
        groups[family].append(page.number)
    return groups


def cover_rows(document):
    covers = []
    rows = []
    lookup = {value: key for key, value in LABELS.items()}
    for page in document:
        lines = page.get_text().splitlines()
        if not any("CAD PARTS" in line for line in lines[:2]):
            break
        covers.append(page.number)
        for index, item in enumerate(lines):
            if re.fullmatch(r"FAMILY_\d+", item):
                if index+3 >= len(lines):
                    raise ValueError("Truncated index row")
                kind = lookup.get(lines[index+2])
                if kind is None or not lines[index+1].isdigit():
                    raise ValueError(f"Unknown index row: {item}")
                rows.append({"family": item.lower(), "quantity": int(lines[index+1]),
                             "tier": kind})
    if not covers or len({row["family"] for row in rows}) != len(rows):
        raise ValueError("Invalid or repeated four-tier index")
    return covers, rows


def replace(args):
    original_sha = hashlib.sha256(args.input.read_bytes()).hexdigest()
    with fitz.open(args.input) as source, fitz.open(args.replacement) as replacement:
        replacement_count = len(replacement)
        covers, rows = cover_rows(source)
        groups = family_groups(source)
        if [row["family"] for row in rows] != list(groups):
            raise ValueError("Cover index and family page order differ")
        selected = next((row for row in rows if row["family"] == args.family), None)
        if selected is None or not replacement:
            raise ValueError("Replacement family or pages missing")
        if any(not page.get_text().startswith(args.family.upper())
               for page in replacement):
            raise ValueError("Replacement pages have the wrong family")
        preserve_covers = (selected["tier"] == args.tier and
                           replacement_count == len(groups[args.family]))
        selected["tier"] = args.tier
        cover_batches = cover_page_groups(rows)
        if len(cover_batches) != len(covers):
            raise ValueError("Cover count changed; refusing partial rebuild")
        ranges = {}
        next_page = len(covers)+1
        for row in rows:
            family = row["family"]
            count = len(replacement) if family == args.family else len(groups[family])
            ranges[family] = (next_page, next_page+count-1)
            next_page += count
        output = fitz.open()
        if preserve_covers:
            output.insert_pdf(source, from_page=0, to_page=len(covers)-1)
        else:
            for index, batch in enumerate(cover_batches, start=1):
                draw_cover(output, rows, batch, ranges, args.case, index, len(cover_batches))
        for row in rows:
            family = row["family"]
            if family == args.family:
                output.insert_pdf(replacement)
            else:
                pages = groups[family]
                output.insert_pdf(source, from_page=pages[0], to_page=pages[-1])
        if len(output) != next_page-1:
            raise ValueError("Assembled page count does not match index")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        output.save(args.output, garbage=4, deflate=True)
        output.close()
    audit = {"source_sha256": original_sha, "family": args.family,
             "tier": args.tier, "pages": next_page-1,
             "replacement_pages": replacement_count, "page_range": ranges[args.family],
             "covers_preserved": preserve_covers}
    args.output.with_suffix(".replacement.json").write_text(json.dumps(audit, indent=2))
    print(json.dumps(audit))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--replacement", required=True, type=Path)
    parser.add_argument("--family", required=True)
    parser.add_argument("--tier", required=True, choices=LABELS)
    parser.add_argument("--output", required=True, type=Path)
    replace(parser.parse_args())


if __name__ == "__main__":
    main()
