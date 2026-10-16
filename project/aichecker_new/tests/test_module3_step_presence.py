"""模块3维度1只检查总装STEP/STP文件是否提供。"""
from contextlib import redirect_stderr,redirect_stdout
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock,patch

from common.context import prepare
from common.io import result
from common.pipeline import main
from modules.module_03.dimension_01_correspondence.checker import assess


PROJECT=Path(__file__).resolve().parents[1]


class Module3StepPresenceCheckerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()

    def test_case_insensitive_step_without_drawing_passes(self):
        step=self.root/'assembly.StEp';step.write_text('assembly')
        report=assess({'steps':[str(step)],'correspondence':result('failed','旧图纸规则不得生效')})
        self.assertEqual(report['status'],'passed')
        self.assertEqual(report['reason'],'通过')
        self.assertEqual(report['evidence']['step_files'],[str(step)])
        self.assertIn('不要求工程图',report['evidence']['scope'])
        self.assertIn('不调用AI',report['evidence']['scope'])

    def test_empty_missing_wrong_extension_directory_and_symlink_fail(self):
        valid=self.root/'valid.step';valid.write_text('valid')
        wrong=self.root/'assembly.pdf';wrong.write_text('drawing')
        directory=self.root/'directory.stp';directory.mkdir()
        linked=self.root/'linked.step';linked.symlink_to(valid)
        cases={
            'empty':[],
            'missing':[str(self.root/'missing.step')],
            'wrong extension':[str(wrong)],
            'directory':[str(directory)],
            'symlink':[str(linked)],
        }
        for name,steps in cases.items():
            with self.subTest(name=name):
                report=assess({'steps':steps,'correspondence':result('passed','旧图纸规则不得生效')})
                self.assertEqual(report['status'],'failed',report)
                self.assertNotEqual(report['reason'],'通过')
                self.assertEqual(report['evidence']['step_files'],steps)


class Module3StepPresenceContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.step=self.root/'assembly.step';self.step.write_text('assembly')
        self.model=dict(kind='assembly',complete=True,
                        families=[dict(id=str(i)) for i in range(100)],images=[])

    def test_prepare_never_pairs_renders_or_uses_judge_with_optional_drawings(self):
        for drawings in ([],[self.root/'bogus.pdf'],
                         [self.root/'bogus.pdf',self.root/'extra.dxf']):
            with self.subTest(drawings=drawings):
                judge=Mock(assess=Mock(return_value=result('passed','通过')))
                inventory=dict(steps=[self.step],drawings=drawings)
                with patch('common.context.render_drawing') as render, \
                     patch('common.context.pair_documents') as pairing:
                    ctx=prepare('3',['1'],inventory,{str(self.step):self.model},judge,
                                self.root/'output',20)
                self.assertEqual(ctx.get('steps'),[str(self.step)])
                self.assertNotIn('correspondence',ctx)
                self.assertEqual(assess(ctx)['status'],'passed')
                render.assert_not_called();pairing.assert_not_called();judge.assess.assert_not_called()


class Module3StepPresencePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.sequence=0
        self.source=self.root/'input';self.source.mkdir()
        self.step=self.source/'assembly.step';self.step.write_text('unique module3 assembly fixture')
        self.registry=self.root/'registry.json'
        shutil.copyfile(PROJECT.parent.parent/'step查重.json',self.registry)
        self.registry_before=self.registry.read_bytes()

    def run_module3(self,total=100,preflight_status='passed',dimension='all'):
        self.sequence+=1;output=self.root/f'output_{self.sequence}'
        geometry=dict(kind='assembly',complete=True,
                      families=[dict(id=str(i)) for i in range(total)],images=[],warnings=[])
        judge=Mock(calls=0,cache_hits=0,
                   assess=Mock(side_effect=AssertionError('模块3不得调用AI')))
        preflight=dict(status=preflight_status,
                       reason='通过' if preflight_status=='passed' else '前置失败',files=[])
        command=['--step',str(self.step),'--module','3','--dimension',dimension,
                 '--output-dir',str(output),'--dedup-registry',str(self.registry),'--llm','off']
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()), \
             patch('common.pipeline.run_preflight',return_value=preflight), \
             patch('common.pipeline.analyze_step',return_value=geometry) as analyze, \
             patch('common.pipeline.Judge',return_value=judge):
            code=main(command)
        summary=json.loads((output/'summary.json').read_text())
        return code,summary,output,judge,analyze

    def test_exactly_100_with_step_only_registers_then_duplicate_rejects_without_ai(self):
        code,summary,output,judge,_=self.run_module3()
        self.assertEqual(code,0,summary)
        self.assertEqual(summary['acceptance_status'],'accepted')
        self.assertEqual([row['status'] for row in summary['results']],['passed','passed'])
        self.assertEqual(summary['results'][0]['title'],'总装STEP/STP文件提供性检查')
        self.assertEqual(summary['llm']['calls'],0);judge.assess.assert_not_called()
        report=(output/'module_03/dimension_01_correspondence/report.md').read_text()
        self.assertIn('# 总装STEP/STP文件提供性检查',report)
        self.assertIn(str(self.step),report)
        self.assertIn('不要求工程图',report);self.assertIn('不调用AI',report)
        committed=self.registry.read_bytes();self.assertNotEqual(committed,self.registry_before)

        code,summary,_,judge,_=self.run_module3()
        self.assertEqual(code,1,summary)
        self.assertEqual(summary['acceptance_status'],'rejected')
        self.assertEqual(summary['finalization']['evidence']['action'],'duplicate')
        self.assertEqual(self.registry.read_bytes(),committed)
        judge.assess.assert_not_called()

    def test_below_100_does_not_register(self):
        code,summary,_,judge,_=self.run_module3(total=99)
        self.assertEqual(code,1,summary)
        self.assertEqual([row['status'] for row in summary['results']],['passed','failed'])
        self.assertEqual(summary['finalization']['status'],'skipped')
        self.assertEqual(self.registry.read_bytes(),self.registry_before)
        judge.assess.assert_not_called()

    def test_failed_preflight_prevents_geometry_context_and_ai(self):
        self.sequence+=1;output=self.root/f'output_{self.sequence}'
        command=['--step',str(self.step),'--module','3','--output-dir',str(output),
                 '--dedup-registry',str(self.registry),'--llm','off']
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()), \
             patch('common.pipeline.run_preflight',return_value=dict(status='failed',reason='无效实体',files=[])), \
             patch('common.pipeline.analyze_step',side_effect=AssertionError('不得解析')) as analyze, \
             patch('common.pipeline.Judge',side_effect=AssertionError('不得初始化AI')):
            code=main(command)
        summary=json.loads((output/'summary.json').read_text())
        self.assertEqual(code,1)
        self.assertEqual([row['status'] for row in summary['results']],['skipped','skipped'])
        self.assertEqual(self.registry.read_bytes(),self.registry_before)
        analyze.assert_not_called()


if __name__=='__main__':
    unittest.main()
