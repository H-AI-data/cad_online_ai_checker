"""Run in FreeCAD Python; export assembled BRep projections and inner cylinders."""
import argparse
import hashlib
import json
import sys
from pathlib import Path


def surface_silhouettes(solid, origin, deflection):
    """View-dependent surface outlines; omit internal triangulation edges."""
    import numpy as np
    vertices, triangles = solid.tessellate(deflection)
    vertices = np.array([[v.x,v.y,v.z] for v in vertices])-origin
    vertices, inverse = np.unique(np.round(vertices,8),axis=0,return_inverse=True)
    faces = inverse[np.array(triangles)]
    tri = vertices[faces]
    normals = np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])
    normals /= np.maximum(np.linalg.norm(normals,axis=1)[:,None],1e-20)
    edges = np.sort(np.concatenate([faces[:,[0,1]],faces[:,[1,2]],faces[:,[2,0]]]),axis=1)
    edges, edge_index = np.unique(edges,axis=0,return_inverse=True)
    normals = np.tile(normals,(3,1))
    result = {}
    for a,b,normal in [(0,2,1),(1,2,0),(0,1,2)]:
        low = np.full(len(edges),np.inf);high = np.full(len(edges),-np.inf)
        np.minimum.at(low,edge_index,normals[:,normal]);np.maximum.at(high,edge_index,normals[:,normal])
        selected = edges[(low < -1e-7) & (high > 1e-7)]
        result[f'{a},{b}'] = vertices[selected].tolist()
    # Imported cylinders may be split at the tangent generator. Meshes on the two
    # faces need not share vertices there, so a sign-change test alone can miss it.
    import FreeCAD as App
    import Part
    import math
    for face in solid.Faces:
        surface=face.Surface
        if not isinstance(surface,Part.Cylinder):continue
        u0,u1,v0,v1=face.ParameterRange
        e0=face.valueAt(0,0)-surface.Center;e0-=surface.Axis*e0.dot(surface.Axis)
        e1=face.valueAt(math.pi/2,0)-surface.Center;e1-=surface.Axis*e1.dot(surface.Axis)
        for a,b,normal in [(0,2,1),(1,2,0),(0,1,2)]:
            view=App.Vector(*[int(k==normal) for k in range(3)])
            x,y=e0.dot(view),e1.dot(view)
            if math.hypot(x,y)<1e-9:continue
            angle=math.atan2(-x,y)
            for root in (angle,angle+math.pi):
                u=root+math.ceil((u0-root-1e-9)/(2*math.pi))*2*math.pi
                if u>u1+1e-9:continue
                p,q=face.valueAt(u,v0),face.valueAt(u,v1)
                if (p-q).Length<1e-8:continue
                # Clip against the trimmed face, including holes and cutouts.
                for edge in Part.makeLine(p,q).common(face).Edges:
                    points=edge.discretize(Deflection=deflection)
                    result[f'{a},{b}'].append([[v.x-origin[0],v.y-origin[1],v.z-origin[2]] for v in points])
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--step', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--spec', type=Path, help='Optional part-spec.json for family identity')
    ap.add_argument('--catalog', type=Path,
                    help='Source-bound display mesh catalog; skip silhouettes for supplied super-complex solids')
    ap.add_argument('--freecad-lib', type=Path)
    ap.add_argument('--deflection', type=float, default=.03, help='Edge sampling deflection in mm')
    args = ap.parse_args()
    if args.deflection <= 0:
        ap.error('deflection must be positive')
    if args.freecad_lib:
        sys.path.insert(0, str(args.freecad_lib))
    import FreeCAD  # Must precede Part in the bundled FreeCAD runtime.
    import Part
    import numpy as np

    shape = Part.read(str(args.step.resolve()))
    if not shape.Solids:
        raise ValueError('STEP contains no solids; surface-only input is not supported')
    super_complex_indices = set()
    if args.catalog:
        catalog = json.loads(args.catalog.read_text())
        if (catalog.get('source_sha256') != hashlib.sha256(args.step.read_bytes()).hexdigest()
                or len(catalog.get('parts', [])) != len(shape.Solids)):
            raise ValueError('Analytic feature catalog does not match source STEP')
        super_complex_indices = {
            index for index, part in enumerate(catalog['parts'])
            if int(part.get('source_triangles', part['triangles'])) > 1_000_000
        }
    raw = []
    for solid in shape.Solids:
        paths = []
        for edge in solid.Edges:
            points = edge.discretize(Deflection=args.deflection)
            if len(points) > 1:
                paths.append([[p.x, p.y, p.z] for p in points])
        v = np.concatenate(paths)
        raw.append((solid, paths, v.min(0), v.max(0)))
    origin = np.min([r[2] for r in raw], axis=0)
    high = np.max([r[3] for r in raw], axis=0)
    centers = np.array([(r[2]+r[3])/2-origin for r in raw])
    sizes = np.array([r[3]-r[2] for r in raw])
    errors = {}
    if args.spec:
        from scipy.optimize import linear_sum_assignment
        occurrences = json.loads(args.spec.read_text())['occurrences']
        if len(occurrences) != len(raw):
            raise ValueError('STEP solid count differs from part-spec occurrence count')
        dc = np.linalg.norm(centers[:, None]-np.array([o['center_wdh_mm'] for o in occurrences]), axis=2)
        ds = np.linalg.norm(sizes[:, None]-np.array([o['size_wdh_mm'] for o in occurrences]), axis=2)
        rr, cc = linear_sum_assignment(dc+ds)
        if dc[rr, cc].max() >= .15 or ds[rr, cc].max() >= .3:
            raise ValueError('Spec mapping failed: check assembly orientation, units and source STEP')
        identities = {int(i): occurrences[int(j)] for i, j in zip(rr, cc)}
        errors = {'mapping_max_center_error_mm': float(dc[rr, cc].max()),
                  'mapping_max_size_error_mm': float(ds[rr, cc].max())}
    else:
        identities = {i: {'id': f'occurrence_{i+1:03d}', 'family_id': f'family_{i+1:03d}'} for i in range(len(raw))}
    parts, bores = [], []
    for i, (solid, paths, low, upper) in enumerate(raw):
        identity = identities[i]
        circles = {}
        cylinders = [face.Surface for face in solid.Faces if isinstance(face.Surface, Part.Cylinder)]
        for edge_index, edge in enumerate(solid.Edges):
            try:
                curve = edge.Curve
            except TypeError:
                # Some imported edges have no exposed analytic curve; sampled paths remain.
                continue
            if isinstance(curve, Part.Circle):
                center = np.array([curve.Center.x, curve.Center.y, curve.Center.z])-origin
                axis = np.array([curve.Axis.x, curve.Axis.y, curve.Axis.z])
                if axis[np.argmax(abs(axis))] < 0:
                    axis = -axis
                key = edge_index  # Distinct trimmed arcs on the same circle must not overwrite each other.
                rim = edge.valueAt((edge.FirstParameter+edge.LastParameter)/2)
                circles[key] = dict(center=center.tolist(), radius=curve.Radius, axis=axis.tolist(),
                    curve_kind='circle', edge_index=edge_index,
                    arc_span=float(edge.LastParameter-edge.FirstParameter),
                    rim_candidates=[[v.x-origin[0],v.y-origin[1],v.z-origin[2]] for v in
                        [edge.valueAt(edge.FirstParameter+(edge.LastParameter-edge.FirstParameter)*t/24)
                         for t in range(3,22)]],
                    projection_minor_major_ratio={f'{a},{b}':float(abs(axis[n])/np.linalg.norm(axis))
                        for a,b,n in [(0,2,1),(1,2,0),(0,1,2)]},
                    full_circle=abs(edge.LastParameter-edge.FirstParameter) >= 6.28,
                    cylindrical=any(abs(surface.Radius-curve.Radius)<1e-5 and
                        abs(abs(surface.Axis.dot(curve.Axis))-1)<1e-6 and
                        (curve.Center-surface.Center).cross(surface.Axis).Length<1e-4 for surface in cylinders),
                    rim=[rim.x-origin[0], rim.y-origin[1], rim.z-origin[2]])
        parts.append(dict(id=identity['id'], family=identity['family_id'], solid_index=i,
                          low=(low-origin).tolist(), high=(upper-origin).tolist(),
                          center=centers[i].tolist(), size=sizes[i].tolist(),
                          paths=[(np.array(p)-origin).tolist() for p in paths], circles=list(circles.values()),
                          silhouettes=({} if i in super_complex_indices else
                                       surface_silhouettes(solid,origin,args.deflection))))
        # Associate directly with the same solid; nearest-center rematching is ambiguous.
        for face in solid.Faces:
            surface = face.Surface
            if not isinstance(surface, Part.Cylinder):
                continue
            u0, u1, v0, v1 = face.ParameterRange
            u, v = (u0+u1)/2, (v0+v1)/2
            point, normal = face.valueAt(u, v), face.normalAt(u, v)
            axis = surface.Axis
            radial = point-surface.Center
            radial -= axis*radial.dot(axis)
            if radial.Length < 1e-9 or normal.dot(radial)/radial.Length >= -.5:
                continue
            center = surface.Center+axis*(face.CenterOfMass-surface.Center).dot(axis)
            bores.append(dict(part=identity['id'], family=identity['family_id'], radius=surface.Radius,
                              center=[center.x-origin[0], center.y-origin[1], center.z-origin[2]],
                              axis=[axis.x, axis.y, axis.z]))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/'manual_geometry.json').write_text(json.dumps(dict(units='mm', source=str(args.step.resolve()),
        origin_raw=origin.tolist(), envelope=(high-origin).tolist(), parts=parts, **errors)))
    (args.out/'bores.json').write_text(json.dumps(bores))
    print(json.dumps({'solids': len(parts), 'inner_cylinders': len(bores), **errors}))


if __name__ == '__main__':
    main()
