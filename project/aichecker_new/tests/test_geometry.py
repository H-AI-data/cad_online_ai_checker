import copy
import hashlib
import json
from pathlib import Path

import cadquery as cq
import tempfile
import unittest

from common.geometry import analyze_step, match_families


def export_part(path, shape):
    # CQ's plain compound exporter intentionally creates a STEP assembly. Build
    # an explicit non-assembly XDE product to test a genuine multibody leaf.
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    document = TDocStd_Document(TCollection_ExtendedString('BinXCAF'))
    tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    tool.AddShape((shape.val() if isinstance(shape, cq.Workplane) else shape).wrapped, False)
    writer = STEPCAFControl_Writer()
    writer.Transfer(document, STEPControl_AsIs)
    writer.Write(str(path))
    return path


def export_assembly(path, assembly):
    assembly.export(str(path))
    return path


def metric(volume):
    return {'watertight': True, 'euler_number': 2, 'volume': volume, 'surface_area': 10., 'principal_inertia': [1., 2., 3.], 'convex_hull_volume': 1., 'convex_hull_area': 1.}


def family(id, *volumes):
    return {'id': id, 'metrics': metric(volumes[0]), 'members_metrics': [metric(v) for v in volumes], 'quantity': len(volumes)}


class GeometryTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.tmp_path = Path(self.directory.name)

    def test_repeated_rotated_and_scaled_parts(self):
        tmp_path = self.tmp_path
        box = cq.Workplane('XY').box(3, 7, 11)
        assembly = cq.Assembly(name='root')
        assembly.add(box, name='first')
        assembly.add(box, name='rotated', loc=cq.Location((100, 30, -50), (1, 2, 3), 37))
        assembly.add(cq.Workplane('XY').box(6, 7, 11), name='different', loc=cq.Location((40, 0, 0)))
        source = export_assembly(tmp_path / 'misleading_part.step', assembly)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        result = analyze_step(source, tmp_path / 'out')
        assert result['complete'], result['warnings']
        assert result['kind'] == 'assembly'
        assert result['structure']['product_definition_count'] > 0
        assert result['structure']['assembly_relation_count'] > 0
        assert result['occurrence_count'] == 3
        assert sorted(f['quantity'] for f in result['families']) == [1, 2]
        assert result['source_sha256'] == digest == hashlib.sha256(source.read_bytes()).hexdigest()
        assert result['images'] and all(Path(p).is_file() for p in result['images'])
        assert all(f['images'] and f['members_metrics'] for f in result['families'])
        json.dumps(result, allow_nan=False)
    
    
    def test_nested_repeated_subassemblies(self):
        tmp_path = self.tmp_path
        sub = cq.Assembly(name='sub')
        sub.add(cq.Workplane('XY').box(2, 3, 4), name='box')
        sub.add(cq.Workplane('XY').circle(2).extrude(7), name='cylinder', loc=cq.Location((8, 0, 0)))
        assembly = cq.Assembly(name='root')
        assembly.add(sub, name='a')
        assembly.add(sub, name='b', loc=cq.Location((20, 0, 0), (0, 0, 1), 90))
        result = analyze_step(export_assembly(tmp_path / 'nested.step', assembly), tmp_path / 'out')
        assert result['complete'], result['warnings']
        assert result['kind'] == 'assembly'
        assert result['occurrence_count'] == 4
        assert [f['quantity'] for f in result['families']] == [2, 2]
    
    
    def test_multibody_part_is_one_leaf_with_all_solids(self):
        tmp_path = self.tmp_path
        compound = cq.Compound.makeCompound([cq.Workplane('XY').box(2, 3, 4).val(), cq.Workplane('XY').box(3, 4, 5).val().translate((10, 0, 0))])
        source = export_part(tmp_path / 'assembly_name.step', compound)
        result = analyze_step(source, tmp_path / 'out')
        assert result['complete'], result['warnings']
        assert result['kind'] == 'part'
        assert result['occurrence_count'] == 1
        assert len(result['families']) == 1
        self.assertAlmostEqual(result['families'][0]['metrics']['volume'], 84)
    
    
    def test_foreign_part_and_subassembly_membership(self):
        tmp_path = self.tmp_path
        sub = cq.Assembly(name='sub')
        sub.add(cq.Workplane('XY').box(2, 3, 4), name='box')
        sub.add(cq.Workplane('XY').box(5, 6, 7), name='other')
        target = analyze_step(export_assembly(tmp_path / 'target.step', sub), tmp_path / 'target')
        part = analyze_step(export_part(tmp_path / 'part.step', cq.Workplane('XY').box(2, 3, 4)), tmp_path / 'part')
        foreign = analyze_step(export_part(tmp_path / 'foreign.step', cq.Workplane('XY').box(10, 12, 14)), tmp_path / 'foreign')
        assert match_families(target, [part])['complete']
        assert len(match_families(target, [part])['matched_family_ids']) == 1
        correspondence = match_families(target, [part])['matches'][0]
        assert correspondence['source_file'] == part['source_file']
        assert correspondence['family_id'] == part['families'][0]['id']
        assert correspondence['assembly_family_id'] in [f['id'] for f in target['families']]
        assert len(match_families(target, [target])['matched_family_ids']) == 2
        result = match_families(target, [part, foreign])
        assert result['foreign'] and result['foreign'][0]['source_file'] == str((tmp_path / 'foreign.step').resolve())
    
    
    def test_matching_checks_every_member_and_ambiguity(self):
        target = {'complete': True, 'families': [family('a', 100, 100.75), family('b', 101.5)]}
        source = {'complete': True, 'source_file': 'part.step', 'families': [family('p', 100.6)]}
        result = match_families(target, [source])
        assert not result['matched_family_ids']
        assert result['ambiguous'] and not result['complete']
        target['families'] = [family('a', 100, 100.75)]
        source['families'] = [family('p', 99.5)]
        assert match_families(target, [source])['foreign']
    
    
    def test_invalid_and_external_step_are_unknown(self):
        tmp_path = self.tmp_path
        content = b"ISO-10303-21;DATA;#1=EXTERNALLY_DEFINED_ITEM('x');ENDSEC;END-ISO-10303-21;"
        source = tmp_path / 'input.step'
        source.write_bytes(content)
        result = analyze_step(source, tmp_path / 'out')
        assert result['kind'] == 'unknown'
        assert not result['complete'] and result['warnings']
    
    
    def test_timeout_is_unknown(self):
        tmp_path = self.tmp_path
        source = export_part(tmp_path / 'slow.step', cq.Workplane('XY').box(2, 3, 4))
        result = analyze_step(source, tmp_path / 'out', timeout=0.000001)
        assert result['kind'] == 'unknown' and not result['complete']
        assert any('超时' in w or 'timeout' in w.lower() for w in result['warnings'])

    def test_missing_multi_member_evidence_is_unknown(self):
        target = {'complete': True, 'families': [{'id': 'a', 'metrics': metric(100), 'quantity': 2}]}
        source = {'complete': True, 'source_file': 'part.step', 'families': [family('p', 100)]}
        result = match_families(target, [source])
        assert not result['complete']
        assert not result['matched_family_ids']

    def test_broken_file_is_unknown(self):
        source = self.tmp_path / 'broken.step'
        source.write_bytes(b'broken STEP')
        result = analyze_step(source, self.tmp_path / 'out')
        assert result['kind'] == 'unknown' and not result['complete']

    def test_single_wrapper_is_part(self):
        assembly = cq.Assembly(name='root')
        assembly.add(cq.Workplane('XY').box(2, 3, 4), name='only')
        result = analyze_step(export_assembly(self.tmp_path / 'one.step', assembly), self.tmp_path / 'out')
        assert result['complete'], result['warnings']
        assert result['kind'] == 'part' and result['occurrence_count'] == 1

    def test_limits_allow_supplied_67mb_machine(self):
        from common.geometry_worker import MAX_ENTITIES, MAX_FILE_BYTES
        # Read-only inventory of supplied machine: 725958 entity lines, 70406194 bytes.
        assert MAX_ENTITIES >= 725958
        assert MAX_FILE_BYTES >= 70406194

    def test_oversized_source_rejected_before_hashing(self):
        from common.geometry_worker import MAX_FILE_BYTES
        source = self.tmp_path / 'huge.step'
        with source.open('wb') as stream:
            stream.truncate(MAX_FILE_BYTES + 1)
        result = analyze_step(source, self.tmp_path / 'out')
        assert not result['complete'] and result['kind'] == 'unknown'
        assert result['source_sha256'] is None
        assert any('大小' in message for message in result['warnings'])

    def test_supplied_heating_plate_closed_brep_with_nonmanifold_seams(self):
        source = Path(__file__).resolve().parents[2] / 'test_input' / '模块1' / 'SY-RY-A300-01-00 发热板.step'
        if not source.is_file():
            self.skipTest('Supplied read-only STEP fixture is not installed')
        result = analyze_step(source, self.tmp_path / 'out')
        assert result['complete'], result['warnings']
        assert result['kind'] == 'part' and result['occurrence_count'] == 1
        metrics = result['families'][0]['metrics']
        # Preserve original mechanical-seven topology: the valid BRep meshes
        # have closed seams with 36 nonmanifold edges, not missing faces.
        assert metrics['watertight'] is False and metrics['euler_number'] == 12
        self.assertLess(abs(metrics['volume'] / 2149600.4966568784 - 1), .0025)

    def test_per_pixel_depth_handles_intersecting_triangle_projections(self):
        import numpy as np
        from common.geometry_worker import _rasterize
        triangles = np.array([[[1, 1, 4], [9, 1, -2], [1, 9, -2]],
                              [[1, 1, 0], [9, 1, 0], [1, 9, 0]]], float)
        colors = np.array([[255, 0, 0], [0, 0, 255]], dtype=np.uint8)
        rgb, depth = _rasterize(triangles, colors, 12)
        assert tuple(rgb[2, 2]) == (255, 0, 0)
        assert tuple(rgb[2, 6]) == (0, 0, 255)
        self.assertGreater(depth[2, 2], 0)

    def test_blind_hole_front_depth_and_solid_back_depth(self):
        import numpy as np
        from common.geometry_worker import _mesh, _rasterize
        shape = cq.Workplane('XY').box(20, 20, 10).faces('>Z').workplane().hole(6, 4).val()
        mesh = _mesh(shape, .01, .1)
        triangles = mesh.triangles.copy()
        triangles[:, :, :2] = triangles[:, :, :2] * 10 + 110
        colors = np.tile(np.array([100, 150, 200], dtype=np.uint8), (len(triangles), 1))
        _, front = _rasterize(triangles, colors, 220)
        self.assertAlmostEqual(front[110, 110], 1, places=5)
        self.assertAlmostEqual(front[110, 170], 5, places=5)
        triangles[:, :, 2] *= -1
        _, back = _rasterize(triangles, colors, 220)
        self.assertAlmostEqual(back[110, 110], 5, places=5)

    def test_render_includes_opposed_views_without_changing_mesh(self):
        import numpy as np
        from common.geometry_worker import _mesh, _render
        mesh = _mesh(cq.Workplane('XY').box(20, 20, 10).val(), .01, .1)
        vertices, faces = mesh.vertices.copy(), mesh.faces.copy()
        images = _render(mesh, self.tmp_path / 'views.png')
        assert len(images) == 2 and all(Path(p).is_file() for p in images)
        np.testing.assert_array_equal(mesh.vertices, vertices)
        np.testing.assert_array_equal(mesh.faces, faces)

    def test_geometry_evidence_preserves_scale_and_explicit_units(self):
        for factor in (1, 2):
            source = export_part(self.tmp_path / f'box_{factor}.step', cq.Workplane('XY').box(3*factor, 7*factor, 11*factor))
            result = analyze_step(source, self.tmp_path / f'out_{factor}')
            assert result['complete'], result['warnings']
            evidence = result['geometry_evidence']
            assert evidence['complete']
            assert evidence['length_unit'] == 'mm'
            assert evidence['output_unit_confidence'] == 'configured_and_verified'
            assert evidence['source_length_units']
            for actual, expected in zip(evidence['world_axis_aligned_size'], [3*factor, 7*factor, 11*factor]):
                self.assertAlmostEqual(actual, expected)
            assert evidence['cylindrical_surfaces']['surface_count'] == 0

    def test_cylinder_evidence_counts_surfaces_and_rotates_instance_axes(self):
        assembly = cq.Assembly(name='axes')
        cylinder = cq.Workplane('XY').circle(2).extrude(10)
        assembly.add(cylinder, name='vertical')
        assembly.add(cylinder, name='horizontal', loc=cq.Location((15, 0, 0), (1, 0, 0), 90))
        result = analyze_step(export_assembly(self.tmp_path / 'axes.step', assembly), self.tmp_path / 'out')
        assert result['complete'], result['warnings']
        evidence = result['geometry_evidence']['cylindrical_surfaces']
        assert evidence['surface_count'] == 2
        assert evidence['axis_direction_count'] == 2
        groups = evidence['axis_radius_groups']
        assert {tuple(g['axis_direction']) for g in groups} == {(0., 0., 1.), (0., 1., 0.)}
        assert all(g['radius'] == 2 and g['surface_count'] == 1 for g in groups)
        assert 'not_hole_count' in evidence['interpretation']

    def test_cylinder_evidence_payload_is_bounded(self):
        from common.geometry_worker import _cylinder_evidence
        result = _cylinder_evidence([{'axis_direction': [0, 0, 1], 'radius': radius+1} for radius in range(100)])
        assert result['surface_count'] == 100 and result['axis_radius_group_count'] == 100
        assert len(result['axis_radius_groups']) == 64 and result['groups_truncated']

    def test_parallel_diagonal_cylinder_axes_have_stable_sign(self):
        from common.geometry_worker import _cylinder_evidence
        result = _cylinder_evidence([
            {'axis_direction': [1, -1-1e-12, 0], 'radius': 2},
            {'axis_direction': [1+1e-12, -1, 0], 'radius': 2},
        ])
        assert result['axis_direction_count'] == 1
        assert result['axis_radius_group_count'] == 1
        assert result['axis_radius_groups'][0]['surface_count'] == 2
        assert result['axis_radius_groups'][0]['axis_direction'] == [.70710678, -.70710678, 0.]
