"""Screen scripted detail pages for crowded, measurable submillimetre callouts.

This is a page-level quality gate, independent of the cause of the short
segments. It reads only red numerical dimension labels, so notes, coordinates,
and the model illustration cannot trigger a STEP handoff.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re

import fitz


MIN_DRAWN_DIMENSION_MM = 0.2
SUBMILLIMETRE_LIMIT_MM = 1.0
MIN_SUBMILLIMETRE_LABELS = 3
_NUMBER = re.compile(r"^(?:(?:\d+)x\s*)?(?:R|Ø)?\s*(\d+(?:\.\d+)?)$")
_FAMILY = re.compile(r"^FAMILY_[A-Za-z0-9]+\b")


def simplify_dimension_stations(stations, minimum=MIN_DRAWN_DIMENSION_MM):
    """Drop internal chain stations that create segments below the label floor."""
    values = sorted(set(float(value) for value in stations))
    if len(values) < 2 or values[-1] - values[0] <= minimum + 1e-9:
        return []
    result = [values[0]]
    for value in values[1:-1]:
        if (value - result[-1] > minimum + 1e-9
                and values[-1] - value > minimum + 1e-9):
            result.append(value)
    result.append(values[-1])
    return result


def _red_dimension_values(page):
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                color = int(span["color"])
                red, green, blue = (color >> 16) & 255, (color >> 8) & 255, color & 255
                if red <= 1.6 * green or red <= 1.2 * blue:
                    continue
                match = _NUMBER.fullmatch(span["text"].strip())
                if match:
                    yield float(match.group(1))


def scan_medium_dimension_density(path: Path):
    """Return family counts and the automatic STEP decision for one detail PDF."""
    values = defaultdict(list)
    with fitz.open(path) as document:
        for page in document:
            headings = {match.group(0).lower()
                        for line in page.get_text().splitlines()
                        if (match := _FAMILY.match(line))}
            if len(headings) != 1:
                raise ValueError(f"Unidentified medium detail page: {page.number + 1}")
            values[headings.pop()].extend(_red_dimension_values(page))
    results = {}
    for family, measured in values.items():
        drawn = [value for value in measured if value > MIN_DRAWN_DIMENSION_MM]
        crowded = [value for value in drawn if value < SUBMILLIMETRE_LIMIT_MM]
        share = len(crowded) / len(drawn) if drawn else 0.0
        results[family] = {
            "dimension_labels_above_0_2mm": len(drawn),
            "submillimetre_labels": len(crowded),
            "submillimetre_share": round(share, 4),
            "step_required_for_density": len(crowded) >= MIN_SUBMILLIMETRE_LABELS,
        }
    return results
