#!/usr/bin/env python3
"""Geometry regressions for automatic circular-bore key-slot dimensions."""

import unittest

import numpy as np
from shapely.affinity import rotate
from shapely.geometry import Point, box

from keyed_bore_profile import detect_keyed_bore


class _Section:
    def __init__(self, outline):
        self.discrete = [np.column_stack((outline, np.full(len(outline), 13.99)))]


class _SectionMesh:
    def __init__(self, outline):
        self.outline = outline

    def section(self, **_kwargs):
        return _Section(self.outline)


class KeyedBoreProfileTests(unittest.TestCase):
    def setUp(self):
        self.center = (27.5, 27.5)
        self.bore = Point(*self.center).buffer(5, quad_segs=90)
        self.circles = [{"center": [*self.center, 14],
                         "diameter": 10, "axis": [0, 0, 1]}]

    def detect(self, polygon):
        return detect_keyed_bore(
            _SectionMesh(np.asarray(polygon.exterior.coords)),
            self.circles, [55, 55, 14])

    def test_circular_bore_has_no_key_slot(self):
        self.assertIsNone(self.detect(self.bore))

    def test_rotated_key_slots_keep_actual_width_and_radial_depth(self):
        slot = box(self.center[0], self.center[1] - 2.05,
                   self.center[0] + 7.1, self.center[1] + 2.05)
        for angle in (0, 12.2, 90, 180):
            with self.subTest(angle=angle):
                polygon = rotate(self.bore.union(slot), angle, origin=self.center)
                result = self.detect(polygon)
                self.assertIsNotNone(result)
                self.assertAlmostEqual(result["width"], 4.1, places=2)
                self.assertAlmostEqual(result["center_to_floor"], 7.1, places=2)
                self.assertAlmostEqual(result["angle_deg"], angle, places=2)

    def test_pointed_tooth_is_not_mistaken_for_straight_slot(self):
        from shapely.geometry import Polygon

        tooth = Polygon([(31.5, 26.7), (34.8, 27.5), (31.5, 28.3)])
        self.assertIsNone(self.detect(self.bore.union(tooth)))


if __name__ == "__main__":
    unittest.main()
