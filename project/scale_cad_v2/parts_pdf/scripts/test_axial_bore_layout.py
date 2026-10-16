"""Geometry and page-space regression checks for inline bore-depth labels."""

import fitz

from build_medium_detail_book import (plan_grouped_axial_bores_on_projection,
                                      projected_planar_face_stations)
import numpy as np


def groups(axis=0):
    positions = (50., 85., 195., 210.)
    depths = (10., 1.4, 18., 30.)
    result = []
    for index, (position, depth) in enumerate(zip(positions, depths)):
        top = index != 0
        low, high = ((0., depth) if not top else (40. - depth, 40.))
        center = [0., 0.]
        center[axis] = position
        result.append(dict(side="top" if top else "bottom",
                           diameter=(18., 10., 10., 10.5)[index],
                           depth=depth, low=low, high=high,
                           count=2, centers=[center]))
    return result


def check():
    document = fitz.open()
    page = document.new_page(width=1190, height=900)
    page.insert_text((150, 150), "FRONT", fontsize=20)
    view = fitz.Rect(135, 117, 1095, 632)
    feature = {"family": "synthetic", "dimensions": [260., 260., 40.]}
    project = lambda station, z: (250. + 3. * station, 430. - 3. * z)
    layout = plan_grouped_axial_bores_on_projection(
        page, project, feature, groups(), 0, view)
    assert layout is not None and len(layout) == len(groups())
    title = fitz.Rect(page.get_text("blocks")[0][:4])
    assert all(not item["box"].intersects(title) for item in layout)
    assert all(item["box"].y1 < project(0, 40.)[1]
               for item in layout if item["side"] == "top")
    assert all(item["box"].y0 > project(0, 0.)[1]
               for item in layout if item["side"] == "bottom")
    recessed = dict(side="top", diameter=5., depth=4.5, low=63.,
                    high=67.5, entry_z=67.5, count=1,
                    centers=[[130., 50.]])
    tall = {**feature, "dimensions": [260., 100., 80.]}
    upper_project = lambda station, z: (250. + 3. * station, 470. - 3. * z)
    recessed_layout = plan_grouped_axial_bores_on_projection(
        page, upper_project, tall, [recessed], 0, view)
    assert recessed_layout is not None
    assert recessed_layout[0]["captions"][1].endswith("Z 67.5 TO 63")
    assert all(item["upper"] < item["lower"] for item in layout)
    assert len({round(item["x"], 2) for item in layout}) == len(layout)
    two_labels = plan_grouped_axial_bores_on_projection(
        page, project, feature, groups()[:2], 0, view)
    assert two_labels is not None
    assert all(item["box"].width < view.width / 3 for item in two_labels)

    # A rotated part chooses its second in-plane coordinate without a
    # FRONT/SIDE label or family-specific rule.
    rotated = plan_grouped_axial_bores_on_projection(
        page, project, feature, groups(axis=1), 1, view)
    assert rotated is not None and len(rotated) == len(layout)

    crowded_project = lambda station, z: (250. + 3. * station, 305. - 3. * z)
    assert plan_grouped_axial_bores_on_projection(
        page, crowded_project, feature, groups(), 0, view) is None
    narrow_view = fitz.Rect(135, 117, 500, 632)
    assert plan_grouped_axial_bores_on_projection(
        page, project, feature, groups(), 0, narrow_view) is None
    invalid = groups()
    invalid[0]["depth"] += 2
    assert plan_grouped_axial_bores_on_projection(
        page, project, feature, invalid, 0, view) is None
    class Mesh:
        triangles = np.array([
            [[30., 0., 0.], [30., 100., 0.], [30., 0., 80.]],
            [[60., 0., 0.], [60., 1., 0.], [60., 0., 1.]],
        ])
        triangles_center = triangles.mean(axis=1)
        face_normals = np.array([[1., 0., 0.], [1., 0., 0.]])
        area_faces = np.array([4000., .5])
    assert projected_planar_face_stations(
        Mesh(), [260., 100., 80.], 0, 1) == [0., 30., 260.]
    document.close()


if __name__ == "__main__":
    check()
    print("inline bore layout: geometry, rotation and fallback passed")
