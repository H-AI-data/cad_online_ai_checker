"""Regression checks for the periodic tooth screen."""

import unittest
from types import SimpleNamespace

import numpy as np

from scan_periodic_radial_profiles import periodic_radial_profile


class SectionMesh:
    def __init__(self, tooth_count=0, tooth_depth=0):
        self.bounds = np.array([[0., 0., 0.], [40., 40., 16.]])
        self.extents = np.diff(self.bounds, axis=0)[0]
        angle = np.linspace(0, 2 * np.pi, 3601)
        radius = 16 + tooth_depth * np.cos(tooth_count * angle)
        self.loop = np.column_stack((20 + radius * np.cos(angle),
                                     20 + radius * np.sin(angle),
                                     np.zeros_like(angle)))

    def section(self, plane_origin, plane_normal):
        return SimpleNamespace(discrete=[self.loop])


class PeriodicRadialProfileTests(unittest.TestCase):
    def test_deep_regular_teeth_are_selected(self):
        result = periodic_radial_profile(SectionMesh(20, 1))
        self.assertIsNotNone(result)
        self.assertEqual([row["peak_count"] for row in result["sections"]], [20, 20])

    def test_smooth_and_shallow_rings_are_excluded(self):
        self.assertIsNone(periodic_radial_profile(SectionMesh()))
        self.assertIsNone(periodic_radial_profile(SectionMesh(20, .1)))


if __name__ == "__main__":
    unittest.main()
