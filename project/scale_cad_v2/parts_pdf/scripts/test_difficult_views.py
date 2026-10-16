"""STEP pages retain three distinct Blender views in both PDF builders."""

import io
import json
from pathlib import Path
import tempfile
import unittest

import pymupdf
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from build_four_tier_parts import draw_step
from generate_parts_pdf_six_views import STEP_VIEWS, draw_difficult_family_page


class DifficultViewsTest(unittest.TestCase):
    def test_three_views_in_both_page_builders(self):
        with tempfile.TemporaryDirectory() as directory:
            renders = Path(directory)
            family = "family_001"
            family_dir = renders / family
            family_dir.mkdir()
            views = {}
            for index, (name, _) in enumerate(STEP_VIEWS):
                image_name = f"{name}.png"
                if name == "top":
                    image = Image.new("RGBA", (1120, 640), (0, 0, 0, 0))
                    ImageDraw.Draw(image).rectangle((100, 290, 1020, 350),
                                                    fill=(100, 120, 140, 255))
                else:
                    image = Image.new("RGB", (112, 64),
                                      (60 + index * 50, 110, 160))
                image.save(family_dir / image_name)
                views[name] = {"image": image_name}
            (family_dir / "render_manifest.json").write_text(json.dumps({
                "view_version": "upper-opposite-grid-linework-v3", "views": views,
            }))

            buffer = io.BytesIO()
            width, height = landscape(A4)
            pdf = canvas.Canvas(buffer, pagesize=(width, height))
            draw_difficult_family_page(pdf, {"id": family, "quantity": 2},
                                       renders, width, height)
            pdf.save()
            with pymupdf.open(stream=buffer.getvalue(), filetype="pdf") as doc:
                self.assert_step_page(doc[0])

            doc = pymupdf.open()
            draw_step(doc, family, 2, renders)
            self.assert_step_page(doc[0])
            doc.close()

    def assert_step_page(self, page):
        text = page.get_text()
        self.assertIn("STEP PROVIDED: provided_part_files/family_001.step", text)
        self.assertIn("3 VIEWS PROVIDED", text)
        self.assertNotIn("BLENDER", text.upper())
        for _, label in STEP_VIEWS:
            self.assertEqual(text.count(label), 1)
        self.assertEqual(len(page.get_image_info()), 3)
        self.assertTrue(all(info["bbox"][3] < page.rect.height - 25
                            for info in page.get_image_info()))
        top_view = page.get_image_info()[2]["bbox"]
        self.assertGreater(top_view[2] - top_view[0], page.rect.width * .7)


if __name__ == "__main__":
    unittest.main()
