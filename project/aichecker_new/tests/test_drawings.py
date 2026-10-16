from pathlib import Path
import tempfile
import unittest


class DrawingTests(unittest.TestCase):
    def test_pdf_render_and_limit_preserve_uncertainty(self):
        import pymupdf
        from common.drawings import render_drawing
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'a.pdf'
            doc=pymupdf.open()
            for _ in range(2):
                page=doc.new_page();page.insert_text((30,30),'PART DRAWING')
            doc.save(source);doc.close()
            report=render_drawing(source,root/'images',1)
            self.assertFalse(report['complete'])
            self.assertEqual(len(report['images']),1)
            self.assertTrue(Path(report['images'][0]).is_file())

    def test_dxf_keeps_paper_layout(self):
        import ezdxf
        from common.drawings import render_drawing
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'a.dxf'
            doc=ezdxf.new();doc.layouts.get('Layout1').add_circle((0,0),10);doc.saveas(source)
            report=render_drawing(source,root/'images',10)
            self.assertTrue(report['complete'])
            self.assertEqual(report['pages'][-1]['layout'],'Layout1')

    def test_dxf_nested_external_image_is_incomplete(self):
        import ezdxf
        from common.drawings import render_drawing
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'a.dxf'
            doc=ezdxf.new()
            image=doc.add_image_def(filename='missing-external.png',size_in_pixel=(100,100))
            inner=doc.blocks.new('INNER');inner.add_image(image,insert=(0,0),size_in_units=(10,10))
            outer=doc.blocks.new('OUTER');outer.add_blockref('INNER',(0,0))
            doc.modelspace().add_blockref('OUTER',(0,0))
            doc.modelspace().add_circle((20,20),10);doc.saveas(source)
            report=render_drawing(source,root/'images',10)
            self.assertFalse(report['complete'])
            self.assertTrue(report['warnings'])


if __name__=='__main__':
    unittest.main()
