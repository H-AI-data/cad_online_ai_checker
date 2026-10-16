"""Regression: large family indexes must remain above the page footer."""
import io
import unittest
import pymupdf
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas
from generate_parts_pdf_six_views import draw_cover, draw_index_continuation, index_page_groups
from generate_parts_pdf import draw_cover as draw_legacy_cover
from build_four_tier_parts import cover_page_groups, draw_cover as draw_four_tier_cover


class CoverLayoutTest(unittest.TestCase):
    def test_four_tier_index_paginates_and_keeps_drawing_references(self):
        rows = [dict(family=f'family_{i:03}', quantity=1, tier='light')
                for i in range(1, 96)]
        groups = cover_page_groups(rows)
        self.assertGreater(len(groups), 1)
        ranges = {row['family']: (len(groups) + index + 1,
                                  len(groups) + index + 1)
                  for index, row in enumerate(rows)}
        doc = pymupdf.open()
        for number, group in enumerate(groups, start=1):
            draw_four_tier_cover(doc, rows, group, ranges, 'generic',
                                 number, len(groups))
        self.assertEqual(len(doc), len(groups))
        entries = []
        for page in doc:
            spans = [span for block in page.get_text('dict')['blocks']
                     for line in block.get('lines', []) for span in line['spans']]
            page_entries = [span for span in spans if span['text'].startswith('FAMILY_')]
            self.assertTrue(all(span['bbox'][3] < 550 for span in page_entries))
            entries.extend(span['text'] for span in page_entries)
        self.assertEqual(entries, [row['family'].upper() for row in rows])
        self.assertIn(str(len(groups) + 1), doc[0].get_text())
        doc.close()

    def test_four_tier_small_index_stays_on_one_page(self):
        self.assertEqual(len(cover_page_groups([{}] * 21)), 1)

    def test_legacy_cover_index_paginates_without_clipping(self):
        families = [dict(id=f'family_{i:03}', quantity=1,
                         occurrence_ids=[f'occurrence_{i:03}'])
                    for i in range(1, 96)]
        dimensions = {f'occurrence_{i:03}': (10., 8., 2.)
                      for i in range(1, 96)}
        width, height = landscape(A4)
        table_y = height - 287
        max_rows = int((table_y - 35) // 14) + 1
        page_size = 3 * max_rows
        groups = [families[start:start + page_size]
                  for start in range(0, len(families), page_size)]
        output = io.BytesIO()
        pdf = canvas.Canvas(output, pagesize=(width, height))
        for group in groups:
            draw_legacy_cover(pdf, families, dimensions, width, height,
                              'GENERIC ASSEMBLY', group)
            pdf.showPage()
        pdf.save()
        with pymupdf.open(stream=output.getvalue(), filetype='pdf') as doc:
            self.assertEqual(len(doc), 2)
            entries = []
            for page in doc:
                spans = [span for block in page.get_text('dict')['blocks']
                         for line in block.get('lines', []) for span in line['spans']]
                page_entries = [span for span in spans if span['text'].startswith('FAMILY_')]
                self.assertTrue(all(span['bbox'][3] < height - 35
                                    for span in page_entries))
                entries.extend(page_entries)
            self.assertEqual({span['text'] for span in entries},
                             {family['id'].upper() for family in families})

    def test_seventy_families_fit_above_footer(self):
        families = [dict(id=f'family_{i:03}', quantity=1, show_depth_views=True)
                    for i in range(1, 71)]
        width, height = landscape(A4)
        output = io.BytesIO()
        pdf = canvas.Canvas(output, pagesize=(width, height))
        draw_cover(pdf, families, families, width, height, 'LARGE ASSEMBLY')
        pdf.showPage()
        pdf.save()
        with pymupdf.open(stream=output.getvalue(), filetype='pdf') as doc:
            spans = [span for block in doc[0].get_text('dict')['blocks']
                     for line in block.get('lines', []) for span in line['spans']]
            entries = [s for s in spans if s['text'].startswith('FAMILY_')]
            self.assertEqual({s['text'] for s in entries},
                             {f['id'].upper() for f in families})
            self.assertTrue(all(s['bbox'][3] < height - 35 for s in entries))

    def test_ninety_five_families_use_two_legible_index_pages(self):
        families = [dict(id=f'family_{i:03}', quantity=1, show_depth_views=True)
                    for i in range(1, 96)]
        width, height = landscape(A4)
        groups = index_page_groups(families, height)
        self.assertEqual(len(groups), 2)
        output = io.BytesIO()
        pdf = canvas.Canvas(output, pagesize=(width, height))
        draw_cover(pdf, families, groups[0], width, height, 'LARGE ASSEMBLY')
        pdf.showPage()
        draw_index_continuation(pdf, groups[1], width, height,
                                len(groups[0]) + 1, len(families))
        pdf.showPage()
        pdf.save()
        with pymupdf.open(stream=output.getvalue(), filetype='pdf') as doc:
            entries = []
            for page in doc:
                spans = [span for block in page.get_text('dict')['blocks']
                         for line in block.get('lines', []) for span in line['spans']]
                entries.extend(s for s in spans if s['text'].startswith('FAMILY_'))
                self.assertTrue(all(s['bbox'][3] < height - 35 for s in entries))
            self.assertEqual([s['text'] for s in entries],
                             [f['id'].upper() for f in families])
            self.assertIn('7 VIEWS | 1 PAGE', doc[1].get_text())


if __name__ == '__main__':
    unittest.main()
