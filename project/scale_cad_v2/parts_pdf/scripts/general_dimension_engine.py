#!/usr/bin/env python3
"""Geometry-driven dimension plan with an explicit completeness gate.

This module never infers tolerances or a manufacturing datum.  It extracts
measurable geometry in the same local frame as parts.pdf and certifies only
solids for which a geometric reconstruction check succeeds.  Other solids are
reported as requiring their original single-part STEP in addition to a
partial dimension drawing.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
import json
import math
from pathlib import Path

import numpy as np
import trimesh

from annotate_complete_plate import (corners, extract_geometry, face_rings,
                                     mesh_volume, read_stl, signed_area)


TOL = 0.06  # mm; larger than STL quantization, smaller than useful dimensions


def grouped_circles(raw):
    """Merge split analytic arcs and opposite rims of the same cylinder."""
    groups = defaultdict(list)
    for circle in raw:
        center = np.asarray(circle["center"], dtype=float)
        axis = np.asarray(circle["axis"], dtype=float)
        axis /= np.linalg.norm(axis)
        if axis[np.argmax(np.abs(axis))] < 0:
            axis *= -1
        foot = center - axis * np.dot(center, axis)
        key = (round(2 * circle["radius"], 3),
               *np.round(foot, 2), *np.round(axis, 2))
        groups[key].append(center)
    result=[]
    for key,centers in sorted(groups.items(),
                              key=lambda item: tuple(item[1][0])):
        axis=list(key[4:7])
        normal=int(np.argmax(np.abs(axis)))
        levels=sorted({round(float(point[normal]),3) for point in centers})
        result.append(dict(diameter=key[0],
                           center=np.mean(centers,axis=0).tolist(),
                           axis=axis,rim_count=len(centers),
                           axial_levels=levels))
    return result


def _circle_identity(circle):
    """Identify one analytic cylinder independent of duplicated STEP rims."""
    return (round(float(circle["diameter"]),2),
            *(round(float(value),2) for value in circle["center"][:2]),
            *(round(float(value),2) for value in circle["axis"]))


def circular_coverage(circles, primary, multisurface, rotational_top):
    """Find every STEP cylinder not yet represented in a drawn projection."""
    covered=[]
    if primary:
        # The primary profile currently draws center chains for its first
        # three hole rows.  Later rows need an enlarged projection.
        drawn_rows={round(row["y"],3) for row in primary["x_hole_rows"][:3]}
        covered.extend(circle for circle in primary["circular_features"]
                       if round(circle["center"][1],3) in drawn_rows)
    if multisurface:
        # The all-face page has room for three dense rows and one sparse pair.
        # Its X chains all start at the same part edge, while its Y chain
        # includes every center row.  A circle on an unselected row therefore
        # needs no new page when its X station is already dimensioned in a
        # selected row (and its diameter is labelled by the all-face page).
        rows=defaultdict(list)
        for circle in multisurface["circular_features"]:
            rows[round(circle["center"][1],2)].append(circle)
        dense=sorted((row for row in rows.values() if len(row)>=3),
                     key=lambda row:-np.mean([c["center"][1] for c in row]))
        pairs=sorted((row for row in rows.values() if len(row)==2),
                     key=lambda row:np.mean([c["center"][1] for c in row]))
        drawn=dense[:3]+pairs[:1]
        x_stations={round(circle["center"][0],2)
                    for row in drawn for circle in row}
        covered.extend(circle for circle in multisurface["circular_features"]
                       if round(circle["center"][0],2) in x_stations)
    if rotational_top:
        covered.extend(circles)
    seen={_circle_identity(circle) for circle in covered}
    secondary=[circle for circle in circles
               if _circle_identity(circle) not in seen]
    return dict(total=len(circles),primary=len(circles)-len(secondary),
                secondary=len(secondary),secondary_features=secondary,
                all_features=circles)


def _face_profile(triangles, z):
    rings = face_rings(triangles, z)
    polygon = corners(rings[0])
    if not 3 <= len(polygon) <= 32:
        return None
    area = abs(signed_area(rings[0]))
    return dict(z=float(z), polygon=polygon, area=area,
                holes=rings[1:], all_rings=rings)


def planar_profiles(mesh, triangles):
    """Trace large horizontal analytic faces, not tessellated silhouette pixels."""
    z_count = Counter(np.round(mesh.vertices[:, 2], 3))
    levels = [float(z) for z, count in z_count.most_common(12)
              if count >= max(8, .004 * len(mesh.vertices))]
    result = []
    for z in sorted(set(levels), reverse=True):
        try:
            profile = _face_profile(triangles, z)
        except ValueError:
            continue
        if profile and profile["area"] > .02 * np.prod(mesh.extents[:2]):
            result.append(profile)
    return result


def contour_dimension_vertices(polygon, dimensions):
    """Discard only well-fitted sampled arcs from outline dimension stations.

    The drawn outline remains untouched.  A mesh can represent one fillet with
    dozens of tiny straight segments; dimensioning every sample creates bogus
    0.1 mm chains.  Require a long, consistently turning run that fits one
    circle closely, so small rectangular steps and irregular teeth survive.
    """
    points = np.asarray(polygon, dtype=float)
    count = len(points)
    if count < 9:
        return polygon, []
    edge_lengths = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    short_limit = max(.45, .015 * min(dimensions[:2]))
    long_edges = np.flatnonzero(edge_lengths > short_limit)
    if len(long_edges) == 0:
        return polygon, []
    # Start after a long edge so no candidate short run wraps across index 0.
    start = (int(long_edges[0]) + 1) % count
    points = np.roll(points, -start, axis=0)
    edge_vectors = np.roll(points, -1, axis=0) - points
    short = np.linalg.norm(edge_vectors, axis=1) <= short_limit
    keep = np.ones(count, dtype=bool)
    arcs = []
    index = 0
    while index < count:
        if not short[index]:
            index += 1
            continue
        end = index
        while end < count and short[end]:
            end += 1
        if end - index >= 6:
            sample = points[index:end + 1]
            steps = np.diff(sample, axis=0)
            turns = np.arctan2(
                steps[:-1, 0] * steps[1:, 1] - steps[:-1, 1] * steps[1:, 0],
                np.sum(steps[:-1] * steps[1:], axis=1),
            )
            total_turn = float(np.sum(turns))
            chord = float(np.linalg.norm(sample[-1] - sample[0]))
            if (chord >= .6 and abs(total_turn) >= math.radians(20)
                    and abs(total_turn) >= .85 * np.sum(abs(turns))):
                fit = np.column_stack((2 * sample, np.ones(len(sample))))
                rhs = np.sum(sample * sample, axis=1)
                cx, cy, constant = np.linalg.lstsq(fit, rhs, rcond=None)[0]
                radius_squared = cx * cx + cy * cy + constant
                if radius_squared > 0:
                    radius = math.sqrt(radius_squared)
                    error = np.max(abs(np.linalg.norm(sample - [cx, cy], axis=1)
                                       - radius))
                    if error <= max(.03, .012 * radius):
                        keep[index + 1:end] = False
                        arcs.append(dict(
                            start=sample[0].tolist(), end=sample[-1].tolist(),
                            midpoint=sample[len(sample) // 2].tolist(),
                            center=[float(cx), float(cy)], radius=float(radius),
                        ))
        index = end
    return [point.tolist() for point in points[keep]], arcs


def profile_dimensions(profile, dimensions, circles):
    """Return model-space dimension chains for a polygonal face and its bores."""
    polygon = profile["polygon"]
    dimension_polygon, fillet_arcs = contour_dimension_vertices(polygon, dimensions)
    # Very short strips at an envelope edge often represent sheet thickness;
    # similarly, a rounded/swept boundary may terminate just short of the
    # envelope.  Neither is a useful isolated outline station.  In particular,
    # do not turn a tangent intersection at Y=178.147 into a nominal dimension
    # when the solid envelope is Y=179.
    def meaningful_stations(values, span):
        threshold = max(TOL, .01*float(span))
        return sorted({round(float(value), 3) for value in values
                       if abs(value) <= TOL or abs(value-span) <= TOL
                       or threshold <= value <= span-threshold})

    x_stations = meaningful_stations((x for x, _ in dimension_polygon), dimensions[0])
    y_stations = meaningful_stations((y for _, y in dimension_polygon), dimensions[1])
    face_circles = []
    for circle in circles:
        if abs(circle["axis"][2]) < .995 or circle["diameter"] < 3:
            continue
        cx, cy, _ = circle["center"]
        radius = circle["diameter"] / 2
        for ring in profile["holes"]:
            xs, ys = zip(*ring)
            rcx, rcy = (min(xs) + max(xs))/2, (min(ys) + max(ys))/2
            ring_area = abs(signed_area(ring))
            if (abs(cx-rcx) < TOL and abs(cy-rcy) < TOL and
                    abs(ring_area-math.pi*radius**2) < max(.3, .015*ring_area)):
                face_circles.append(circle)
                break
    face_circles.sort(key=lambda item: (item["center"][1], item["center"][0]))
    x_hole_rows = []
    for y in sorted(set(round(c["center"][1], 3) for c in face_circles)):
        centers = sorted(set(round(c["center"][0], 3) for c in face_circles
                             if abs(c["center"][1]-y) < TOL))
        x_hole_rows.append(dict(y=y, stations=[0.0, *centers,
                                               round(float(dimensions[0]), 3)]))
    y_hole_stations = [0.0, *sorted(set(round(c["center"][1], 3)
                                            for c in face_circles))]
    # Only dimension the last center to the far edge if that edge actually
    # exists on this selected plane.  A global XYZ envelope is not a datum on
    # every face of a folded or curved part.
    if any(abs(y-dimensions[1]) <= TOL for _, y in polygon):
        y_hole_stations.append(round(float(dimensions[1]), 3))
    diameters = dict(sorted(Counter(round(c["diameter"], 3)
                                    for c in face_circles).items()))
    diagonals = []
    arc_chords = {(tuple(arc["start"]), tuple(arc["end"])) for arc in fillet_arcs}
    for a, b in zip(dimension_polygon, dimension_polygon[1:] + dimension_polygon[:1]):
        if (tuple(a), tuple(b)) in arc_chords:
            continue
        dx, dy = abs(a[0]-b[0]), abs(a[1]-b[1])
        if dx > TOL and dy > TOL:
            diagonals.append(dict(start=a, end=b, run=dx, rise=dy,
                                  angle_deg=math.degrees(math.atan2(dy, dx))))
    return dict(plane_z=profile["z"], outline=polygon,
                fillet_arcs=fillet_arcs,
                x_outline_stations=x_stations,
                y_outline_stations=y_stations,
                x_hole_rows=x_hole_rows, y_hole_stations=y_hole_stations,
                circular_features=face_circles, diameters=diameters,
                diagonal_edges=diagonals,
                inner_loops=len(profile["holes"]),
                matched_inner_loops=len(face_circles))


def certify_lid_prism(profiles, plan, mesh, triangles):
    """Certify a constant-thickness lid with downward sidewalls by volume.

    The top sheet area and wall cross-section independently predict volume.
    The check also requires each planar hole to have two analytic rims.
    """
    z_levels = sorted(set(np.round(mesh.vertices[:, 2], 2)))
    if len(z_levels) != 3 or len(profiles) < 2:
        return None
    top, mid = profiles[:2]
    bottom_z, middle_z, top_z = map(float, z_levels)
    if abs(top["z"]-top_z) > TOL or abs(mid["z"]-middle_z) > TOL:
        return None
    if (plan["matched_inner_loops"] != plan["inner_loops"] or
            len(mid["holes"]) != plan["inner_loops"] or
            not plan["circular_features"] or
            any(c["rim_count"] < 2 for c in plan["circular_features"])):
        return None
    a_top, a_mid = top["area"], mid["area"]
    wall_area = a_top-a_mid
    if wall_area <= TOL:
        return None
    try:
        bottom = face_rings(triangles, bottom_z)
    except ValueError:
        return None
    bottom_area = sum(abs(signed_area(r)) for r in bottom)
    if abs(bottom_area-wall_area) > max(.2, .002*wall_area):
        return None
    bores_area = sum(math.pi*(c["diameter"]/2)**2
                     for c in plan["circular_features"])
    expected = ((a_top-bores_area)*(top_z-middle_z) +
                wall_area*(middle_z-bottom_z))
    actual = mesh_volume(triangles)
    error = abs(actual-expected)/expected
    if error > .0005:
        return None
    return dict(method="lid-prism-volume-and-rings", actual_volume=actual,
                reconstructed_volume=expected, relative_error=error,
                wall_thickness=top_z-middle_z,
                wall_height=middle_z-bottom_z)


@lru_cache(maxsize=2)
def _analytic_cache(path):
    return json.loads(Path(path).read_text())


def tapered_bore_groups(feature, geometry_path, mesh):
    """Find STEP/mesh-verified tapered mouths on cylindrical bores.

    The STEP cache supplies exact rim diameters and levels; a middle STL
    section verifies linear radius change before any cone is annotated.
    """
    if geometry_path is None:
        return []
    geometry = _analytic_cache(str(Path(geometry_path).resolve()))
    source = geometry["parts"][feature["source_index"]]
    transform = np.asarray(feature["world_to_local"], dtype=float)
    origin = np.asarray(geometry["origin_raw"], dtype=float)
    height = float(feature["dimensions"][2])
    by_center = defaultdict(list)
    sections = {}
    for edge in source["circles"]:
        local = np.r_[np.asarray(edge["center"],float)+origin, 1.0] @ transform
        axis = np.asarray(edge["axis"],float) @ transform[:3]
        if abs(axis[2]) < .995:
            continue
        key = (round(local[0],2), round(local[1],2))
        record = (round(2*float(edge["radius"]),3),
                  round(float(local[2]),3),bool(edge.get("cylindrical")))
        by_center[key].append(record)
    candidates = []
    for center, records in by_center.items():
        records = sorted(set(records))
        cylinders = [(d,z) for d,z,kind in records if kind]
        mouths = [(d,z) for d,z,kind in records if not kind]
        for large_d,outer_z in mouths:
            on_top = abs(outer_z-height)<TOL
            on_bottom = abs(outer_z)<TOL
            if not (on_top or on_bottom):
                continue
            for small_d in sorted(set(d for d,_ in cylinders)):
                if large_d-small_d < .8:
                    continue
                levels = sorted(z for d,z in cylinders if abs(d-small_d)<.02)
                if len(levels)<2:
                    continue
                inner_z = max(levels) if on_top else min(levels)
                far_z = min(levels) if on_top else max(levels)
                depth = abs(outer_z-inner_z)
                if (depth < .3 or depth > .8*height or
                        abs(far_z-(0 if on_top else height)) > TOL):
                    continue
                # Inspect the closed bore ring halfway along the tapered wall.
                middle_z=(outer_z+inner_z)/2
                section_key=round(middle_z,4)
                if section_key not in sections:
                    sections[section_key]=mesh.section(
                        plane_origin=(0,0,middle_z),plane_normal=(0,0,1))
                section=sections[section_key]
                if section is None:
                    continue
                expected=(large_d+small_d)/4
                verified=False
                for loop in section.discrete:
                    xy=loop[:,:2]
                    radii=np.linalg.norm(xy-np.asarray(center),axis=1)
                    if (len(xy)>=24 and
                            abs(float(np.median(radii))-expected)<max(.12,.02*expected) and
                            abs(float(np.mean(radii))-expected)<max(.12,.02*expected) and
                            max(abs(np.percentile(radii,[5,95])-expected))<max(.2,.04*expected)):
                        verified=True
                        break
                if verified:
                    candidates.append(dict(center=[float(v) for v in center],
                                           bore_diameter=small_d,
                                           mouth_diameter=large_d,
                                           bore_depth=round(abs(inner_z-far_z),3),
                                           taper_depth=round(depth,3),
                                           mouth_at="top" if on_top else "bottom"))
                    break
    groups = defaultdict(list)
    for item in candidates:
        key=(item["bore_diameter"],item["mouth_diameter"],
             item["bore_depth"],item["taper_depth"],item["mouth_at"])
        groups[key].append(item["center"])
    return [dict(bore_diameter=key[0],mouth_diameter=key[1],
                 bore_depth=key[2],taper_depth=key[3],mouth_at=key[4],
                 centers=centers,count=len(centers))
            for key,centers in groups.items()]


def _local_analytic_edges(feature, geometry_path):
    """Return original STEP arcs in the same local frame as the PDF views."""
    if geometry_path is None:
        return []
    geometry = _analytic_cache(str(Path(geometry_path).resolve()))
    source = geometry["parts"][feature["source_index"]]
    transform = np.asarray(feature["world_to_local"], dtype=float)
    origin = np.asarray(geometry["origin_raw"], dtype=float)
    result = []
    for edge in source["circles"]:
        center = (np.r_[np.asarray(edge["center"], float)+origin, 1.0]
                  @ transform)[:3]
        axis = np.asarray(edge["axis"], float) @ transform[:3, :3]
        rim=edge.get("rim_candidates") or []
        sample=((np.r_[np.asarray(rim[len(rim)//2],float)+origin,1.0]
                 @ transform)[:3] if rim else None)
        edge_index=edge.get("edge_index")
        path=(source["paths"][edge_index] if isinstance(edge_index,int) and
              0<=edge_index<len(source["paths"]) else [])
        endpoints=([(np.r_[np.asarray(point,float)+origin,1.0]
                     @ transform)[:3] for point in (path[0],path[-1])]
                   if len(path)>=2 else [])
        result.append(dict(center=center, axis=axis,
                           sample=sample,
                           endpoints=endpoints,
                           radius=float(edge["radius"]),
                           arc_span=float(edge.get("arc_span", 0)),
                           cylindrical=bool(edge.get("cylindrical"))))
    return result


def bend_tangent_lines(feature, geometry_path, bend, outer_arcs):
    """Match straight STEP edges to both ends of the verified outer arc."""
    geometry=_analytic_cache(str(Path(geometry_path).resolve()))
    source=geometry["parts"][feature["source_index"]]
    transform=np.asarray(feature["world_to_local"],float)
    origin=np.asarray(geometry["origin_raw"],float)
    lines=[]
    for raw in source["paths"]:
        if len(raw)!=2:
            continue
        points=(np.c_[np.asarray(raw,float)+origin,np.ones(2)]
                @ transform)[:,:3]
        if abs(points[0,0]-points[1,0])>.05:
            continue
        length=float(np.linalg.norm(points[1,1:]-points[0,1:]))
        if length<3:
            continue
        lines.append((points,length))
    for arc in outer_arcs:
        ends=arc["endpoints"]
        if len(ends)!=2 or abs(ends[0][0])>.1:
            continue
        center=np.asarray(arc["center"])[1:]
        if any(abs(np.linalg.norm(e[1:]-center)-bend["outer_radius"])>.04
               for e in ends):
            continue
        neighbors=[]
        for end in ends:
            local=[]
            for points,length in lines:
                if abs(points[0,0]-end[0])>.05:
                    continue
                distance=np.linalg.norm(points-end,axis=1)
                if min(distance)>.04:
                    continue
                far=points[1-int(np.argmin(distance))]
                direction=(far[1:]-end[1:])/length
                radial=(end[1:]-center)/bend["outer_radius"]
                if abs(np.dot(direction,radial))>.025:
                    continue
                local.append(dict(tangent=end[1:].tolist(),far=far[1:].tolist(),
                                  length=length,
                                  angle_deg=round(math.degrees(math.atan2(
                                      abs(direction[1]),abs(direction[0]))),2)))
            neighbors.append(sorted(local,key=lambda e:-e["length"]))
        vertical=[i for i,group in enumerate(neighbors)
                  if any(abs(item["tangent"][0]-item["far"][0])<.05
                         for item in group)]
        if len(vertical)!=1 or not neighbors[1-vertical[0]]:
            continue
        before=next(item for item in neighbors[vertical[0]]
                    if abs(item["tangent"][0]-item["far"][0])<.05)
        after=neighbors[1-vertical[0]][0]
        return dict(before=before,after=after)
    return None


def stepped_side_plan(feature, geometry_path, primary_profile):
    """Verify a constant-thickness stepped flange at both long end sections.

    A bottom-face profile can start inside the global Y envelope when an
    upturned return occupies the missing strip.  It is invalid to extend the
    bottom XY outline to Y=0; show that strip in a true Y-Z side section.
    """
    if geometry_path is None or primary_profile is None:
        return None
    length,width,height=map(float,feature["dimensions"])
    outline=primary_profile["outline"]
    if (abs(primary_profile["plane_z"])>TOL or
            min(y for _,y in outline)<1 or
            max(y for _,y in outline)<width-TOL):
        return None
    geometry=_analytic_cache(str(Path(geometry_path).resolve()))
    source=geometry["parts"][feature["source_index"]]
    transform=np.asarray(feature["world_to_local"],float)
    origin=np.asarray(geometry["origin_raw"],float)
    sections=[]
    for end_x in (0.,length):
        lines=[]
        for raw in source["paths"]:
            if len(raw)!=2:
                continue
            points=(np.c_[np.asarray(raw,float)+origin,np.ones(2)]
                    @ transform)[:,:3]
            if np.max(abs(points[:,0]-end_x))<.06:
                lines.append(points[:,1:])
        def horizontal(z, left=None, right=None):
            matches=[]
            for line in lines:
                if np.max(abs(line[:,1]-z))>.06:
                    continue
                lo,hi=sorted(line[:,0])
                if (left is None or abs(lo-left)<.06) and (right is None or abs(hi-right)<.06):
                    matches.append((lo,hi))
            return matches
        bottom=[item for item in horizontal(0,right=width)
                if item[0]>1 and item[0]<.45*width]
        top=[item for item in horizontal(height,left=0)
             if 1<item[1]<.45*width]
        matched=None
        for lower_start,_ in bottom:
            for _,upper_end in top:
                thickness=upper_end-lower_start
                if not .3<thickness<5:
                    continue
                if (not horizontal(height-thickness,left=0,right=lower_start) or
                        not horizontal(thickness,left=upper_end,right=width)):
                    continue
                def vertical(y,lower_z,upper_z):
                    return any(np.max(abs(line[:,0]-y))<.06 and
                               abs(min(line[:,1])-lower_z)<.06 and
                               abs(max(line[:,1])-upper_z)<.06
                               for line in lines)
                if (vertical(lower_start,0,height-thickness) and
                        vertical(upper_end,thickness,height)):
                    matched=(lower_start,upper_end,thickness)
                    break
            if matched:
                break
        if matched is None:
            return None
        sections.append(matched)
    if max(abs(a-b) for a,b in zip(*sections))>.06:
        return None
    lower_start,upper_end,thickness=sections[0]
    if abs(min(y for _,y in outline)-lower_start)>.06:
        return None
    return dict(lower_start=round(lower_start,3),
                upper_end=round(upper_end,3),
                thickness=round(thickness,3),
                width=round(width,3),height=round(height,3),
                outline=[[0,height],[upper_end,height],
                         [upper_end,thickness],[width,thickness],
                         [width,0],[lower_start,0],
                         [lower_start,height-thickness],
                         [0,height-thickness]])


def rotational_axial_features(feature, geometry_path, mesh):
    """Measure a concentric flange's axial outline from STEP and an STL cut."""
    if geometry_path is None:
        return None
    dims=np.asarray(feature["dimensions"],float)
    if abs(dims[0]-dims[1])>.01*max(dims[:2]):
        return None
    center=dims[:2]/2
    rims=sorted({(round(2*e["radius"],3),round(float(e["center"][2]),3),
                  e["cylindrical"])
                 for e in _local_analytic_edges(feature,geometry_path)
                 if abs(e["axis"][2])>.995 and
                 np.linalg.norm(e["center"][:2]-center)<.15})
    cylinders={d:sorted(z for dd,z,kind in rims if kind and dd==d)
               for d,_,kind in rims if kind}
    diameters=sorted(cylinders,reverse=True)
    if len(diameters)!=3:
        return None
    outer,boss,bore=diameters
    outer_z,boss_z,bore_z=(cylinders[d] for d in diameters)
    height=float(dims[2])
    if (abs(outer-max(dims[:2]))>.15 or outer/boss<1.3 or boss/bore<1.3 or
            len(outer_z)<2 or len(boss_z)<2 or len(bore_z)<2 or
            abs(outer_z[0]-boss_z[-1])>.08 or
            abs(outer_z[-1]-height)>.08 or
            abs(bore_z[0])>.08 or abs(bore_z[-1]-height)>.08):
        return None
    section=mesh.section(plane_origin=(0,float(center[1]),0),
                         plane_normal=(0,1,0))
    if section is None or len(section.discrete)!=2:
        return None
    loops=section.discrete
    if any(np.linalg.norm(loop[0]-loop[-1])>.05 for loop in loops):
        return None
    right=max(loops,key=lambda loop:np.max(loop[:,0]))
    if np.min(right[:,0])<center[0]-.1:
        return None
    radii=right[:,0]-center[0]
    zs=right[:,2]
    if (abs(np.min(radii)-bore/2)>.1 or
            abs(np.max(radii)-outer/2)>.1):
        return None
    result=dict(outer_diameter=outer,boss_diameter=boss,
                bore_diameter=bore,boss_height=boss_z[-1],
                flange_height=outer_z[-1]-outer_z[0],
                grooves=[],bevel=None)
    # Certify a concave semicircular groove by matching its STEP rings to
    # the radius of the actual cross-section points.
    for diameter,z,kind in rims:
        if kind or not boss<diameter<outer:
            continue
        lower=max((level for level in outer_z if level<z),default=None)
        upper=min((level for level in outer_z if level>z),default=None)
        if lower is None or upper is None:
            continue
        depth=(outer-diameter)/2
        if (depth<.35 or abs((upper-lower)/2-depth)>.08 or
                abs(z-(lower+upper)/2)>.08):
            continue
        selected=(zs>lower+.04)&(zs<upper-.04)&(radii>diameter/2-.1)
        errors=np.abs(np.hypot(radii[selected]-outer/2,zs[selected]-z)-depth)
        if len(errors)>=20 and np.percentile(errors,95)<.07:
            result["grooves"].append(dict(lower=lower,upper=upper,
                                          mid=z,radius=round(depth,3)))
    # A bottom bevel is measured only when the near-bottom mesh points fit
    # the run/rise between its STEP ring and the boss cylinder.
    for diameter,z,kind in rims:
        if kind or abs(z)>.08 or not bore<diameter<boss:
            continue
        run=(boss-diameter)/2
        rise=boss_z[0]-z
        if run<.3 or rise<.3:
            continue
        selected=(zs>z+.04)&(zs<boss_z[0]-.04)&(radii>diameter/2-.1)
        errors=np.abs(radii[selected]-(diameter/2+run*(zs[selected]-z)/rise))
        if len(errors)>=2 and np.percentile(errors,95)<.07:
            result["bevel"]=dict(run=round(run,3),rise=round(rise,3))
            break
    return result


