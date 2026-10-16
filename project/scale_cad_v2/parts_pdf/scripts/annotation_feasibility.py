"""Lightweight evidence that a part drawing is likely to be hard to annotate.

This is a screening heuristic, not a proof that a dimensional drawing cannot
be made.  It uses the existing analytic-circle cache and medium-screen metrics;
it does not reconstruct volume or render any new views.  No case/family IDs
participate in the decision.
"""

from __future__ import annotations

from collections import Counter
import math
import re

import fitz

from general_dimension_engine import grouped_circles


def projection_collisions(circles):
    """Count coincident projected features at different depths, per view."""
    counts = Counter()
    for normal, name in enumerate(("side", "front", "top")):
        u, v = ((1, 2), (0, 2), (0, 1))[normal]
        for index, left in enumerate(circles):
            for right in circles[index + 1:]:
                a, b = left["center"], right["center"]
                depth = abs(a[normal] - b[normal])
                if depth < max(.5, .1 * min(left["diameter"], right["diameter"])):
                    continue
                planar = math.hypot(a[u] - b[u], a[v] - b[v])
                if planar < .6 * min(left["diameter"], right["diameter"]):
                    counts[name] += 1
    return dict(counts)


def screen(feature, medium_row):
    """Return transparent, general STEP-priority evidence for one family."""
    circles = grouped_circles(feature["circles"])
    count = len(circles)
    diameters = len({round(item["diameter"], 2) for item in circles})
    oblique = int(sum(max(abs(value) for value in item["axis"]) < .985
                      for item in circles))
    collisions = projection_collisions(circles)
    outline = medium_row.get("thin_outline") or {}
    profile = medium_row.get("long_axis_profile") or {}
    reasons = []
    if count >= 14 and diameters >= 4:
        reasons.append("dense_dimension_load")
    if count >= 8 and oblique >= 4:
        reasons.append("oblique_features")
    if count >= 8 and diameters >= 3 and max(collisions.values(), default=0) >= 5:
        reasons.append("projected_feature_overlap")
    if ((outline.get("max_reflex", 0) >= 8 or
         profile.get("median_reflex", 0) >= 8) and
            (count >= 6 or diameters >= 3)):
        reasons.append("irregular_profile_with_features")
    return {
        "risk_reasons": reasons,
        "evidence": {
            "cylindrical_groups": count,
            "diameter_types": diameters,
            "oblique_groups": oblique,
            "projected_collisions": collisions,
            "outline_reflex": int(outline.get("max_reflex", 0)),
            "section_reflex": float(profile.get("median_reflex", 0)),
        },
    }


def assess_drawing(risk, plan, audit, pages):
    """Promote only when the existing drawing has concrete annotation gaps.

    Geometry risk alone never triggers STEP priority.  The inexpensive PDF
    checks look for missing analytic diameters, omitted oblique groups, severe
    text collisions, too few dimension values, or no alternate view for a
    profile whose medium-screen geometry is irregular.
    """
    texts = [page.get_text() for page in pages]
    all_text = "\n".join(texts)
    expected = {round(item["diameter"], 1)
                for item in plan["circular_coverage"]["all_features"]}
    shown = [float(value) for value in
             re.findall(r"Ø\s*(\d+(?:\.\d+)?)", all_text)]
    missing = sorted(value for value in expected
                     if not any(abs(value - displayed) <= .11 for displayed in shown))
    label_count = 0
    collisions = 0
    for page in pages[1:]:
        labels = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["color"] != 0xc7212e or not re.search(r"\d", span["text"]):
                        continue
                    if "STEP PROVIDED" in span["text"]:
                        continue
                    labels.append(fitz.Rect(span["bbox"]))
        label_count += len(labels)
        for i, left in enumerate(labels):
            for right in labels[i + 1:]:
                overlap = left & right
                if (not overlap.is_empty and
                        overlap.get_area() > .2 * min(left.get_area(), right.get_area())):
                    collisions += 1
    circles = len(plan["circular_coverage"]["all_features"])
    diameters = len(expected)
    minimum_labels = min(12, max(4, math.ceil(circles / 2) + diameters))
    poor_quantity = label_count < minimum_labels
    has_alt_view = any(marker in all_text for marker in (
        "SIDE  |", "FRONT AXIAL", "FRONT ADDITIONAL", "SIDE ADDITIONAL"))
    irregular_needs_view = (
        "irregular_profile_with_features" in risk["risk_reasons"] and
        not has_alt_view)
    oblique_unplotted = audit["oblique_cylindrical_groups_in_step"]
    reasons = []
    if len(missing) >= 2 or (missing and risk["risk_reasons"]):
        reasons.append("missing_analytic_diameters")
    if oblique_unplotted >= 2:
        reasons.append("oblique_features_without_dimension_view")
    if collisions >= 2:
        reasons.append("dimension_text_collision")
    if risk["risk_reasons"] and poor_quantity:
        reasons.append("insufficient_dimension_labels")
    if irregular_needs_view:
        reasons.append("irregular_profile_without_alternate_view")
    return {
        "step_priority": bool(reasons),
        "reasons": reasons,
        "drawing_evidence": {
            "missing_diameters_mm": missing,
            "oblique_groups_without_view": oblique_unplotted,
            "dimension_label_count": label_count,
            "minimum_label_screen": minimum_labels,
            "text_collisions": collisions,
            "has_alternate_view": has_alt_view,
        },
    }
