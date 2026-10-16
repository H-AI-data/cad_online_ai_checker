import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.input=self.root/'输入';self.input.mkdir()
        (self.input/'a.step').write_text('STEP')
        (self.input/'a.pdf').write_text('PDF')
        self.output=self.root/'输出'

    def test_failed_gate_blocks_geometry_and_llm_all_dimensions(self):
        from common.pipeline import main
        with patch('common.pipeline.run_preflight',return_value=dict(status='failed',reason='坏实体',files=[])), \
             patch('common.pipeline.analyze_step',side_effect=AssertionError('不能执行几何分析')), \
             patch('common.pipeline.Judge',side_effect=AssertionError('不能初始化模型')):
            code=main(argv=['--module','1','--input-dir',str(self.input),'--output-dir',str(self.output)])
        self.assertEqual(code,1)
        report=json.loads((self.output/'summary.json').read_text())
        self.assertEqual([r['status'] for r in report['results']],['skipped']*3)
        self.assertEqual(report['llm']['calls'],0)

    def test_auto_module3_with_100_unique_families(self):
        from common.pipeline import main
        geometry=dict(kind='assembly',complete=True,families=[dict(id=str(i)) for i in range(100)],images=[],warnings=[])
        with patch('common.pipeline.run_preflight',return_value=dict(status='passed',reason='通过',files=[])), \
             patch('common.pipeline.analyze_step',return_value=geometry):
            code=main(argv=['--dimension','2','--input-dir',str(self.input),'--output-dir',str(self.output),'--llm','off'])
        self.assertEqual(code,0)
        report=json.loads((self.output/'summary.json').read_text())
        self.assertEqual(report['module'],'3')
        self.assertEqual(report['results'][0]['status'],'passed')

    def test_explicit_wrong_module_not_silently_used(self):
        from common.pipeline import main
        geometry=dict(kind='part',complete=True,families=[dict(id='1')],images=[],warnings=[])
        with patch('common.pipeline.run_preflight',return_value=dict(status='passed',reason='通过',files=[])), \
             patch('common.pipeline.analyze_step',return_value=geometry):
            code=main(argv=['--module','3','--input-dir',str(self.input),'--output-dir',str(self.output),'--llm','off'])
        self.assertEqual(code,1)
        report=json.loads((self.output/'summary.json').read_text())
        self.assertTrue(all(r['status']=='skipped' for r in report['results']))

    def test_output_overlap_is_rejected(self):
        from common.pipeline import main
        self.assertEqual(main(argv=['--input-dir',str(self.input),'--output-dir',str(self.input/'输出')]),2)
        self.assertFalse((self.input/'输出').exists())

    def test_replacement_gate_script_is_rejected(self):
        from common.pipeline import main
        replacement=self.root/'fake_gate.py';replacement.write_text('raise SystemExit(0)')
        with patch('common.pipeline.run_preflight',side_effect=AssertionError('不能使用替代脚本')):
            code=main(['--input-dir',str(self.input),'--output-dir',str(self.output),
                       '--step-check-script',str(replacement)])
        report=json.loads((self.output/'summary.json').read_text())
        self.assertEqual(code,2)
        self.assertIn('原始',report['routing']['reason'])
        self.assertEqual(report['llm']['calls'],0)

    def test_added_input_during_run_invalidates_result(self):
        from common.pipeline import main
        def analyze(*args):
            (self.input/'added.step').write_text('未检查的新输入')
            return dict(kind='assembly',complete=True,families=[dict(id=str(i)) for i in range(100)],images=[],warnings=[])
        with patch('common.pipeline.run_preflight',return_value=dict(status='passed',reason='通过',files=[])), \
             patch('common.pipeline.analyze_step',side_effect=analyze):
            code=main(['--input-dir',str(self.input),'--output-dir',str(self.output),'--dimension','2','--llm','off'])
        report=json.loads((self.output/'summary.json').read_text())
        self.assertEqual(code,2)
        self.assertIn('输入',report['routing']['reason'])

    def test_all_eight_fixed_entries_keep_preflight(self):
        from common.pipeline import main,DIMENSIONS
        for module,dimensions in DIMENSIONS.items():
            for dimension in dimensions:
                with self.subTest(module=module,dimension=dimension):
                    output=self.root/f'输出_{module}_{dimension}'
                    with patch('common.pipeline.run_preflight',return_value=dict(status='failed',reason='无效实体',files=[])), \
                         patch('common.pipeline.analyze_step',side_effect=AssertionError('禁止解析')), \
                         patch('common.pipeline.Judge',side_effect=AssertionError('禁止调用AI')):
                        code=main(['--input-dir',str(self.input),'--output-dir',str(output)],
                                  fixed_module=module,fixed_dimension=dimension)
                    report=json.loads((output/'summary.json').read_text())
                    self.assertEqual(code,1)
                    self.assertEqual(len(report['results']),1)
                    self.assertEqual(report['results'][0]['status'],'skipped')
                    self.assertEqual(report['llm']['calls'],0)


if __name__=='__main__':
    unittest.main()