def coaxial_step_heights(feature, mesh):
    """Recognize two exterior Z cylinders meeting at one shoulder.

    Analytic STEP rims supply the diameters and stations; independent STL
    sections check that each cylinder is on the outside, not a counterbore.
    This also works for a circular flange clipped by two parallel flats.
    """
    dims=np.asarray(feature["dimensions"],float)
    center=dims[:2]/2
    circles=grouped_circles(feature["circles"])
    coaxial=[circle for circle in circles
             if abs(circle["axis"][2])>.995 and
             np.linalg.norm(np.asarray(circle["center"][:2])-center)<.15 and
             len(circle["axial_levels"])>=2]
    if len(coaxial)<3:
        return None
    height=float(dims[2])
    outer_candidates=[circle for circle in coaxial
                      if abs(max(circle["axial_levels"])-height)<.08 and
                      min(circle["axial_levels"])>.8 and
                      abs(circle["diameter"]-max(dims[:2]))<.2]
    for outer in sorted(outer_candidates,key=lambda item:-item["diameter"]):
        shoulder=min(outer["axial_levels"])
        bosses=[circle for circle in coaxial
                if abs(min(circle["axial_levels"]))<.08 and
                abs(max(circle["axial_levels"])-shoulder)<.08 and
                outer["diameter"]>1.2*circle["diameter"]]
        for boss in sorted(bosses,key=lambda item:-item["diameter"]):
            bores=[circle for circle in coaxial
                   if abs(min(circle["axial_levels"]))<.08 and
                   abs(max(circle["axial_levels"])-height)<.08 and
                   boss["diameter"]>1.2*circle["diameter"]]
            if not bores:
                continue
            # Reject counterbores: a true exterior cylinder determines the
            # radial envelope of horizontal mesh cuts through each segment.
            valid=True
            for cylinder,z in ((boss,shoulder/2),(outer,(shoulder+height)/2)):
                section=mesh.section(plane_origin=(0,0,float(z)),
                                     plane_normal=(0,0,1))
                if section is None:
                    valid=False
                    break
                radius=max(np.linalg.norm(np.asarray(path)[:,:2]-center,axis=1).max()
                           for path in section.discrete)
                if abs(radius-cylinder["diameter"]/2)>.2:
                    valid=False
                    break
            if valid:
                bore=min(bores,key=lambda item:item["diameter"])
                return dict(outer_diameter=outer["diameter"],
                            boss_diameter=boss["diameter"],
                            bore_diameter=bore["diameter"],
                            boss_height=round(shoulder,3),
                            flange_height=round(height-shoulder,3))
    return None


