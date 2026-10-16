"""Regression checks for additive chains and crowded labels (no CAD runtime needed)."""
import unittest
from unittest.mock import patch
from reportlab.pdfbase import pdfmetrics
import drawing as d


class DiameterViewTests(unittest.TestCase):
    def test_axis_and_reversed_normal_select_only_face_on_view(self):
        views=[(1,2),(0,2),(0,1)]
        for normal, face_view in enumerate(views):
            for sign in (-1,1):
                axis=[0,0,0];axis[normal]=sign
                for view in views:
                    self.assertEqual(d.circle_faces_view({'axis':axis},view),view==face_view)

    def test_ellipse_and_flattened_circle_are_not_diameter_candidates(self):
        self.assertFalse(d.circle_faces_view({'curve_kind':'ellipse','axis':[0,0,1]},(0,1)))
        self.assertFalse(d.circle_faces_view({'axis':[1,0,.01]},(0,1)))


class ChainTests(unittest.TestCase):
    def chain(self, stations):
        parts = [dict(paths=[[[x, 0, 0], [x, 1, 0]]]) for x in stations]
        labels = []
        d.log.clear()
        with patch.object(d, 'line'), patch.object(d, 'dot'), patch.object(d, 'text'), \
             patch.object(d, 'label', side_effect=lambda x, y, t: labels.append((x, t))), \
             patch.object(d.pdfmetrics, 'stringWidth', side_effect=lambda s, font, size: len(s)*8):
            d.small_chain(parts, (0, 1), lambda p: (p[0], p[1]), 965)
        return list(d.log), labels

    def test_crowded_chain_merges_and_preserves_total(self):
        stations = list(range(0, 401, 10))
        dims, labels = self.chain(stations)
        self.assertLess(len(dims), len(stations)-1)
        self.assertGreater(len(dims), 1)
        self.assertAlmostEqual(sum(v['mm'] for v in dims), 400)
        for dim, (mid, label) in zip(dims, labels):
            self.assertIn(dim['start'][0], stations)
            self.assertIn(dim['end'][0], stations)
            self.assertGreaterEqual(dim['mm'], len(label)*8+d.CHAIN_PADDING)
            self.assertAlmostEqual(mid, (dim['start'][0]+dim['end'][0])/2)

    def test_sparse_chain_keeps_stations(self):
        dims, _ = self.chain([0, 100, 200, 300])
        self.assertEqual([v['mm'] for v in dims], [100, 100, 100])

    def test_detailed_short_segments_stagger_without_merging(self):
        parts = [dict(id=str(i), paths=[[[x, 0, 0], [x, 1, 0]]])
                 for i, x in enumerate([0, 1, 2, 3])]
        d.log.clear(); d.dimensions.clear(); d.leaders.clear(); d.routing.clear()
        panel = {'chains': [{'axis': 0, 'parts': [str(i) for i in range(4)]}],
                 'diameter_parts': [], 'max_diameters': 0}
        d.detailed_dimensions(parts, (0, 1), lambda p: (500+p[0]*25, 500+p[1]), panel)
        self.assertEqual([v['mm'] for v in d.log], [1, 1, 1])
        with patch.object(d, 'line'), patch.object(d, 'dot'), patch.object(d, 'label'), \
             patch.object(d.pdfmetrics, 'stringWidth', return_value=40):
            d.render_annotations()
        self.assertEqual(len({v['rail'] for v in d.routing}), 3)
        self.assertEqual([v['start'][0] for v in d.log], [0, 1, 2])

    def test_detail_chain_only_appears_in_containing_view(self):
        parts = [dict(id='a', paths=[[[0, 0, 0], [10, 0, 0]]])]
        d.log.clear(); d.dimensions.clear(); d.leaders.clear()
        d.detailed_dimensions(parts, (1, 2), lambda p: (p[1], p[2]),
                              {'chains': [{'axis': 0, 'parts': ['a']}],
                               'diameter_parts': [], 'max_diameters': 0})
        self.assertEqual(d.log, [])

    def test_one_percent_filter_preserves_boundary(self):
        parts=[dict(id=str(i),size=[100,10,10],paths=[[[x,0,0],[x,1,0]]])
               for i,x in enumerate([0,.5,1.5,3.5])]
        d.log.clear();d.dimensions.clear();d.leaders.clear()
        d.detailed_dimensions(parts,(0,1),lambda p:(p[0]*100,p[1]*100),
            {'chains':[{'axis':0,'parts':[str(i) for i in range(4)]}],
             'diameter_parts':[]})
        self.assertEqual([v['mm'] for v in d.log],[1,2])
        self.assertTrue(all(v['minimum_mm']==1 for v in d.log))

    def test_crop_does_not_dimension_off_page_endpoints(self):
        parts=[dict(id='a',size=[100,10,10],paths=[[[0,0,0],[100,0,0]]])]
        d.log.clear();d.dimensions.clear();d.leaders.clear()
        d.detailed_dimensions(parts,(0,1),lambda p:(p[0]*10,p[1]*10),
            {'chains':[{'axis':0,'parts':['a']}], 'diameter_parts':[],
             'crop':[[0,-1],[50,1]]})
        self.assertEqual(d.log,[])


if __name__ == '__main__':
    unittest.main()
