"""Bounded XDE STEP instance traversal and approximate geometry fingerprints.

Fingerprints are necessary evidence for initial membership checks, not a proof of
congruence: different solids can share mass properties. No external references
are followed. A multibody part remains a single leaf instance.
"""
from __future__ import annotations

import io
import hashlib
import json
import sys
import numpy as np
import trimesh
from PIL import Image, ImageDraw
from numba import njit
from vendor.mechanical_families import mechanical_metrics, group_mechanical_metrics, MERGE_POLICY, CONTINUOUS_METRICS
from common.geometry import MAX_FILE_BYTES
from pathlib import Path
import re

import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.StepBasic import StepBasic_ProductDefinition
from OCP.StepRepr import StepRepr_AssemblyComponentUsage
from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
from OCP.TColStd import TColStd_SequenceOfAsciiString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label, TDF_LabelSequence, TDF_Tool
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.UnitsMethods import UnitsMethods

MAX_ENTITIES = 2_000_000
MAX_OCCURRENCES = 20_000
MAX_DEPTH = 64

# Strip STEP quoted strings and comments before checking syntactic tokens. This
# also catches external entities embedded in complex entity instances. Rejecting
# document metadata is deliberately conservative: CAF's Transfer can load files
# named by document references and offers no reliable public disable switch.
_NON_CODE = re.compile(rb"'(?:''|[^'])*'|/\*.*?\*/", re.DOTALL)
_EXTERNAL = re.compile(rb"\b(?:[A-Z_]*(?:EXTERNAL|EXTERNALLY|DOCUMENT)[A-Z_]*|PRODUCT_DEFINITION_REFERENCE[A-Z_]*)\s*\(", re.I)


def _entry(label):
    value = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, value)
    return value.ToCString()


def _name(label):
    attr = TDataStd_Name()
    return attr.Get().ToExtString() if label.FindAttribute(TDataStd_Name.GetID_s(), attr) else ""


def _has_failures(checks):
    checks.Start()
    while checks.More():
        if checks.Value().HasFailed():
            return True
        checks.Next()
    return False


def _check_product_graph(model):
    """Catch cyclic assembly products before CAF enters geometry transfer."""
    edges = {}
    unresolved = False
    for index in range(1, model.NbEntities() + 1):
        entity = model.Value(index)
        if not isinstance(entity, StepRepr_AssemblyComponentUsage):
            continue
        parent = entity.RelatingProductDefinition()
        child = entity.RelatedProductDefinition()
        if parent is None or child is None:
            unresolved = True
            continue
        # Keep handles alive as keys: STEPModel.Number() returns zero for these
        # transferred handles in the installed OCP binding.
        edges.setdefault(parent, set()).add(child)
    done = set()
    for root in edges:
        if root in done:
            continue
        active = set()
        stack = [(root, False)]
        while stack:
            current, leaving = stack.pop()
            if leaving:
                active.remove(current)
                done.add(current)
                continue
            if current in active:
                raise ValueError("STEP 装配产品存在循环引用，无法安全转换")
            if current in done:
                continue
            if len(active) >= MAX_DEPTH:
                raise ValueError("STEP 装配层级超过安全转换限制")
            active.add(current)
            stack.append((current, True))
            stack.extend((child, False) for child in edges.get(current, ()))
    return unresolved