def radial_stepped_bores(feature, mesh):
    """Measure two-stage radial drills through a concentric annular wall.

    The STEP rims locate a common-axis diameter transition.  Concentric
    Z cylinders locate the inner and outer wall.  Two independent mesh cuts
    perpendicular to the drill verify that the two cylinder diameters really
    occur on their respective sides of the transition.
    """
    circles=grouped_circles(feature["circles"])
    horizontal=[c for c in circles
                if abs(c["axis"][2])<.05 and
                np.linalg.norm(c["axis"][:2])>.995]
    vertical=[c for c in circles if abs(c["axis"][2])>.995 and
              len(c["axial_levels"])>=2]
    groups=defaultdict(list)
    for circle in horizontal:
        axis=np.asarray(circle["axis"][:2],float)
        axis/=np.linalg.norm(axis)
        center=np.asarray(circle["center"],float)
        key=(*(round(float(value),1) for value in center),
             round(abs(axis[0]),2),round(abs(axis[1]),2))
        groups[key].append(circle)
    result=[]
    for group in groups.values():
        unique={round(c["diameter"],3):c for c in group}
        if len(unique)!=2:
            continue
        small,large=(unique[d] for d in sorted(unique))
        step=np.asarray(small["center"],float)
        if np.linalg.norm(step-np.asarray(large["center"],float))>.08:
            continue
        candidates=[]
        for outer in vertical:
            origin=np.asarray(outer["center"][:2],float)
            r_outer=outer["diameter"]/2
            z=float(step[2])
            if not (min(outer["axial_levels"])+.15<z<
                    max(outer["axial_levels"])-.15):
                continue
            for inner in vertical:
                if (inner is outer or
                        np.linalg.norm(np.asarray(inner["center"][:2])-origin)>.1 or
                        not (min(inner["axial_levels"])+.15<z<
                             max(inner["axial_levels"])-.15)):
                    continue
                r_inner=inner["diameter"]/2
                radius=np.linalg.norm(step[:2]-origin)
                if not (r_inner+.6<radius<r_outer-.6):
                    continue
                outward=(step[:2]-origin)/radius
                axis=np.asarray(small["axis"][:2],float)
                axis/=np.linalg.norm(axis)
                if abs(float(np.dot(outward,axis)))<.995:
                    continue
                verified=True
                for test_radius,expected in (((r_inner+radius)/2,
                                              small["diameter"]/2),
                                             ((radius+r_outer)/2,
                                              large["diameter"]/2)):
                    point=np.r_[origin+test_radius*outward,z]
                    normal=np.r_[outward,0.]
                    section=mesh.section(plane_origin=point,
                                         plane_normal=normal)
                    if section is None:
                        verified=False
                        break
                    matching=False
                    for path in section.discrete:
                        vertices=np.asarray(path,float)
                        if (len(vertices)<24 or
                                np.linalg.norm(vertices[0]-vertices[-1])>.08):
                            continue
                        delta=vertices-point
                        transverse=delta-np.outer(delta@normal,normal)
                        distances=np.linalg.norm(transverse,axis=1)
                        error=np.percentile(np.abs(distances-expected),95)
                        if error<max(.08,.025*expected):
                            matching=True
                            break
                    if not matching:
                        verified=False
                        break
                if verified:
                    candidates.append(dict(center=origin.tolist(),
                                           step=step.tolist(),
                                           axis=outward.tolist(),
                                           inner_diameter=small["diameter"],
                                           outer_diameter=large["diameter"],
                                           inner_wall_diameter=inner["diameter"],
                                           outer_wall_diameter=outer["diameter"],
                                           inner_run=float(round(radius-r_inner,3)),
                                           outer_run=float(round(r_outer-radius,3)),
                                           angle_deg=round(math.degrees(
                                               math.atan2(outward[1],outward[0]))%360,3)))
        if candidates:
            # Choose the widest verified annular wall if nested concentric
            # surfaces produce multiple otherwise valid pairs.
            result.append(max(candidates,key=lambda x:x["inner_run"]+x["outer_run"]))
    return result


