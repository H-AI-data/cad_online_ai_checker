"""Regression for audited medium pages that contain no local dimensions."""

import json
from pathlib import Path
import tempfile
import unittest

import pymupdf

from build_parts import promote_unannotated_medium
from dimension_density import simplify_dimension_stations


class MediumCoveragePromotionTests(unittest.TestCase):
    def test_three_visible_submillimetre_dimensions_promote(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = root / "candidates.json"
            medium = root / "medium.pdf"
            families = ["family_alpha", "family_beta"]
            candidates.write_text(json.dumps([{"family": fid} for fid in families]))
            medium.with_suffix(".audit.json").write_text(json.dumps([
                {"family": fid, "geometry_status": "geometry_certified",
                 "supplement_pages": 1, "step_available": False}
                for fid in families]))
            with pymupdf.open() as document:
                for family, labels in zip(families,
                                          [["0.3", "0.4", "0.5", "0.1"],
                                           ["0.3", "0.4", "0.2"]]):
                    page = document.new_page()
                    page.insert_text((30, 30), family.upper())
                    for index, label in enumerate(labels):
                        page.insert_text((30, 60 + index * 20), label,
                                         color=(.78, .13, .18))
                document.save(medium)
            self.assertEqual(promote_unannotated_medium(candidates, medium),
                             [families[0]])
            self.assertEqual(simplify_dimension_stations([0, .1, .4, 1]),
                             [0.0, .4, 1.0])
            self.assertEqual(simplify_dimension_stations([0, .2, 1]),
                             [0.0, 1.0])

    def test_promotes_only_uncertified_page_without_supplement(self):
        families = ["family_alpha", "family_beta", "family_gamma"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = root / "candidates.json"
            medium = root / "medium.pdf"
            candidates.write_text(json.dumps(
                [{"family": family} for family in families]))
            audit = [
                {"family": families[0], "geometry_status": "step_required",
                 "supplement_pages": 0, "additional_projection_pages": 0,
                 "step_available": False},
                {"family": families[1], "geometry_status": "step_required",
                 "supplement_pages": 1, "additional_projection_pages": 0,
                 "step_available": False},
                {"family": families[2], "geometry_status": "geometry_certified",
                 "supplement_pages": 0, "additional_projection_pages": 0,
                 "step_available": False},
            ]
            medium.with_suffix(".audit.json").write_text(json.dumps(audit))
            with pymupdf.open() as document:
                for family in families:
                    page = document.new_page()
                    page.insert_text((30, 30), family.upper())
                document.save(medium)
            self.assertEqual(
                promote_unannotated_medium(candidates, medium),
                [families[0]],
            )
            with pymupdf.open(medium) as document:
                self.assertEqual(len(document), 2)
                self.assertEqual(
                    [page.get_text().splitlines()[0].lower()
                     for page in document], families[1:])
            self.assertEqual(
                [row["family"] for row in json.loads(candidates.read_text())],
                families[1:],
            )


if __name__ == "__main__":
    unittest.main()
