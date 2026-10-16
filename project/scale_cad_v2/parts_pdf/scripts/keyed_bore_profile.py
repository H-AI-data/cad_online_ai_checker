#!/usr/bin/env python3
"""Measure a straight-sided key slot interrupting a circular axial bore."""

from __future__ import annotations

import math

import numpy as np
from shapely.geometry import LineString


def _top_loops(mesh, height):
    z = float(height) - min(.01, .002*float(height))
    section = mesh.section(plane_origin=(0, 0, z), plane_normal=(0, 0, 1))
    return [] if section is None else [np.asarray(loop)[:, :2]
                                           for loop in section.discrete]


def detect_keyed_bore(mesh, circles, dimensions):
    """Find a mostly circular inner loop with one verified straight slot.

    The bore center/radius comes from analytic STEP circles.  The slot outline
    comes from a near-top mesh section, so the drawing does not erase material
    that lies outside the nominal bore circle.
    """
    height = float(dimensions[2])
    loops = _top_loops(mesh, height)
    axial = [circle for circle in circles
             if abs(float(circle["axis"][2])) > .995 and
             3 <= float(circle["diameter"]) < .5*min(dimensions[:2])]
    for circle in sorted(axial, key=lambda item:item["diameter"]):
        center = np.asarray(circle["center"][:2], float)
        radius = float(circle["diameter"])/2
        for loop in loops:
            distances = np.linalg.norm(loop-center, axis=1)
            near = np.abs(distances-radius) < max(.04, .008*radius)
            if (np.mean(near) < .75 or
                    np.max(distances)-radius < max(.3, .05*radius) or
                    np.min(distances) < radius-max(.05, .01*radius)):
                continue
            simplified = np.asarray(LineString(loop).simplify(max(.02,.004*radius)).coords)
            if len(simplified) < 8:
                continue
            candidates = []
            for index in range(len(simplified)-1):
                a,b = simplified[index:index+2]
                if (min(np.linalg.norm(a-center),np.linalg.norm(b-center)) <
                        radius+max(.15,.025*radius)):
                    continue
                width = float(np.linalg.norm(b-a))
                if not .15*radius < width < 1.5*radius:
                    continue
                before = simplified[index-1]
                after = simplified[index+2] if index+2 < len(simplified) else simplified[1]
                if (abs(np.linalg.norm(before-center)-radius) > .1 or
                        abs(np.linalg.norm(after-center)-radius) > .1):
                    continue
                normal = np.array([-(b-a)[1],(b-a)[0]])/width
                middle = (a+b)/2
                if np.dot(middle-center,normal)<0:
                    normal=-normal
                distance = float(np.dot(middle-center,normal))
                if distance <= radius+.2:
                    continue
                side_a = a-before
                side_b = b-after
                if (min(np.linalg.norm(side_a),np.linalg.norm(side_b)) < .2 or
                    min(abs(np.dot(side_a,normal))/np.linalg.norm(side_a),
                        abs(np.dot(side_b,normal))/np.linalg.norm(side_b)) < .95):
                    continue
                candidates.append((width,distance,a,b,normal))
            if len(candidates) != 1:
                continue
            width,distance,a,b,normal = candidates[0]
            angle = math.degrees(math.atan2(normal[1],normal[0])) % 360
            return dict(center=center.tolist(), bore_diameter=2*radius,
                        outline=loop.tolist(), floor=[a.tolist(),b.tolist()],
                        width=width, center_to_floor=distance,
                        angle_deg=angle, direction=normal.tolist())
    return None