def _section_has_circular_rim(mesh, axis, station, center, radius):
    """Check an analytic STEP rim against an independent mesh cross-section."""
    normal=np.eye(3)[axis]
    origin=np.zeros(3)
    origin[axis]=station
    section=mesh.section(plane_origin=origin,plane_normal=normal)
    if section is None:
        return False
    transverse=[index for index in range(3) if index!=axis]
    for loop in section.discrete:
        points=np.asarray(loop)[:,transverse]
        if len(points)<24 or np.linalg.norm(points[0]-points[-1])>.1:
            continue
        distances=np.linalg.norm(points-np.asarray(center),axis=1)
        if (abs(float(np.median(distances))-radius)<.09 and
                np.percentile(np.abs(distances-radius),95)<.12):
            return True
    return False


def stepped_bore_axial_chains(feature, mesh, circles):
    """Find repeated two-diameter straight bores with a shared axial shoulder.

    The measured runs are from actual STEP rim planes.  A mesh section in
    each stage verifies the bore before either distance is drawn.
    """
    dimensions=np.asarray(feature["dimensions"],float)
    by_axis_and_center=defaultdict(list)
    for circle in circles:
        axis=int(np.argmax(np.abs(circle["axis"])))
        if abs(circle["axis"][axis])<.995 or len(circle["axial_levels"])<2:
            continue
        transverse=[index for index in range(3) if index!=axis]
        key=(axis,*(round(float(circle["center"][index]),2)
                    for index in transverse))
        by_axis_and_center[key].append(circle)
    candidates=defaultdict(list)
    for (axis,*center),group in by_axis_and_center.items():
        if axis==2:
            continue
        span=float(dimensions[axis])
        for small in group:
            for large in group:
                if large["diameter"]-small["diameter"]<.5:
                    continue
                small_lo,small_hi=min(small["axial_levels"]),max(small["axial_levels"])
                large_lo,large_hi=min(large["axial_levels"]),max(large["axial_levels"])
                if (abs(small_lo)>.08 or abs(small_hi-large_lo)>.08 or
                        abs(large_hi-span)>.08 or
                        small_hi-small_lo<1 or large_hi-large_lo<1):
                    continue
                if not all(_section_has_circular_rim(
                        mesh,axis,(lo+hi)/2,center,diameter/2)
                           for lo,hi,diameter in ((small_lo,small_hi,small["diameter"]),
                                                   (large_lo,large_hi,large["diameter"]))):
                    continue
                key=(axis,round(small_hi,3),round(span-small_hi,3),
                     round(small["diameter"],3),round(large["diameter"],3))
                candidates[key].append(list(center))
    return [dict(axis=key[0],stages=[key[1],key[2]],
                 diameters=[key[3],key[4]],count=len(centers),
                 centers=sorted(centers))
            for key,centers in sorted(candidates.items())]