def read_step(path) -> dict:
    """Read one self-contained STEP file; return private geometry records.

    Each occurrence has id (instance path), name, definition_id, definition_name,
    signature, and solid_count. complete=False means traversal/geometry had gaps.
    Invalid files, external-reference files, and files without usable solids
    raise ValueError in Chinese. Limits intentionally favor a manual review.
    """
    try:
        source = Path(path)
        before = source.stat()
        with source.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        after = source.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("读取期间 STEP 文件发生变化，请重新检查")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("STEP 文件超过安全读取大小限制")
        syntax = _NON_CODE.sub(b" ", data)
        if _EXTERNAL.search(syntax) or re.search(rb"\b(?:REFERENCE|ANCHOR)\s*;", syntax, re.I):
            raise ValueError("STEP 包含外部或文档引用；为避免加载外部文件，拒绝读取，请先导出为完整单文件")
        if not re.search(rb"END-ISO-10303-21\s*;\s*$", syntax, re.I):
            raise ValueError("STEP 文件损坏或不完整：缺少结束标记")
        reader = STEPCAFControl_Reader()
        reader.SetNameMode(True)
        reader.SetColorMode(False)
        reader.SetLayerMode(False)
        base = reader.ChangeReader()
        # Freeze input and remove its original filesystem path from CAF. STEP's
        # plain reader only parses this in-memory stream; CAF transfer is later.
        if base.ReadStream("in_memory.step", io.BytesIO(data)) != IFSelect_RetDone:
            raise ValueError("无法解析 STEP 文件，文件可能损坏")
        # OCP's documented system factor is measured in millimeters; 1.0 maps
        # to 'mm'. Configure both the STEP transfer and XDE target document.
        base.SetSystemLengthUnit(1.0)
        length_names, angle_names, solid_angle_names = (TColStd_SequenceOfAsciiString() for _ in range(3))
        try:
            base.FileUnits(length_names, angle_names, solid_angle_names)
            source_length_units = [length_names.Value(i).ToCString() for i in range(1, length_names.Length()+1)]
        except Exception:
            source_length_units = []  # Metadata evidence fails closed independently.
        model = base.Model()
        if model.NbEntities() > MAX_ENTITIES:
            raise ValueError("STEP 实体数量超过安全读取限制")
        # Defense in depth against syntactic variations/complex entities.
        structure = {'product_definition_count': 0, 'assembly_relation_count': 0}
        for index in range(1, model.NbEntities() + 1):
            entity = model.Value(index)
            entity_type = type(entity).__name__.upper()
            if "EXTERN" in entity_type or "DOCUMENT" in entity_type or "PRODUCTDEFINITIONREFERENCE" in entity_type:
                raise ValueError("STEP 包含外部引用，已禁止加载外部文件")
            structure['product_definition_count'] += int(isinstance(entity, StepBasic_ProductDefinition))
            structure['assembly_relation_count'] += int(isinstance(entity, StepRepr_AssemblyComponentUsage))
        warnings = []
        if not structure['product_definition_count']:
            warnings.append('STEP 产品定义缺失，无法确认原始零件结构')
        if _check_product_graph(model):
            warnings.append("STEP 装配包含无法解析的产品引用")
        if _has_failures(base.WS().ModelCheckList()):
            warnings.append("STEP 数据校验存在错误，可能遗漏几何或引用")
        doc = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
        XCAFDoc_DocumentTool.SetLengthUnit_s(doc, .001)  # Document API uses meters.
        if not reader.Transfer(doc):
            warnings.append("STEP 转换未完整成功")
        if _has_failures(base.WS().TransferReader().LastCheckList()):
            warnings.append("STEP 几何转换存在错误，可能遗漏零件")
        tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        roots = TDF_LabelSequence()
        tool.GetFreeShapes(roots)
        result = {"has_structure": False, "complete": not warnings, "occurrences": [], "warnings": warnings, "source_sha256": hashlib.sha256(data).hexdigest()}
        cache = {}
        visited_count = 0

        def gap(message):
            result["complete"] = False
            if message not in warnings:
                warnings.append(message)

        def visit(label, path_parts, ancestry, depth, location):
            nonlocal visited_count
            visited_count += 1
            if depth > MAX_DEPTH or visited_count > MAX_OCCURRENCES:
                gap("装配层级或实例数量超过安全遍历限制，结果不完整")
                return
            location = location * cq.Location(tool.GetLocation_s(label))
            definition = label
            if tool.IsReference_s(label):
                definition = TDF_Label()
                if not tool.GetReferredShape_s(label, definition) or definition.IsNull():
                    gap("装配存在无法解析的零件引用")
                    return
            key = _entry(definition)
            path_parts = [*path_parts, _entry(label)]
            if key in ancestry:
                gap("装配存在循环引用，结果不完整")
                return
            if tool.IsExternRef_s(definition):
                gap("装配存在未加载的外部零件引用")
                return
            if tool.IsAssembly_s(definition):
                result["has_structure"] = True
                children = TDF_LabelSequence()
                tool.GetComponents_s(definition, children, False)
                if not children.Length():
                    gap("装配包含空的子装配")
                for index in range(1, children.Length() + 1):
                    if visited_count >= MAX_OCCURRENCES:
                        gap("装配层级或实例数量超过安全遍历限制，结果不完整")
                        break
                    visit(children.Value(index), path_parts, ancestry | {key}, depth + 1, location)
                return
            if key not in cache:
                try:
                    cache[key] = _geometry(tool.GetShape_s(definition))
                except Exception:
                    cache[key] = (None, 0, False)
            signature, solid_count, complete = cache[key]
            if not complete:
                gap(f"零件 {key} 几何缺失、无有效实体或不完整")
            if signature is not None:
                result["occurrences"].append({
                    "id": "/".join(path_parts), "name": _name(label) or _name(definition) or key,
                    "definition_id": key, "definition_name": _name(definition) or key,
                    "signature": signature, "solid_count": solid_count, "location": location,
                })

        for index in range(1, roots.Length() + 1):
            visit(roots.Value(index), [], set(), 0, cq.Location())
        if not result["occurrences"]:
            raise ValueError("STEP 文件没有可用的实体几何，无法检查装配")
        result["root_count"] = roots.Length()
        system_factor = base.SystemLengthUnit()
        result['unit_evidence'] = {
            'length_unit': UnitsMethods.DumpLengthUnit_s(system_factor),
            'output_unit_confidence': 'configured_and_verified' if system_factor == 1.0 else 'unknown',
            'source_length_units': source_length_units,
            'source_unit_confidence': 'declared_in_step' if source_length_units else 'unknown',
            'system_length_unit_in_millimeters': system_factor,
        }
        result['structure'] = structure
        if structure['assembly_relation_count'] and not result['has_structure']:
            gap('STEP 装配关系未能保留到转换后的结构树')
        if result['has_structure'] and not structure['assembly_relation_count']:
            gap('STEP 转换树有分组但源文件没有可验证的装配关系')
        result["definitions"] = cache
        return result
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"STEP 读取失败：{type(exc).__name__}") from exc


