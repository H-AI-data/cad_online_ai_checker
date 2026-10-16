"""Shape-based regression checks for repeated notches in a closed strip."""

import unittest

import numpy as np

from scan_repeated_edge_notches import repeated_inner_edge_notches


class Section:
    def __init__(self, paths):
        self.discrete = paths


class StripMesh:
    bounds = np.array([[0., 0., 0.], [140., 35., 10.]])
    extents = bounds[1] - bounds[0]

    def __init__(self, centers=()):
        self.centers = centers

    def section(self, plane_origin, plane_normal):
        outer = np.array([[0., 0., 0.], [70., 0., 0.], [140., 0., 0.],
                          [140., 17.5, 0.], [140., 35., 0.],
                          [70., 35., 0.], [0., 35., 0.],
                          [0., 17.5, 0.], [0., 0., 0.]])
        x = np.linspace(1.5, 138.5, 1371)
        top = np.full_like(x, 33.5)
        for center in self.centers:
            top -= 1.9 * np.maximum(0, 1 - abs(x - center) / 1.6)
        inner = np.vstack([
            [1.5, 1.5, 0.], [138.5, 1.5, 0.],
            np.column_stack([x[::-1], top[::-1], np.zeros(len(x))]),
            [1.5, 1.5, 0.],
        ])
        return Section([outer, inner])


class RepeatedEdgeNotchTest(unittest.TestCase):
    def test_evenly_spaced_inner_notches(self):
        result = repeated_inner_edge_notches(StripMesh([95, 100, 105, 110, 115]))
        self.assertIsNotNone(result)
        self.assertEqual(result["notch_count"], 5)
        self.assertAlmostEqual(result["pitch_mm"], 5, delta=.1)

    def test_smooth_or_irregular_strip(self):
        self.assertIsNone(repeated_inner_edge_notches(StripMesh()))
        self.assertIsNone(repeated_inner_edge_notches(StripMesh([60, 73, 88, 112])))


if __name__ == "__main__":
    unittest.main()