def longitudinal_shaft_stations(feature, mesh, circles):
    """Locate the major shoulders of a long, coaxial stepped shaft."""
    dimensions=np.asarray(feature["dimensions"],float)
    axis=int(np.argmax(dimensions))
    span=float(dimensions[axis])
    if span/max(float(dimensions[i]) for i in range(3) if i!=axis)<8:
        return None
    aligned=[circle for circle in circles
             if abs(circle["axis"][axis])>.995 and
             len(circle["axial_levels"])>=2]
    if len({round(c["diameter"],2) for c in aligned})<3:
        return None
    transverse=[index for index in range(3) if index!=axis]
    center=np.median([circle["center"] for circle in aligned],axis=0)[transverse]
    aligned=[circle for circle in aligned
             if np.linalg.norm(np.asarray(circle["center"])[transverse]-center)<.15]
    if len(aligned)<3:
        return None
    min_plateau=max(3.,.018*span)
    plateaus=[]
    for circle in aligned:
        if circle["diameter"]<3:
            continue
        # The same diameter can reappear at both ends of a shaft.  Grouped
        # STEP rims therefore give candidate intervals, not one long span.
        for low,high in zip(circle["axial_levels"],
                            circle["axial_levels"][1:]):
            if high-low<min_plateau:
                continue
            midpoint=(low+high)/2
            if not _section_has_circular_rim(
                    mesh,axis,midpoint,center,circle["diameter"]/2):
                continue
            plateaus.append((low,high,circle["diameter"]))
    if len(plateaus)<3:
        return None
    plateaus=sorted(set(plateaus))
    # Tiny grooves and end chamfers do not define a main axial station.
    major=[]
    for left,right in zip(plateaus,plateaus[1:]):
        if (0<=right[0]-left[1]<max(1.,.006*span) and
                abs(right[2]-left[2])>=1.):
            major.append(round(left[1] if left[2]>right[2] else right[0],3))
    stations=[0.,*sorted(set(major)),round(span,3)]
    if len(stations)<4:
        return None
    return dict(axis=axis,stations=stations,
                diameters=sorted({round(p[2],3) for p in plateaus}),
                method="STEP rims and verified circular mesh sections")


def needs_step_first_page(dimensions, circles, profile, certificate):
    """Route a densely drilled slender nonplanar solid to exact STEP geometry."""
    if profile or certificate:
        return False
    axis=int(np.argmax(dimensions))
    if float(dimensions[axis])/max(float(dimensions[i])
                                   for i in range(3) if i!=axis)<8:
        return False
    transverse=[circle for circle in circles if abs(circle["axis"][axis])<.1]
    return (len(transverse)>=12 and
            len({round(circle["center"][axis],1) for circle in transverse})>=8 and
            len({round(circle["diameter"],1) for circle in transverse})>=3)


def _section_z_at_y(section, y):
    intersections = []
    for path in section.discrete:
        for a, b in zip(path, path[1:]):
            if abs(a[1]-b[1]) < 1e-6:
                continue
            if (a[1]-y)*(b[1]-y) <= 0:
                z = a[2]+(y-a[1])*(b[2]-a[2])/(b[1]-a[1])
                intersections.append(float(z))
    return sorted({round(z, 3) for z in intersections})