def _mesh(shape, linear, angular):
    # Weld within each solid only; touching bodies retain their own topology.
    meshes = []
    for solid in shape.Solids():
        vertices, triangles = solid.tessellate(linear, angularTolerance=angular)
        meshes.append(trimesh.Trimesh(vertices=[v.toTuple() for v in vertices], faces=triangles, process=True))
    return trimesh.util.concatenate(meshes)


def _geometry(shape):
    if shape.IsNull():
        return None, 0, False
    wrapped = cq.Shape.cast(shape).located(cq.Location())
    solids = wrapped.Solids()
    if not solids:
        return None, 0, False
    valid = wrapped.isValid() and not any(
        TopExp_Explorer(shape, kind, parent).More()
        for kind, parent in [(TopAbs_FACE, TopAbs_SOLID), (TopAbs_EDGE, TopAbs_FACE), (TopAbs_VERTEX, TopAbs_EDGE)]
    )
    compound = cq.Compound.makeCompound(solids)
    volume, surface = GProp_GProps(), GProp_GProps()
    BRepGProp.VolumeProperties_s(compound.wrapped, volume)
    BRepGProp.SurfaceProperties_s(compound.wrapped, surface)
    exact = {'volume': volume.Mass(), 'surface_area': surface.Mass(),
             'principal_inertia': sorted(volume.PrincipalProperties().Moments())}
    linear = max(1e-7, compound.BoundingBox().DiagonalLength * 1e-4)
    previous = None
    accepted = False
    for level in range(4):
        mesh = _mesh(compound, linear / 2**level, .1 / 2**level)
        metrics = mechanical_metrics(mesh)
        # Watertightness is an exact *family invariant*, not a completeness
        # requirement: valid CAD solids can tessellate to stable nonmanifold
        # seam slivers. Preserve that topology instead of repairing it away.
        accurate = metrics['volume'] > 0
        for key, reference in exact.items():
            a, b = np.asarray(metrics[key]), np.asarray(reference)
            accurate &= bool(np.all(np.abs(a - b) <= .0025 * np.maximum(np.abs(b), 1e-14)))
        stable = previous is not None and all(
            np.all(np.abs(np.asarray(metrics[k]) - np.asarray(previous[k])) <= .0025 * np.maximum(np.abs(previous[k]), 1e-14))
            for k in CONTINUOUS_METRICS
        ) and all(metrics[k] == previous[k] for k in ('watertight', 'euler_number'))
        if accurate and stable:
            accepted = True
            break
        previous = metrics
    cylinders = []
    cylinder_evidence_complete = True
    for face in compound.Faces():
        try:
            if face.geomType() == 'CYLINDER':
                cylinder = BRepAdaptor_Surface(face.wrapped).Cylinder()
                direction = cylinder.Axis().Direction()
                cylinders.append({'axis_direction': [direction.X(), direction.Y(), direction.Z()],
                                  'radius': cylinder.Radius()})
        except Exception:
            cylinder_evidence_complete = False
    return {'metrics': metrics, 'mesh': mesh, 'mesh_verified': bool(accepted), 'exact': exact,
            'cylinder_surfaces': cylinders, 'cylinder_evidence_complete': cylinder_evidence_complete}, len(solids), bool(valid and accepted)


