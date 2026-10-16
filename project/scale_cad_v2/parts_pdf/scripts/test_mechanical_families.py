"""Behavioral regression checks for seven-metric family grouping."""
import copy
import unittest
import numpy as np
import trimesh
from mechanical_families import mechanical_metrics, same_mechanical_family, group_mechanical_metrics, CONTINUOUS_METRICS, drawing_families


class MechanicalFamiliesTest(unittest.TestCase):
    def setUp(self):
        self.mesh = trimesh.creation.box(extents=[3., 7., 11.])
        self.metrics = mechanical_metrics(self.mesh)

    def test_rotation_translation_and_different_world_boxes_merge(self):
        rotated = self.mesh.copy()
        rotated.apply_transform(trimesh.transformations.rotation_matrix(.731, [1, 2, 3]))
        rotated.apply_translation([1200, -800, 150])
        self.assertFalse(np.allclose(self.mesh.extents, rotated.extents))
        self.assertTrue(same_mechanical_family(self.metrics, mechanical_metrics(rotated)))

    def test_each_metric_can_reject_independently(self):
        for key in CONTINUOUS_METRICS:
            other = copy.deepcopy(self.metrics)
            if key == 'principal_inertia':
                other[key][1] *= 1.011
            else:
                other[key] *= 1.011
            with self.subTest(key=key):
                self.assertFalse(same_mechanical_family(self.metrics, other))
        for key in ['watertight', 'euler_number']:
            other = copy.deepcopy(self.metrics)
            other[key] = not other[key] if key == 'watertight' else other[key] + 1
            self.assertFalse(same_mechanical_family(self.metrics, other))

    def test_tessellation_sensitive_curvature_does_not_split_a_family(self):
        other = copy.deepcopy(self.metrics)
        other['integral_mean_curvature'] *= 1.5
        self.assertTrue(same_mechanical_family(self.metrics, other))
        self.assertEqual(group_mechanical_metrics([self.metrics, other]), [[0, 1]])

    def test_threshold_is_inclusive_symmetric_and_zero_safe(self):
        a = copy.deepcopy(self.metrics); b = copy.deepcopy(a)
        a['volume'] = 100.; b['volume'] = 101.
        self.assertTrue(same_mechanical_family(a, b))
        self.assertTrue(same_mechanical_family(b, a))
        b['volume'] = 101.000001
        self.assertFalse(same_mechanical_family(a, b))
        a['volume'] = b['volume'] = 0.
        self.assertTrue(same_mechanical_family(a, b))
        b['volume'] = 1e-20
        self.assertFalse(same_mechanical_family(a, b))
        b['volume'] = float('nan')
        self.assertFalse(same_mechanical_family(a, b))

    def test_no_transitive_drift(self):
        values = [copy.deepcopy(self.metrics) for _ in range(3)]
        for m, v in zip(values, [100., 100.75, 101.5]): m['volume'] = v
        self.assertEqual(group_mechanical_metrics(values), [[0, 1], [2]])

    def test_different_shapes_with_same_bbox_do_not_merge(self):
        sphere = trimesh.creation.icosphere(subdivisions=2)
        box = trimesh.creation.box(extents=sphere.extents)
        self.assertFalse(same_mechanical_family(mechanical_metrics(sphere), mechanical_metrics(box)))

    def test_legacy_spec_requires_reextraction(self):
        with self.assertRaisesRegex(ValueError, 'Regenerate'):
            drawing_families({'families': []})


if __name__ == '__main__': unittest.main()