def orthogonal_bore_plan(circles, mesh, dimensions):
    """Verify a full-height bore and group the intersecting blind drills.

    The finely faceted projection of a true cylindrical bore is not treated
    as a pattern of separate ribs.  A middle mesh section must match the STEP
    bore before its side-view tangent positions are dimensioned.
    """
    length,width,height=map(float,dimensions)
    candidates=sorted((circle for circle in circles
                       if abs(circle["axis"][2])>.995 and
                       circle["diameter"]>.2*min(length,width) and
                       any(abs(z)<.08 for z in circle["axial_levels"]) and
                       any(abs(z-height)<.08 for z in circle["axial_levels"])),
                      key=lambda circle:-circle["diameter"])
    if not candidates:
        return None
    bore=candidates[0]
    center=np.asarray(bore["center"][:2],float)
    radius=bore["diameter"]/2
    if not (radius<center[0]<length-radius and
            radius<center[1]<width-radius):
        return None
    section=mesh.section(plane_origin=(0,0,height/2),plane_normal=(0,0,1))
    if section is None:
        return None
    if not any(len(loop)>=24 and
               abs(float(np.median(np.linalg.norm(loop[:,:2]-center,axis=1)))-radius)<.08 and
               float(np.percentile(abs(np.linalg.norm(loop[:,:2]-center,axis=1)-radius),95))<.12
               for loop in section.discrete):
        return None
    top_drills=defaultdict(list)
    side_drills=defaultdict(list)
    for circle in circles:
        levels=circle["axial_levels"]
        if len(levels)<2:
            continue
        low,high=min(levels),max(levels)
        if (abs(circle["axis"][2])>.995 and
                abs(high-height)<.08 and low>.5 and
                high-low>2 and circle["diameter"]<bore["diameter"]*.7):
            top_drills[(round(circle["diameter"],3),round(high-low,3),
                        round(low,3))].append(circle)
        if (abs(circle["axis"][1])>.995 and
                high>width-.65 and high-low>2):
            side_drills[(round(circle["diameter"],3),round(high-low,3),
                         round(low,3),round(high,3))].append(circle)
    top_blind=[dict(diameter=key[0],depth=key[1],bottom_z=key[2],
                    count=len(group),centers=[c["center"] for c in group])
               for key,group in sorted(top_drills.items()) if len(group)>=2]
    if not top_blind or not side_drills:
        return None
    return dict(
        through_bore=dict(diameter=bore["diameter"],
                          center=[round(float(v),3) for v in bore["center"]],
                          y_tangents=[round(float(center[1]-radius),3),
                                      round(float(center[1]+radius),3)]),
        top_blind=top_blind,
        side_blind=[dict(diameter=key[0],depth=key[1],start_y=key[2],
                         end_y=key[3],count=len(group),
                         centers=[c["center"] for c in group])
                    for key,group in sorted(side_drills.items())])


def vertical_axial_bore_depths(circles, mesh, dimensions):
    """Group verified STEP-rim bore spans at exterior or recessed Z faces.

    A matching *inner* loop in a mid-depth mesh section distinguishes a real
    drilled void from an external cylindrical boss carrying the same rims.
    The far rim may meet another bore stage, a cone, or a local exterior face.
    """
    height = float(dimensions[2])
    face_areas = defaultdict(float)
    for triangle, center, normal, area in zip(
            mesh.triangles, mesh.triangles_center, mesh.face_normals,
            mesh.area_faces):
        if (abs(float(normal[2])) > .999 and
                np.ptp(triangle[:, 2]) < .02 and
                .5 < center[2] < height - .5):
            face_areas[round(float(center[2]), 2)] += float(area)
    interior_faces = [level for level, area in face_areas.items()
                      if area >= max(25., .01 * dimensions[0] * dimensions[1])]
    grouped = defaultdict(list)
    section_cache = {}
    for circle in circles:
        if (abs(circle["axis"][2]) < .995 or
                circle["diameter"] < 3 or
                len(circle["axial_levels"]) != 2):
            continue
        low, high = sorted(float(value) for value in circle["axial_levels"])
        depth = high - low
        if depth < max(1., .02 * height) or depth > .95 * height:
            continue
        if abs(high-height) < .08 and low > .5:
            side = "top"
        elif abs(low) < .08 and high < height-.5:
            side = "bottom"
        elif (any(abs(high-level) < .08 for level in interior_faces) and
              not any(abs(low-level) < .08 for level in interior_faces)):
            side = "top"
        elif (any(abs(low-level) < .08 for level in interior_faces) and
              not any(abs(high-level) < .08 for level in interior_faces)):
            side = "bottom"
        else:
            continue
        station = round((low+high)/2, 4)
        if station not in section_cache:
            section_cache[station] = mesh.section(
                plane_origin=(0, 0, station), plane_normal=(0, 0, 1))
        section = section_cache[station]
        if section is None:
            continue
        loops = []
        for loop in section.discrete:
            points = np.asarray(loop[:, :2], dtype=float)
            area = abs(float(np.dot(points[:, 0], np.roll(points[:, 1], -1)) -
                             np.dot(points[:, 1], np.roll(points[:, 0], -1)))) / 2
            loops.append((area, points))
        if len(loops) < 2:
            continue
        outer_area = max(area for area, _ in loops)
        center = np.asarray(circle["center"][:2], dtype=float)
        radius = circle["diameter"] / 2
        if not any(
                len(points) >= 24 and area < .8 * outer_area and
                abs(float(np.median(np.linalg.norm(points-center, axis=1))) - radius)
                < max(.08, .01 * radius) and
                float(np.percentile(abs(np.linalg.norm(points-center, axis=1) -
                                        radius), 95)) < max(.12, .02 * radius)
                for area, points in loops):
            continue
        key = (side, round(float(circle["diameter"]), 3), round(depth, 3),
               round(low, 3), round(high, 3))
        grouped[key].append([round(float(value), 3)
                             for value in circle["center"][:2]])
    result = []
    for (side, diameter, depth, low, high), centers in sorted(grouped.items()):
        interior_entry = ((side == "top" and high < height - .08) or
                          (side == "bottom" and low > .08))
        if len(centers) < 2 and not interior_entry:
            continue
        result.append(dict(side=side, diameter=diameter, depth=depth,
                           low=low, high=high, count=len(centers),
                           entry_z=high if side == "top" else low,
                           centers=sorted(centers)))
    return result


def projected_planar_stations(mesh, dimensions):
    """Find long, axis-aligned structural edges in a nonplanar TOP view.

    A shell may have no single planar face suitable for a polygonal profile,
    yet its axial end faces and longitudinal wall faces still define useful
    projected dimensions.  Require multiple substantial planes in both axes;
    this deliberately avoids turning tessellated curves into false stations.
    """
    length,width,height=map(float,dimensions)
    if length<1.5*max(width,height):
        return None
    triangles=mesh.triangles
    normals=mesh.face_normals
    areas=mesh.area_faces
    detected=[]
    for axis in (0,1):
        records=defaultdict(lambda: [0.,float("inf"),-float("inf"),
                                     float("inf"),-float("inf")])
        mask=(np.abs(normals[:,axis])>.998)&(
            np.ptp(triangles[:,:,axis],axis=1)<.03)
        other=1-axis
        for triangle,area in zip(triangles[mask],areas[mask]):
            position=round(float(np.mean(triangle[:,axis])),2)
            record=records[position]
            record[0]+=float(area)
            record[1]=min(record[1],float(np.min(triangle[:,other])))
            record[2]=max(record[2],float(np.max(triangle[:,other])))
            record[3]=min(record[3],float(np.min(triangle[:,2])))
            record[4]=max(record[4],float(np.max(triangle[:,2])))
        if axis==0:
            positions=[value for value,record in records.items()
                       if record[0]>=.02*width*height]
            span=length
        else:
            positions=[value for value,record in records.items()
                       if record[0]>=.0004*length*height and
                       record[2]-record[1]>=.1*length and
                       record[4]-record[3]>=.2*height]
            span=width
        stations=sorted({0.,round(span,2),
                         *(value for value in positions if .05<value<span-.05)})
        detected.append(stations)
    x_stations,y_stations=detected
    if not (4<=len(x_stations)<=8 and 4<=len(y_stations)<=10):
        return None
    return dict(x_stations=x_stations,y_stations=y_stations,
                method="verified-axis-aligned-mesh-planes")


