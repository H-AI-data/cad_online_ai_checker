#!/usr/bin/env python3
"""Render depth-readable orthographic PNG views of aligned STLs in Blender."""

from __future__ import annotations

import argparse
import math
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view
sys.path.insert(0, str(Path(__file__).resolve().parent))
from view_camera_spec import VIEW_DIRECTIONS as CAMERA_DIRECTIONS


VIEW_DIRECTIONS = {name: Vector(direction)
                   for name, direction in CAMERA_DIRECTIONS.items()}
STEP_VIEW_NAMES = ("upper-oblique", "rear-oblique", "top")


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras):
        for block in list(collection):
            if block.users == 0:
                collection.remove(block)


def import_stl(path: Path) -> bpy.types.Object:
    bpy.ops.wm.stl_import(filepath=str(path))
    objects = [obj for obj in bpy.context.selected_objects if obj.type == "MESH"]
    if not objects:
        raise RuntimeError(f"No mesh imported from {path}")
    obj = objects[0]
    obj.name = path.stem
    obj.color = (0.46, 0.66, 0.84, 1.0)
    for polygon in obj.data.polygons:
        polygon.use_smooth = False
    return obj


def world_bounds(obj: bpy.types.Object) -> list[Vector]:
    return [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]


def center_object(obj: bpy.types.Object) -> None:
    bounds = world_bounds(obj)
    low = Vector(tuple(min(point[i] for point in bounds) for i in range(3)))
    high = Vector(tuple(max(point[i] for point in bounds) for i in range(3)))
    obj.location -= (low + high) * 0.5
    bpy.context.view_layer.update()


def configure_scene(output_dir: Path) -> bpy.types.Object:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = 1120
    scene.render.resolution_y = 640
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.render.filepath = str(output_dir)
    scene.render.image_settings.color_depth = "8"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "OBJECT"
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = "WORLD"
    scene.display.shading.curvature_ridge_factor = 1.65
    scene.display.shading.curvature_valley_factor = 1.65
    scene.display.shading.show_specular_highlight = True
    scene.display.shading.show_object_outline = True
    scene.display.shading.object_outline_color = (0.055, 0.075, 0.10)
    scene.display.shading.show_backface_culling = False
    scene.view_settings.look = "AgX - Medium High Contrast"

    camera_data = bpy.data.cameras.new("DepthCamera")
    camera_data.type = "ORTHO"
    camera = bpy.data.objects.new("DepthCamera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    return camera


def fit_camera(
    camera: bpy.types.Object,
    obj: bpy.types.Object,
    direction: Vector,
    margin: float = 1.22,
) -> None:
    direction = direction.normalized()
    bounds = world_bounds(obj)
    center = sum(bounds, Vector()) / len(bounds)
    diameter = max((a - b).length for a in bounds for b in bounds)
    camera.location = center + direction * max(diameter * 2.5, 1.0)
    camera.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
    if abs(direction.z) > 1 - 1e-8:
        # Remove the roll ambiguity at the poles: TOP is +X right, +Y up.
        # The true bottom camera keeps +X right and therefore has -Y up.
        camera.rotation_euler = (0, 0, 0) if direction.z > 0 else (math.pi, 0, 0)
    bpy.context.view_layer.update()
    camera.data.ortho_scale = 1.0
    bpy.context.view_layer.update()
    projected = [world_to_camera_view(bpy.context.scene, camera, p) for p in bounds]
    width = max(p.x for p in projected) - min(p.x for p in projected)
    height = max(p.y for p in projected) - min(p.y for p in projected)
    camera.data.ortho_scale = max(width, height) * margin
    camera.data.clip_start = max(diameter * 0.001, 0.01)
    camera.data.clip_end = max(diameter * 10.0, 10_000.0)


def render_views(obj: bpy.types.Object, camera: bpy.types.Object, output_dir: Path,
                 step_views: bool = False) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"view_version": "upper-opposite-grid-linework-v3", "views": {}}
    view_directions = ({name: VIEW_DIRECTIONS[name] for name in STEP_VIEW_NAMES}
                       if step_views else VIEW_DIRECTIONS)
    for name, direction in view_directions.items():
        fit_camera(camera, obj, direction, margin=1.22)
        bpy.context.scene.render.filepath = str(output_dir / f"{name}.png")
        bpy.ops.render.render(write_still=True)
        manifest["views"][name] = {"image": f"{name}.png"}
    (output_dir / "render_manifest.json").write_text(json.dumps(manifest, indent=2))


def parse_args() -> argparse.Namespace:
    arguments = []
    if "--" in __import__("sys").argv:
        arguments = __import__("sys").argv[__import__("sys").argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--stl", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--batch-ids-json", type=Path,
                        help="Family-ID JSON array to render in one Blender process")
    parser.add_argument("--meshes-dir", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--manifest", type=Path, help="Defaults to manifest.json beside the STL")
    parser.add_argument("--step-views", action="store_true",
                        help="Render upper 3/4, opposite 3/4 and top for a STEP-provided family")
    args = parser.parse_args(arguments)
    if args.batch_ids_json:
        if not (args.meshes_dir and args.output_root and args.manifest):
            parser.error("Batch mode needs --meshes-dir, --output-root and --manifest")
        if args.stl or args.output_dir:
            parser.error("Batch mode cannot also use --stl or --output-dir")
    elif not (args.stl and args.output_dir):
        parser.error("Single-family mode needs --stl and --output-dir")
    return args


def main() -> None:
    args = parse_args()
    manifest_path = args.manifest or args.stl.with_name("manifest.json")
    families = json.loads(manifest_path.read_text())["families"] if manifest_path.is_file() else []
    known = {family["id"] for family in families}
    if args.batch_ids_json:
        ids = json.loads(args.batch_ids_json.read_text())
        if not isinstance(ids, list) or any(not isinstance(fid, str) for fid in ids):
            raise ValueError("Batch family IDs must be a JSON string array")
        if len(ids) != len(set(ids)) or set(ids) - known:
            raise ValueError("Batch family IDs are duplicated or missing from manifest")
        jobs = [(args.meshes_dir / f"{fid}.stl", args.output_root / fid) for fid in ids]
    else:
        if known and args.stl.stem not in known:
            raise ValueError(f"Family missing from manifest: {args.stl.stem}")
        jobs = [(args.stl, args.output_dir)]
    for stl, output_dir in jobs:
        clear_scene()
        obj = import_stl(stl)
        center_object(obj)
        camera = configure_scene(output_dir)
        render_views(obj, camera, output_dir, args.step_views)


if __name__ == "__main__":
    main()
