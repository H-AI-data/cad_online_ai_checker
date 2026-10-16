"""真实FreeCAD→几何→路由→维度离线集成，不替换前置或几何组件。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import cadquery as cq
from test_geometry import export_part,export_assembly
from common.pipeline import main


@unittest.skipUnless((Path(sys.prefix)/'lib'/'FreeCAD.so').is_file(),'需要已安装的FreeCAD Conda环境')
class RuntimeTests(unittest.TestCase):
    def test_package_and_single_assembly_count_real_geometry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);package=root/'资料';assembly_dir=package/'总装';parts=package/'非标准件'
            assembly_dir.mkdir(parents=True);parts.mkdir()
            box=cq.Workplane('XY').box(3,7,11)
            assembly=cq.Assembly(name='root')
            assembly.add(box,name='one')
            assembly.add(box,name='repeated',loc=cq.Location((20,0,0)))
            assembly.add(cq.Workplane('XY').box(6,7,11),name='different',loc=cq.Location((40,0,0)))
            export_assembly(assembly_dir/'总装.step',assembly)
            export_part(parts/'零件.step',box)
            # 数量维度不读取图纸，测试不生成伪PDF来冒充内容验证。
            code=main(['--input-dir',str(package),'--dimension','2','--llm','off','--output-dir',str(root/'result2')])
            report=json.loads((root/'result2'/'summary.json').read_text())
            self.assertEqual(report['preflight']['status'],'passed')
            self.assertEqual(report['module'],'2')
            self.assertEqual(code,1)
            self.assertEqual(report['results'][0]['status'],'failed')
            evidence=report['results'][0]['evidence']
            self.assertEqual((evidence['total'],evidence['nonstandard'],evidence['standard']),(2,1,1))
            self.assertEqual(report['llm']['calls'],0)
            code=main(['--input-dir',str(assembly_dir),'--dimension','2','--llm','off','--output-dir',str(root/'result3')])
            report=json.loads((root/'result3'/'summary.json').read_text())
            self.assertEqual(report['preflight']['status'],'passed')
            self.assertEqual(report['module'],'3')
            self.assertEqual(code,1)
            self.assertEqual(report['results'][0]['evidence']['total'],2)
            self.assertEqual(report['llm']['calls'],0)