def verified_local_tapers(feature, geometry_path, mesh, flat_gauges):
    """Detect countersinks on a local sheet, not just at global Z extrema."""
    by_center=defaultdict(list)
    for edge in _local_analytic_edges(feature,geometry_path):
        if abs(edge["axis"][2])<.995 or edge["radius"]<1.5:
            continue
        key=tuple(round(float(v),2) for v in edge["center"][:2])
        by_center[key].append((round(2*edge["radius"],3),
                               round(float(edge["center"][2]),3),
                               edge["cylindrical"]))
    verified=[]
    section_cache={}
    for center,records in by_center.items():
        cylinders=defaultdict(set)
        mouths=set()
        for diameter,z,is_cylinder in records:
            (cylinders[diameter].add(z) if is_cylinder else
             mouths.add((diameter,z)))
        for bore_diameter,levels in cylinders.items():
            if len(levels)<2:
                continue
            lo,hi=min(levels),max(levels)
            for mouth_diameter,mouth_z in mouths:
                if mouth_diameter-bore_diameter<.8:
                    continue
                if mouth_z<lo-.3:
                    inner_z,far_z=lo,hi
                    mouth_at="bottom"
                elif mouth_z>hi+.3:
                    inner_z,far_z=hi,lo
                    mouth_at="top"
                else:
                    continue
                taper_depth=abs(inner_z-mouth_z)
                wall_depth=abs(far_z-mouth_z)
                if not (.3<taper_depth<8 and
                        any(abs(g["thickness"]-wall_depth)<.08 for g in flat_gauges)):
                    continue
                middle_z=round((mouth_z+inner_z)/2,4)
                if middle_z not in section_cache:
                    section_cache[middle_z]=mesh.section(
                        plane_origin=(0,0,middle_z),plane_normal=(0,0,1))
                section=section_cache[middle_z]
                if section is None:
                    continue
                expected=(mouth_diameter+bore_diameter)/4
                for loop in section.discrete:
                    xy=loop[:,:2]
                    radii=np.linalg.norm(xy-np.asarray(center),axis=1)
                    if (len(xy)>=24 and
                            abs(float(np.median(radii))-expected)<.08 and
                            np.max(abs(np.percentile(radii,[5,95])-expected))<.12):
                        verified.append(dict(center=list(center),
                                             bore_diameter=bore_diameter,
                                             mouth_diameter=mouth_diameter,
                                             taper_depth=round(taper_depth,3),
                                             wall_depth=round(wall_depth,3),
                                             mouth_at=mouth_at))
                        break
    groups=defaultdict(list)
    for item in verified:
        key=(item["bore_diameter"],item["mouth_diameter"],
             item["taper_depth"],item["wall_depth"],item["mouth_at"])
        groups[key].append(item["center"])
    return [dict(bore_diameter=key[0],mouth_diameter=key[1],
                 taper_depth=key[2],wall_depth=key[3],mouth_at=key[4],
                 centers=sorted(centers),count=len(centers))
            for key,centers in sorted(groups.items())]


def multi_surface_bend_plan(feature, mesh, circles, geometry_path):
    """Locate holes on every face and independently verify an extruded bend.

    The selected planar face remains useful for simple solids, but is never
    treated as the entire hole inventory on a folded shell.  A bend is only
    dimensioned when two concentric STEP arcs at both long ends agree with
    the same section thickness measured in the STL.
    """
    visible = [c for c in circles if c["diameter"] >= 3 and
               c["rim_count"] >= 2 and abs(c["axis"][2]) > .90]
    if len(visible) < 6 or np.ptp([c["center"][2] for c in visible]) < 2:
        return None
    dimensions = feature["dimensions"]
    length, width, height = map(float, dimensions)
    arcs = [e for e in _local_analytic_edges(feature, geometry_path)
            if e["cylindrical"] and abs(e["axis"][0]) > .995 and
            .3 < e["arc_span"] < math.pi and 2 < e["radius"] < .6*min(width,height)]
    by_center = defaultdict(list)
    for edge in arcs:
        key = (round(edge["center"][1], 2), round(edge["center"][2], 2))
        by_center[key].append(edge)
    candidate_bends = []
    for (cy,cz), group in by_center.items():
        radii = sorted({round(e["radius"], 3) for e in group})
        for inner, outer in zip(radii, radii[1:]):
            thickness = outer-inner
            if not .3 < thickness < 6:
                continue
            inner_edges = [e for e in group if abs(e["radius"]-inner)<.02]
            outer_edges = [e for e in group if abs(e["radius"]-outer)<.02]
            if (len(inner_edges)<2 or len(outer_edges)<2 or
                    max(e["center"][0] for e in inner_edges)-
                    min(e["center"][0] for e in inner_edges)<.8*length or
                    max(e["center"][0] for e in outer_edges)-
                    min(e["center"][0] for e in outer_edges)<.8*length):
                continue
            samples=[e["sample"] for e in inner_edges if e["sample"] is not None]
            if not samples:
                continue
            z_offset=float(np.median([sample[2]-cz for sample in samples]))
            if abs(z_offset)<.3:
                continue
            outer_at_ends=[e for e in outer_edges if e["endpoints"]]
            candidate_bends.append(dict(center_y=cy, center_z=cz,
                                        inner_radius=inner,outer_radius=outer,
                                        thickness=round(thickness,3),
                                        z_direction=1 if z_offset>0 else -1,
                                        arc_span=min(e["arc_span"] for e in group),
                                        outer_arcs=outer_at_ends))
    if not candidate_bends:
        return None
    bend = max(candidate_bends,key=lambda e:e["arc_span"]*e["outer_radius"])
    outer_arcs=bend.pop("outer_arcs")
    bend["tangent_lines"]=bend_tangent_lines(feature,geometry_path,bend,outer_arcs)
    # Two independent sections avoid treating a local emboss as a global
    # sheet gauge.  Sampling on clean, flat regions returns the actual pair
    # of opposite surfaces, not a guessed nominal thickness.
    samples = []
    for x in (.2*length, .55*length):
        section = mesh.section(plane_origin=(x,0,0),plane_normal=(1,0,0))
        if section is None:
            return None
        for fraction in (.1,.3,.45,.6,.75,.84,.88):
            y = fraction*width
            levels = _section_z_at_y(section,y)
            # A cut can pass through two disjoint sheets.  Consecutive entry /
            # exit intersections are each a physical wall pair; a large gap
            # between the pairs is air, not the local material thickness.
            if len(levels) in (2,4,6):
                for lower,upper in zip(levels[::2],levels[1::2]):
                    thickness=round(upper-lower,3)
                    delta=min(1.,.01*width)
                    neighboring=[]
                    for offset in (-delta,delta):
                        near=_section_z_at_y(section,y+offset)
                        pairs=list(zip(near[::2],near[1::2])) if len(near) in (2,4,6) else []
                        neighboring.append(any(abs(lo-lower)<.025 and abs(hi-upper)<.025
                                               for lo,hi in pairs))
                    if .3<thickness<8 and all(neighboring):
                        samples.append((fraction,thickness,lower))
    counts=Counter(t for _,t,_ in samples)
    gauges=[]
    for thickness,count in sorted(counts.items()):
        if count<2:
            continue
        instances=[(f,z) for f,t,z in samples if t==thickness]
        # Prefer a section where that gauge is measured at the same Z in
        # both independent X cuts.  This avoids an unrepeatable local pocket.
        by_position=Counter((round(f,3),round(z,2)) for f,z in instances)
        verified=[(f,z,n) for (f,z),n in by_position.items() if n>=2]
        if not verified:
            continue
        fraction,base_z,_=max(verified,key=lambda item:(item[2],item[0]))
        gauges.append(dict(thickness=thickness,y_fraction=fraction,
                           base_z=base_z))
    if not any(abs(g["thickness"]-bend["thickness"])<.08 for g in gauges):
        return None
    local_tapers=verified_local_tapers(feature,geometry_path,mesh,gauges)
    return dict(circular_features=visible,bend=bend,flat_gauges=gauges,
                local_tapered_bores=local_tapers,
                # The inventory is measured from STEP, not from one selected
                # horizontal face; dimensions remain partial until certified.
                feature_count=len(visible))


