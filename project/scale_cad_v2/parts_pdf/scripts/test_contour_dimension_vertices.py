"""Regression checks for faceted arcs in planar-profile dimension chains."""

import math
import unittest

from general_dimension_engine import contour_dimension_vertices


class ContourDimensionVerticesTest(unittest.TestCase):
    def test_sampled_quarter_fillet_keeps_only_tangencies(self):
        arc = [(101 + 3 * math.cos(angle), 15 + 3 * math.sin(angle))
               for angle in [-(math.pi / 2) * i / 23 for i in range(24)]]
        polygon = [(116, 0), (116, 50), (104, 50), *arc,
                   (0, 12), (0, 0)]
        vertices, arcs = contour_dimension_vertices(polygon, [116, 50, 12])
        self.assertEqual(len(vertices), 7)
        self.assertEqual(len(arcs), 1)
        self.assertAlmostEqual(arcs[0]["radius"], 3, delta=.01)
        self.assertEqual({round(x, 3) for x, _ in vertices}, {0, 101, 104, 116})
        self.assertEqual({round(y, 3) for _, y in vertices}, {0, 12, 15, 50})

    def test_alternating_small_teeth_are_not_a_circular_fillet(self):
        teeth = [(i * .3, 12 + (.2 if i % 2 else 0)) for i in range(13)]
        polygon = [(0, 0), (6, 0), (6, 12), *reversed(teeth)]
        vertices, arcs = contour_dimension_vertices(polygon, [6, 12, 2])
        self.assertEqual(len(vertices), len(polygon))
        self.assertEqual(arcs, [])


if __name__ == "__main__":
    unittest.main()
