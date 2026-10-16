"""Extract STEP solids and meshes with the host FreeCAD Python runtime."""
import argparse
import gzip
import hashlib
import json
import math
import sys
import gc
from pathlib import Path
import numpy as np
p = argparse.ArgumentParser()
p.add_argument('source', type=Path)
p.add_argument('output', type=Path)
p.add_argument('--tolerance', type=float, default=0.15)
p.add_argument('--freecad-lib', type=Path)
a = p.parse_args()
if not math.isfinite(a.tolerance) or a.tolerance <= 0:
    p.error('tolerance must be positive and finite')
if a.freecad_lib:
    sys.path.insert(0, str(a.freecad_lib))
import FreeCAD as App  # Import before Part: otherwise some builds segfault.
import Part
import MeshPart
a.output.mkdir(parents=True, exist_ok=True)
shape = Part.read(str(a.source))
solids = list(shape.Solids)
if not solids:
    raise SystemExit('No closed solids found. Surface-only STEP needs a separate workflow.')
# The verifier rejects an assembly if any occurrence is an invalid B-rep.
# Reject it here as well, before drawing pages for an unscorable source.
for i, solid in enumerate(solids, 1):
    if not solid.isValid() or not solid.isClosed() or solid.Volume <= 0:
        raise ValueError(f'solid_{i:04d}: invalid source B-rep; skip assembly')
parts, meshes = [], []


def tessellated_volume(vertices, faces):
    """Compute the signed mesh volume without holding all triangles at once."""
    points = np.asarray([[v.x, v.y, v.z] for v in vertices], dtype=np.float64)
    points -= points.mean(axis=0)
    total = 0.0
    for start in range(0, len(faces), 20_000):
        triangles = points[np.asarray(faces[start:start + 20_000], dtype=np.int64)]
        total += float(np.einsum(
            'ij,ij->i', triangles[:, 0],
            np.cross(triangles[:, 1], triangles[:, 2]),
        ).sum())
    return abs(total / 6.0)


def tessellate_with_volume_check(solid, initial_tolerance):
    """Refine small curved solids when coarse facets distort their volume."""
    target = abs(float(solid.Volume))
    tolerances = [initial_tolerance]
    tolerances.extend(t for t in (0.05, 0.02, 0.015, 0.01, 0.005)
                      if t < initial_tolerance)
    best = None
    for tolerance in tolerances:
        vertices, faces = solid.tessellate(tolerance)
        if not vertices or not faces:
            raise ValueError('empty tessellation')
        # A few imported spline surfaces ignore this deflection and generate
        # millions of facets.  Do not refine that mesh again before switching
        # to the bounded MeshPart display path below.
        if len(faces) > 1_000_000:
            return tolerance, vertices, faces
        discrepancy = abs(tessellated_volume(vertices, faces) - target)
        if best is None or discrepancy < best[0]:
            best = (discrepancy, tolerance, vertices, faces)
        if discrepancy <= max(0.05, target * 0.05):
            return tolerance, vertices, faces
    return best[1:]


for i, solid in enumerate(solids, 1):
    ident = f'solid_{i:04d}'
    mesh_tolerance, verts, faces = tessellate_with_volume_check(solid, a.tolerance)
    source_triangles = len(faces)
    mesh_method = 'brep_tessellate'
    if source_triangles > 1_000_000:
        del verts, faces
        gc.collect()
        # Keep the exact source B-rep for the supplied STEP tier, but use a
        # bounded closed display mesh for grouping and drawing.  The first
        # setting is the normal CAD mesh tier; refine angle only if needed.
        for angular in (0.5, 0.1):
            display = MeshPart.meshFromShape(
                Shape=solid.cleaned(), LinearDeflection=0.05,
                AngularDeflection=angular, Relative=False)
            volume_error = abs(abs(float(display.Volume))-abs(float(solid.Volume)))
            if (display.CountFacets <= 1_000_000 and display.isSolid()
                    and volume_error <= max(0.05, abs(float(solid.Volume))*0.05)):
                verts, faces = display.Topology
                mesh_tolerance = 0.05
                mesh_method = f'meshpart_0.05_{angular:g}'
                break
            del display
            gc.collect()
        else:
            raise ValueError(f'{ident}: no bounded closed display mesh for super-complex solid')
    vertices = [[v.x, v.y, v.z] for v in verts]
    low = [min(v[j] for v in vertices) for j in range(3)]
    high = [max(v[j] for v in vertices) for j in range(3)]
    cylinders = {}
    for face in solid.Faces:
        s = face.Surface
        if isinstance(s, Part.Cylinder):
            axis = [s.Axis.x, s.Axis.y, s.Axis.z]
            k = max(range(3), key=lambda j: abs(axis[j]))
            if axis[k] < 0:
                axis = [-x for x in axis]
            key = tuple(round(x, 4) for x in axis)
            cylinders[key] = cylinders.get(key, 0) + face.Area
    axes = [{'direction': list(k), 'area_mm2': round(v, 3)}
            for k, v in sorted(cylinders.items(), key=lambda item: -item[1])[:3]]
    parts.append(dict(id=ident, label=ident, bbox_min=low, bbox_max=high,
                      center=[(x+y)/2 for x,y in zip(low,high)],
                      size=[y-x for x,y in zip(low,high)], volume_mm3=solid.Volume,
                      cylinder_axes=axes, triangles=len(faces),
                      source_triangles=source_triangles, mesh_method=mesh_method,
                      mesh_tolerance_mm=mesh_tolerance))
    meshes.append(dict(id=ident, vertices=vertices, faces=faces))
    print(f'{ident}: {len(faces)} triangles', flush=True)
catalog = dict(schema_version=1, source_name=a.source.name,
               source_sha256=hashlib.sha256(a.source.read_bytes()).hexdigest(),
               units='mm', coordinate_frame='source STEP world frame; no normalization',
               tolerance_mm=a.tolerance, occurrence_count=len(parts),
               id_scope='This export only. Re-export/importer changes may reorder solids.',
               parts=parts)
(a.output/'catalog.json').write_text(json.dumps(catalog, indent=2))
with gzip.open(a.output/'meshes.json.gz', 'wt', encoding='utf-8') as f:
    json.dump(meshes, f, separators=(',', ':'))
print(f'Exported {len(parts)} solids to {a.output}', flush=True)