def broad_interior_wall_span(mesh, dimensions):
    """Measure the clear TOP-view gap between a pair of long inner walls.

    Two large, parallel Y-normal faces are required on each side of the
    central gap.  This rejects hole rows and small ribs, whose projected
    stations alone do not define the width of the central opening.
    """
    length,width,height=map(float,dimensions)
    if min(length,width,height)<5:
        return None
    records=defaultdict(lambda:[0.,float("inf"),-float("inf"),
                                float("inf"),-float("inf")])
    mask=(np.abs(mesh.face_normals[:,1])>.999)&(
        np.ptp(mesh.triangles[:,:,1],axis=1)<.025)
    for triangle,area in zip(mesh.triangles[mask],mesh.area_faces[mask]):
        station=round(float(np.mean(triangle[:,1])),2)
        rec=records[station]
        rec[0]+=float(area)
        rec[1]=min(rec[1],float(np.min(triangle[:,0])))
        rec[2]=max(rec[2],float(np.max(triangle[:,0])))
        rec[3]=min(rec[3],float(np.min(triangle[:,2])))
        rec[4]=max(rec[4],float(np.max(triangle[:,2])))
    broad=sorted(station for station,rec in records.items()
                 if rec[0]>.25*length*height and
                 rec[2]-rec[1]>.75*length and
                 rec[4]-rec[3]>.5*height and
                 .05*width<station<.95*width)
    midpoint=width/2
    low=[value for value in broad if value<midpoint]
    high=[value for value in broad if value>midpoint]
    if len(low)<2 or len(high)<2:
        return None
    inner_low,inner_high=max(low),min(high)
    if inner_high-inner_low<.45*width:
        return None
    return dict(axis=1,low=inner_low,high=inner_high,
                span=round(inner_high-inner_low,3),
                method="paired broad STEP-aligned mesh wall planes")


def top_circular_rims(mesh, dimensions, circles):
    """Measure dense complete circular edges on the upper face of a small part.

    This captures a real circular lip even when STEP circle extraction misses
    the rim of a tapered opening. It does not infer a polygon's size.
    """
    length,width,height=map(float,dimensions)
    # One known coaxial STEP circle provides an unambiguous center while
    # avoiding duplicate labels on already dimensioned multi-ring parts.
    if len(circles)!=1 or min(length,width)<2.5 or height<1:
        return []
    axial=[c for c in circles if abs(c["axis"][2])>.995]
    if not axial:
        return []
    center=np.median([c["center"][:2] for c in axial],axis=0)
    vertices=mesh.vertices
    top=vertices[np.abs(vertices[:,2]-height)<.002,:2]
    if len(top)<48:
        return []
    radii=np.linalg.norm(top-center,axis=1)
    groups=defaultdict(list)
    for point,radius in zip(top,radii):
        if .2<radius<.85*min(length,width)/2:
            groups[round(float(radius),2)].append((point,radius))
    found=[]
    for samples in groups.values():
        if len(samples)<48:
            continue
        points=np.asarray([item[0] for item in samples])
        distances=np.asarray([item[1] for item in samples])
        if np.ptp(distances)>.01:
            continue
        angles=np.mod(np.arctan2(points[:,1]-center[1],
                                 points[:,0]-center[0]),2*np.pi)
        angles=np.sort(np.unique(np.round(angles,4)))
        if len(angles)<48 or np.max(np.diff(np.r_[angles,angles[0]+2*np.pi]))>.2:
            continue
        diameter=round(2*float(np.median(distances)),3)
        if any(abs(diameter-c["diameter"])<.03 for c in axial):
            continue
        found.append(dict(center=center.tolist(),diameter=diameter,
                          level=round(height,3),method="complete top mesh rim"))
    return sorted(found,key=lambda item:item["diameter"])


def mesh_envelope_tolerance(dimensions):
    """Allow small tessellation overshoot while retaining STEP measurements."""
    return max(.1, min(.5, .01 * min(dimensions)))


def analyze_family(feature, mesh_path, features_path=None, geometry_path=None):
    mesh = trimesh.load_mesh(mesh_path, process=False)
    dimensions = np.asarray(feature["dimensions"], dtype=float)
    if np.max(abs(mesh.extents-dimensions)) > mesh_envelope_tolerance(dimensions):
        raise ValueError(f"{feature['family']}: mesh and STEP feature frames differ")
    triangles = read_stl(mesh_path)
    circles = grouped_circles(feature["circles"])
    tapered = tapered_bore_groups(feature, geometry_path, mesh)
    rotational = rotational_axial_features(feature, geometry_path, mesh)
    coaxial_steps = (None if rotational else coaxial_step_heights(feature,mesh))
    radial_steps = radial_stepped_bores(feature,mesh)
    stepped_bores = stepped_bore_axial_chains(feature,mesh,circles)
    shaft_stations = longitudinal_shaft_stations(feature,mesh,circles)
    interior_span=broad_interior_wall_span(mesh,dimensions)
    top_rims=top_circular_rims(mesh,dimensions,circles)
    multisurface = multi_surface_bend_plan(feature, mesh, circles, geometry_path)
    orthogonal_bores = orthogonal_bore_plan(circles, mesh, dimensions)
    vertical_axial_bores = vertical_axial_bore_depths(circles, mesh, dimensions)
    profiles = planar_profiles(mesh, triangles)
    # Prefer a face whose holes can be tied to analytic STEP circles.  This
    # avoids selecting a large but nearly featureless underside of a shell.
    # Equal hole coverage then favors the larger face, e.g. a folded base.
    candidate_plans = [(profile, profile_dimensions(profile, dimensions, circles))
                       for profile in profiles]
    if candidate_plans:
        profile, plan = max(candidate_plans,
                            key=lambda item: (item[1]["matched_inner_loops"],
                                              item[1]["matched_inner_loops"]-
                                              item[1]["inner_loops"],
                                              item[0]["area"]))
    else:
        profile, plan = None, None
    projected_stations=(projected_planar_stations(mesh,dimensions)
                        if plan is None else None)
    stepped_side=stepped_side_plan(feature,geometry_path,plan)
    certificate = (certify_lid_prism(profiles, plan, mesh, triangles)
                   if plan else None)
    if certificate is None and features_path is not None:
        try:
            plate = extract_geometry(mesh_path, features_path,
                                     feature["family"])
        except ValueError:
            plate = None
        if plate is not None:
            certificate = dict(method="notched-plate-volume-and-rings",
                               actual_volume=plate["mesh_volume"],
                               reconstructed_volume=plate["expected_volume"],
                               relative_error=plate["relative_error"])
    rotational_top=(len(circles)>=2 and
                    abs(dimensions[0]-dimensions[1])<.02*max(dimensions[:2]) and
                    all(abs(c["axis"][2])>.995 for c in circles) and
                    np.max(np.ptp([c["center"][:2] for c in circles],axis=0))<.08)
    coverage=circular_coverage(circles,plan,multisurface,rotational_top)
    if certificate and coverage["secondary"]:
        certificate=None
    issues = []
    if not plan:
        issues.append("No polygonal primary planar face; needs axial/freeform analysis")
    else:
        if plan["inner_loops"] != plan["matched_inner_loops"]:
            issues.append("One or more inner loops are not explained by analytic circles")
        if not certificate:
            issues.append("Three-dimensional volume/topology not reconstructed")
    if coverage["secondary"]:
        issues.append(f"{coverage['secondary']} cylindrical groups outside primary projection")
    step_first=needs_step_first_page(dimensions,circles,plan,certificate)
    result = dict(family=feature["family"], dimensions=dimensions.tolist(),
                  circle_count=len(circles),
                  circular_coverage=coverage,
                  tapered_bores=tapered,
                  rotational_axial=rotational,
                  coaxial_steps=coaxial_steps,
                  radial_stepped_bores=radial_steps,
                  stepped_bore_axial_chains=stepped_bores,
                  longitudinal_shaft_stations=shaft_stations,
                  broad_interior_wall_span=interior_span,
                  top_circular_rims=top_rims,
                  multi_surface_bend=multisurface,
                  orthogonal_bores=orthogonal_bores,
                  vertical_axial_bores=vertical_axial_bores,
                  stepped_side=stepped_side,
                  profile=plan, projected_stations=projected_stations,
                  certificate=certificate,
                  step_first=step_first,
                  status="geometry_certified" if certificate else "step_required",
                  unresolved=issues)
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", required=True, type=Path)
    parser.add_argument("--meshes", required=True, type=Path)
    parser.add_argument("--geometry", type=Path)
    parser.add_argument("--families", nargs="+")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    features = json.loads(args.features.read_text())
    if args.families:
        selected = set(args.families)
        features = [f for f in features if f["family"] in selected]
        if len(features) != len(selected):
            raise ValueError("Unknown family in --families")
    results = [analyze_family(feature, args.meshes/(feature["family"]+".stl"),
                              args.features,args.geometry)
               for feature in features]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(json.dumps(Counter(r["status"] for r in results), ensure_ascii=False))


if __name__ == "__main__":
    main()