def _cylinder_evidence(cylinders):
    """Group parallel surface axes, not coaxial holes, bosses, or part counts."""
    groups = {}
    for cylinder in cylinders:
        axis = np.asarray(cylinder['axis_direction'], float)
        axis /= np.linalg.norm(axis)
        axis = np.round(axis, 8)
        axis[np.abs(axis) < 1e-8] = 0.0
        if axis[np.flatnonzero(axis)[0]] < 0:
            axis = -axis
        axis = tuple(float(v) if v != 0 else 0.0 for v in axis)
        key = (axis, round(float(cylinder['radius']), 8))
        groups[key] = groups.get(key, 0) + 1
    rows = [{'axis_direction': list(axis), 'radius': radius, 'surface_count': count}
            for (axis, radius), count in groups.items()]
    return {'surface_count': len(cylinders), 'axis_direction_count': len({key[0] for key in groups}),
            'axis_radius_group_count': len(rows), 'axis_radius_groups': rows[:64], 'groups_truncated': len(rows) > 64,
            'interpretation': 'cylinder_surface_count_not_hole_count: surfaces may belong to bores or bosses; split faces count separately; axis direction does not establish coaxiality'}


@njit(cache=True)
def _rasterize(triangles, colors, size):
    """Orthographic z-buffer: larger interpolated z is closer to the camera."""
    rgb = np.full((size, size, 3), 255, dtype=np.uint8)
    depth = np.full((size, size), -np.inf)
    for index in range(len(triangles)):
        a, b, c = triangles[index, 0], triangles[index, 1], triangles[index, 2]
        denominator = (b[1]-c[1])*(a[0]-c[0]) + (c[0]-b[0])*(a[1]-c[1])
        if abs(denominator) < 1e-12:
            continue
        xmin = max(0, int(np.ceil(min(a[0], b[0], c[0]) - .5)))
        xmax = min(size-1, int(np.floor(max(a[0], b[0], c[0]) - .5)))
        ymin = max(0, int(np.ceil(min(a[1], b[1], c[1]) - .5)))
        ymax = min(size-1, int(np.floor(max(a[1], b[1], c[1]) - .5)))
        for row in range(ymin, ymax+1):
            for col in range(xmin, xmax+1):
                x, y = col + .5, row + .5
                u = ((b[1]-c[1])*(x-c[0]) + (c[0]-b[0])*(y-c[1])) / denominator
                v = ((c[1]-a[1])*(x-c[0]) + (a[0]-c[0])*(y-c[1])) / denominator
                w = 1-u-v
                if min(u, v, w) < -1e-10:
                    continue
                z = u*a[2] + v*b[2] + w*c[2]
                if z > depth[row, col]:
                    depth[row, col] = z
                    rgb[row, col, :] = colors[index]
    return rgb, depth


