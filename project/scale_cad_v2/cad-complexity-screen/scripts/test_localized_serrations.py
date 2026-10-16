"""Geometry regression for one-sided end serrations."""

import unittest

import numpy as np
import trimesh

from scan_localized_serrations import localized_end_serrations


def toothed_end():
    vertices, faces = [], []
    for row in range(8):
        for column in range(8):
            first = len(vertices)
            y, z = row * 1.25, column * 1.25
            vertices.extend([
                [0, y, z], [0, y + 1.25, z],
                [0, y + 1.25, z + 1.25], [0, y, z + 1.25],
                [-.7, y + .625, z + .625],
            ])
            faces.extend([[first + index, first + (index + 1) % 4,
                           first + 4] for index in range(4)])
    teeth = trimesh.Trimesh(vertices=np.asarray(vertices),
                            faces=np.asarray(faces), process=False)
    body = trimesh.creation.box(
        extents=(5, 10, 10),
        transform=trimesh.transformations.translation_matrix((2.5, 5, 5)))
    return trimesh.util.concatenate([body, teeth])


class LocalizedSerrationTests(unittest.TestCase):
    def test_detects_one_sided_interior_teeth(self):
        result = localized_end_serrations(toothed_end())
        self.assertIsNotNone(result)
        self.assertEqual((result["axis"], result["end"]), (0, "min"))

    def test_plain_box_has_no_serration(self):
        self.assertIsNone(localized_end_serrations(
            trimesh.creation.box(extents=(5, 10, 10))))


if __name__ == "__main__":
    unittest.main()
