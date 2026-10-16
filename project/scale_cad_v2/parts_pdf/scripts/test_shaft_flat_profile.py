"""Geometry regression for a short flat cut into a round shaft."""

import unittest

import numpy as np
import trimesh

from shaft_flat_profile import milled_shaft_flats


def planar_patch(width):
    normal = np.array([0., np.sqrt(3)/2, -.5])
    tangent = np.array([0., .5, np.sqrt(3)/2])
    center = np.array([14.5, 4., 4.]) + 1.5 * normal
    vertices = np.array([
        center + [-2., 0., 0.] - tangent * width/2,
        center + [-2., 0., 0.] + tangent * width/2,
        center + [2., 0., 0.] + tangent * width/2,
        center + [2., 0., 0.] - tangent * width/2,
    ])
    return trimesh.Trimesh(vertices=vertices, faces=[[0, 1, 2], [0, 2, 3]],
                           process=False)


class ShaftFlatProfileTests(unittest.TestCase):
    def setUp(self):
        self.circles = [{
            "axis": [1, 0, 0], "center": [10, 4, 4],
            "diameter": 5, "axial_levels": [3, 17.5],
        }]

    def test_local_broad_flat_has_length_width_and_depth(self):
        flats = milled_shaft_flats(planar_patch(4), self.circles, [18, 8, 8])
        self.assertEqual(len(flats), 1)
        self.assertAlmostEqual(flats[0]["length"], 4)
        self.assertAlmostEqual(flats[0]["width"], 4)
        self.assertAlmostEqual(flats[0]["depth"], 1)

    def test_narrow_round_wall_facet_is_excluded(self):
        self.assertEqual(
            milled_shaft_flats(planar_patch(.2), self.circles, [18, 8, 8]),
            [],
        )


if __name__ == "__main__":
    unittest.main()