def _render(mesh, output, local=False):
    """Six opposed orthographic and two isometric views with pixelwise occlusion."""
    mesh = mesh.copy()
    if local:
        mesh.apply_transform(mesh.principal_inertia_transform)
    mesh.apply_translation(-mesh.bounds.mean(axis=0))
    triangles = mesh.triangles
    # The image is supporting evidence: never infer missing geometry from occlusion.
    canvases = [Image.new('RGB', (1200, 1200), 'white') for _ in range(2)]
    directions = [(0., -1., 0.), (0., 1., 0.), (0., 0., 1.), (1., -1., .8),
                  (1., 0., 0.), (-1., 0., 0.), (0., 0., -1.), (-1., 1., -.8)]
    names = ['-Y view', '+Y view (opposed)', '+Z view', 'Isometric (+X,-Y,+Z)',
             '+X view', '-X view (opposed)', '-Z view (opposed)', 'Isometric (-X,+Y,-Z)']
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-20)
    light = .4 + .6 * np.abs(normals @ np.array([.3, -.4, .866]))
    colors = np.column_stack((70 + 100*light, 95 + 100*light, 120 + 100*light)).astype(np.uint8)
    for view, direction in enumerate(directions):
        z = np.asarray(direction, float)
        z /= np.linalg.norm(z)
        up = np.array([0., 0., 1.]) if abs(z[2]) < .9 else np.array([0., 1., 0.])
        x = np.cross(up, z); x /= np.linalg.norm(x)
        y = np.cross(z, x)
        projected = triangles @ np.column_stack((x, y, z))
        low, high = projected[:, :, :2].min(axis=(0, 1)), projected[:, :, :2].max(axis=(0, 1))
        scale = 510 / max(float(np.max(high - low)), 1e-9)
        projected[:, :, :2] = (projected[:, :, :2] - (low + high) / 2) * scale
        projected[:, :, 1] *= -1
        projected[:, :, :2] += [300, 315]
        rgb, depth = _rasterize(projected, colors, 600)
        # Draw only measured depth discontinuities, so a blind hole's floor
        # remains distinguishable from a parallel, equally lit outer face.
        visible = np.isfinite(depth)
        threshold = max(float(np.ptp(triangles, axis=(0, 1)).max()) * .01, 1e-9)
        edge = np.zeros(depth.shape, dtype=bool)
        for dy, dx in [(0, 1), (1, 0)]:
            shifted = np.roll(depth, (dy, dx), axis=(0, 1))
            valid = visible & np.isfinite(shifted)
            difference = np.zeros(depth.shape)
            np.subtract(depth, shifted, out=difference, where=valid)
            edge |= valid & (np.abs(difference) > threshold)
        rgb[edge] = (rgb[edge].astype(float) * .45).astype(np.uint8)
        panel = Image.fromarray(rgb)
        ImageDraw.Draw(panel).text((20, 15), names[view] + ' | orthographic', fill='black')
        canvases[view//4].paste(panel, (600*(view % 2), 600*((view % 4)//2)))
    paths = [output, output.with_name(output.stem + '_opposed.png')]
    for canvas, path in zip(canvases, paths):
        canvas.save(path)
    return [str(path.resolve()) for path in paths]


def analyze(path, output):
    parsed = read_step(path)
    occurrences = parsed['occurrences']
    definitions = parsed.pop('definitions')
    usable = [key for key, (item, _, _) in definitions.items() if item is not None]
    groups = group_mechanical_metrics([definitions[key][0]['metrics'] for key in usable])
    families = []
    for number, group in enumerate(groups, 1):
        keys = [usable[index] for index in group]
        representative = definitions[keys[0]][0]
        family_id = f'family_{number:04d}'
        family_occurrences = [o for o in occurrences if o['definition_id'] in keys]
        families.append({'id': family_id, 'metrics': representative['metrics'],
                         'members_metrics': [definitions[key][0]['metrics'] for key in keys],
                         'quantity': len(family_occurrences), 'definition_ids': keys,
                         'occurrence_ids': [o['id'] for o in family_occurrences],
                         'solid_counts': [definitions[key][1] for key in keys],
                         'images': _render(representative['mesh'], output / f'{family_id}.png', local=True)})
    instances = []
    cylinder_instances = []
    for occurrence in occurrences:
        mesh = definitions[occurrence['definition_id']][0]['mesh'].copy()
        transform = occurrence['location'].wrapped.Transformation()
        matrix = np.eye(4)
        for row in range(3):
            for col in range(4):
                matrix[row, col] = transform.Value(row+1, col+1)
        mesh.apply_transform(matrix)
        instances.append(mesh)
        for cylinder in definitions[occurrence['definition_id']][0]['cylinder_surfaces']:
            cylinder_instances.append({'axis_direction': (matrix[:3, :3] @ cylinder['axis_direction']).tolist(),
                                       'radius': cylinder['radius']})
    source_mesh = trimesh.util.concatenate(instances)
    images = _render(source_mesh, output / 'source.png')
    geometry_evidence = {**parsed['unit_evidence'], 'world_axis_aligned_size': source_mesh.extents.tolist(),
                         'size_method': 'tessellated_world_axis_aligned_extent; dimensions preserve scale and source view axes; curved extrema are approximate',
                         'cylindrical_surfaces': _cylinder_evidence(cylinder_instances),
                         'cylinder_method': 'analytic_BRep_surface_axes_transformed_by_XDE_instances',
                         'limitations': 'Cylindrical surfaces alone cannot identify bores versus bosses, through versus blind holes, or manufacturing complexity.'}
    geometry_evidence['complete'] = bool(
        parsed['complete'] and parsed['unit_evidence']['source_length_units']
        and parsed['unit_evidence']['output_unit_confidence'] == 'configured_and_verified'
        and all(definitions[key][0]['cylinder_evidence_complete'] for key in usable)
        and not geometry_evidence['cylindrical_surfaces']['groups_truncated'])
    kind = 'unknown'
    if parsed['complete']:
        if parsed['has_structure'] and len(occurrences) > 1:
            kind = 'assembly'
        elif parsed['root_count'] == 1 and len(occurrences) == 1:
            kind = 'part'
    if kind == 'unknown' and parsed['complete']:
        parsed['complete'] = False
        parsed['warnings'].append('STEP 没有足够的产品结构确认单零件或多叶装配')
    return {'kind': kind, 'complete': parsed['complete'], 'families': families,
            'occurrence_count': len(occurrences), 'images': images, 'warnings': parsed['warnings'],
            'source_file': str(path), 'source_sha256': parsed['source_sha256'], 'family_merge_policy': MERGE_POLICY,
            'definition_count': len(usable), 'structure': parsed['structure'],
            'geometry_evidence': geometry_evidence,
            'view_note': '图像存在投影遮挡；不能依据图像中不可见断言零件或特征不存在。'}


def main():
    path, output, destination = map(Path, sys.argv[1:])
    try:
        result = analyze(path, output)
    except Exception as exc:
        result = {'kind': 'unknown', 'complete': False, 'families': [], 'occurrence_count': 0, 'images': [],
                  'warnings': [f'{type(exc).__name__}: {exc}'], 'source_file': str(path),
                  'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    destination.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    main()
