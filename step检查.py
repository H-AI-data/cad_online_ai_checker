#!/usr/bin/env python3
"""只读检查 STEP 中的每个实体，输出 JSON；不修复或重新导出源文件。

用法：python3 ~/Desktop/step检查.py /完整路径/model.step --output /完整路径/report.json
省略 --output 时，在源文件旁生成 model.step.检查.json。
macOS 自动使用 /Applications/FreeCAD.app 内的 Python，也可传 --freecad-python。
零件以 FreeCAD 导入后的 Solid 为单位，重复装配实例分别记录。
"""

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import sys


def load_freecad(args):
    app_resources = Path('/Applications/FreeCAD.app/Contents/Resources')
    requested = args.freecad_python or os.environ.get('FREECAD_PYTHON')
    library = args.freecad_lib or os.environ.get('FREECAD_LIB')
    if not requested and importlib.util.find_spec('FreeCAD') is None:
        candidate = app_resources / 'bin/python'
        if candidate.is_file():
            requested = str(candidate)
            library = library or str(app_resources / 'lib')
    if requested:
        resolved = shutil.which(requested)
        if resolved is None:
            raise RuntimeError('找不到 FreeCAD Python：' + requested)
        if Path(resolved).resolve() != Path(sys.executable).resolve():
            if os.environ.get('_STEP_CHECK_REEXEC'):
                raise RuntimeError('FreeCAD Python 启动失败，请检查运行时路径')
            env = os.environ.copy()
            env['_STEP_CHECK_REEXEC'] = '1'
            if library:
                env['FREECAD_LIB'] = str(library)
            os.execve(resolved, [resolved, str(Path(__file__).resolve()), *sys.argv[1:]], env)
    if library:
        sys.path.insert(0, str(library))
    elif app_resources.is_dir() and Path(sys.executable).resolve().is_relative_to(app_resources):
        sys.path.insert(0, str(app_resources / 'lib'))
    try:
        import FreeCAD  # 必须先于 Part 导入。
        import Part
    except ImportError as exc:
        raise RuntimeError('需要能导入 FreeCAD、Part 的 Python；可传 --freecad-python 和 --freecad-lib') from exc
    return FreeCAD, Part


def finite_number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('非有限数值')
    return value


def vector(value):
    return [finite_number(value.x), finite_number(value.y), finite_number(value.z)]


def inspect_solid(solid, index):
    errors = []

    def read(name, getter):
        try:
            return getter()
        except Exception as exc:
            errors.append({'field': name, 'error': '{}: {}'.format(type(exc).__name__, exc)})
            return None

    def bounding_box():
        box = solid.BoundBox
        return {
            'min': [finite_number(box.XMin), finite_number(box.YMin), finite_number(box.ZMin)],
            'max': [finite_number(box.XMax), finite_number(box.YMax), finite_number(box.ZMax)],
            'size': [finite_number(box.XLength), finite_number(box.YLength), finite_number(box.ZLength)],
        }

    valid = read('is_valid', lambda: bool(solid.isValid()))
    closed = read('closed', lambda: bool(solid.isClosed()))
    volume = read('volume_mm3', lambda: finite_number(solid.Volume))
    positive = volume > 0 if volume is not None else None
    if volume == 0:
        center = None
        errors.append({'field': 'center_of_mass_mm', 'error': '零体积实体的体积质心未定义'})
    else:
        center = read('center_of_mass_mm', lambda: vector(solid.CenterOfMass))
    return {
        'solid_index': index,
        'id': 'solid_{:04d}'.format(index),
        'is_valid': valid,
        'closed': closed,
        'positive_volume': positive,
        'volume_mm3': volume,
        'surface_area_mm2': read('surface_area_mm2', lambda: finite_number(solid.Area)),
        'bbox_mm': read('bbox_mm', bounding_box),
        'center_of_mass_mm': center,
        'face_count': len(solid.Faces),
        'shell_count': len(solid.Shells),
        'errors': errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('step', type=Path, help='待检查的 .step / .stp 文件')
    parser.add_argument('--output', '-o', type=Path, help='JSON 报告路径')
    parser.add_argument('--freecad-python', help='FreeCAD Python 可执行文件')
    parser.add_argument('--freecad-lib', help='FreeCAD / Part 模块目录')
    args = parser.parse_args()
    source = args.step.expanduser().resolve()
    if not source.is_file():
        parser.error('输入文件不存在：' + str(source))
    output = (args.output.expanduser().resolve() if args.output else source.with_name(source.name + '.检查.json'))
    if output == source:
        parser.error('报告路径不能与输入 STEP 相同')
    App, Part = load_freecad(args)
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    shape = Part.read(str(source))
    solids = list(shape.Solids)
    parts = [inspect_solid(solid, i) for i, solid in enumerate(solids, 1)]
    checks = {
        'solid_count': len(parts),
        'invalid_solid_count': sum(p['is_valid'] is False for p in parts),
        'not_closed_count': sum(p['closed'] is False for p in parts),
        'non_positive_volume_count': sum(p['positive_volume'] is False for p in parts),
        'parts_with_errors_count': sum(bool(p['errors']) for p in parts),
        'all_solids_pass': bool(parts) and all(p['is_valid'] is True and p['closed'] is True
                                              and p['positive_volume'] is True and not p['errors'] for p in parts),
    }
    report = {
        'schema_version': 1,
        'source': str(source),
        'source_sha256': digest.hexdigest(),
        'freecad_version': '.'.join(App.Version()[:3]),
        'units': {'length': 'mm', 'volume': 'mm^3', 'surface_area': 'mm^2'},
        'part_definition': 'FreeCAD 导入后的每个 Solid；重复实例分别记录，非实体曲面或线不作为零件',
        'coordinate_frame': 'FreeCAD 导入后的 STEP 原始坐标系；未平移或归一化',
        'bbox_method': 'BRep BoundBox，轴对齐包围盒，非网格包围盒',
        'source_modified': False,
        'shape_type': shape.ShapeType,
        'summary': checks,
        'parts': parts,
    }
    if not parts:
        report['errors'] = ['未找到实体 Solid，文件可能为空或只包含曲面、线']
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(output), **checks}, ensure_ascii=False))
    return 0 if checks['all_solids_pass'] else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('检查失败：{}: {}'.format(type(exc).__name__, exc), file=sys.stderr)
        sys.exit(2)
