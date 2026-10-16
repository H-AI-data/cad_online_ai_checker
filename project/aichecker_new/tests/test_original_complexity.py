import importlib
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from common.io import save_json,sha256

MODULE='modules.module_01.dimension_02_complexity.engine'


class OriginalComplexityTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec(MODULE),'缺少原始复杂特征适配器')
        self.engine=importlib.import_module(MODULE)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.step=self.root/'part.step'
        self.step.write_text('source fixture')
        save_json(self.root/'parts/part-spec.json',{'families':[{'id':'family_001'}]})
        self.record={'case':'cad_current','source_step_sha256':sha256(self.step),
            'part_spec_sha256':sha256(self.root/'parts/part-spec.json'),'families':['family_001']}
        self.rows=[{'case':'cad_current','family':'family_001','reasons':['dense_holes'],
            'reason':'紧凑多孔槽件','screen_level':'strong'}]
        self.completion={'complete':True,'source_unchanged':True,'coverage_gaps':[],
            'reason_labels':{'dense_holes':'紧凑多孔槽件','shallow_recess':'浅表细节（扩展）'},
            'engine':'cad-complexity-screen'}

    def evaluate(self):
        save_json(self.root/'scans/complexity_auto_candidates.json',self.rows)
        save_json(self.root/'scans/complexity_step_selection.json',{
            'schema':'cad-complexity-screen/step-selection-v1','cases':[self.record]})
        save_json(self.root/'completion.json',self.completion)
        return self.engine.read_result(self.step,self.root)

    def test_original_candidate_passes_without_confidence(self):
        report=self.evaluate()
        self.assertEqual(report['status'],'passed')
        self.assertEqual(report['reason'],'通过')
        self.assertEqual(report['evidence']['命中特征'],['紧凑多孔槽件'])

    def test_complete_empty_original_selection_fails(self):
        self.rows=[];self.record['families']=[]
        report=self.evaluate()
        self.assertEqual(report['status'],'failed')
        self.assertIn('未命中',report['reason'])
        self.assertNotIn('置信度',report['reason'])

    def test_expanded_candidate_is_retained_and_labeled(self):
        self.rows[0].update(reasons=['shallow_recess'],reason='浅表细节（扩展）',screen_level='expanded')
        report=self.evaluate()
        self.assertEqual(report['status'],'passed')
        self.assertEqual(report['evidence']['扩展规则候选数'],1)

    def test_source_and_spec_mismatch_are_errors(self):
        for key in ('source_step_sha256','part_spec_sha256'):
            old=self.record[key];self.record[key]='bad'
            self.assertEqual(self.evaluate()['status'],'error')
            self.record[key]=old

    def test_incomplete_or_skipped_scan_cannot_be_negative_or_positive(self):
        for key,value in (('complete',False),('source_unchanged',False),('coverage_gaps',['family_001: skipped'])):
            old=self.completion[key];self.completion[key]=value
            self.assertIn(self.evaluate()['status'],('error','unknown'))
            self.completion[key]=old

    def test_inconsistent_selection_or_invented_rule_is_rejected(self):
        self.record['families']=[]
        self.assertEqual(self.evaluate()['status'],'error')
        self.record['families']=['family_001']
        self.rows[0]['reasons']=['multi_axis_holes']
        self.assertEqual(self.evaluate()['status'],'error')

    def test_other_case_or_unknown_family_is_rejected(self):
        for key,value in (('case','other_case'),('family','family_999')):
            old=self.rows[0][key];self.rows[0][key]=value
            self.assertEqual(self.evaluate()['status'],'error')
            self.rows[0][key]=old

    def test_missing_output_is_error(self):
        self.assertEqual(self.engine.read_result(self.step,self.root)['status'],'error')

    def test_original_null_sections_are_coverage_gaps_not_negative_evidence(self):
        worker=importlib.import_module(MODULE.replace('engine','worker'))
        check=getattr(worker,'profile_coverage_gaps',None)
        self.assertTrue(callable(check),'需识别原脚本真实输出的空截面，而非不存在的error字段')
        for sections in ([None,None,None],[[4,0,100,0,1],None,[4,0,100,0,1]]):
            rows=[dict(case='cad_current',family='family_001',sections=sections,profile_candidate=False)]
            gaps=check(rows,['family_001'])
            self.assertTrue(gaps)
            self.completion['coverage_gaps']=gaps
            self.assertEqual(self.evaluate()['status'],'unknown')
        rows=[dict(case='cad_current',family='family_001',sections=[[4,0,100,0,1]]*3)]
        self.assertEqual(check(rows,['family_001']),[])
        self.assertTrue(check([],['family_001']))

    def test_missing_original_source_does_not_fall_back_to_llm(self):
        report=self.engine.screen_complexity(self.step,self.root/'new',source_root=self.root/'missing')
        self.assertEqual(report['status'],'error')
        self.assertIn('原始',report['reason'])

    def test_user_cancel_kills_children_and_is_not_swallowed(self):
        source=self.root/'original'
        entry=source/'cad-complexity-screen/scripts/run.py'
        entry.parent.mkdir(parents=True);entry.write_text('# runtime fixture')
        with tempfile.TemporaryDirectory() as dest, \
             patch.object(self.engine.subprocess,'Popen') as start, \
             patch.object(self.engine.os,'killpg') as kill:
            process=start.return_value;process.pid=12345
            process.wait.side_effect=[KeyboardInterrupt(),0]
            with self.assertRaises(KeyboardInterrupt):
                self.engine.screen_complexity(self.step,Path(dest)/'run',source_root=source,blender=sys.executable)
            kill.assert_called_once()
            self.assertEqual(process.wait.call_count,2)

    def test_timeout_kills_children_and_never_becomes_business_failure(self):
        source=self.root/'original'
        entry=source/'cad-complexity-screen/scripts/run.py'
        entry.parent.mkdir(parents=True);entry.write_text('# runtime fixture')
        with tempfile.TemporaryDirectory() as dest, \
             patch.object(self.engine.subprocess,'Popen') as start, \
             patch.object(self.engine.os,'killpg') as kill:
            process=start.return_value;process.pid=12345
            process.wait.side_effect=[subprocess.TimeoutExpired('worker',1),0]
            report=self.engine.screen_complexity(self.step,Path(dest)/'run',source_root=source,blender=sys.executable,timeout=1)
            self.assertEqual(report['status'],'error')
            self.assertIn('超时',report['reason'])
            kill.assert_called_once()

    def test_report_shows_chinese_original_features(self):
        from common.reporting import write_reports
        report=self.evaluate()
        output=self.root/'reports';output.mkdir()
        write_reports(output,'1',dict(status='passed',files=[]),dict(status='passed',reason='通过'),
            [dict(dimension='dimension_02_complexity',title='零件复杂特征',**report)])
        text=(output/'module_01/dimension_02_complexity/report.md').read_text()
        self.assertIn('紧凑多孔槽件',text)
        self.assertIn('原始脚本',text)
        self.assertIn('高召回',text)

    def test_cli_off_and_auto_never_read_key_for_complexity(self):
        from common.pipeline import main
        from common.vision import Judge
        model=dict(kind='part',complete=True,families=[dict(id='1')],images=[])
        for mode in ('off','auto'):
            with self.subTest(mode=mode), \
                 patch('common.pipeline.run_preflight',return_value=dict(status='passed',reason='通过',files=[])), \
                 patch('common.pipeline.analyze_step',return_value=model), \
                 patch.object(Judge,'assess',side_effect=AssertionError('不能调用AI')), \
                 patch('common.vision.load_config',side_effect=AssertionError('不能读取密钥')), \
                 patch('common.context.screen_complexity',return_value=self.evaluate()):
                output=self.root.parent/(self.root.name+'-'+mode)
                self.addCleanup(__import__('shutil').rmtree,output,True)
                self.assertEqual(main(['--step',str(self.step),'--module','1','--dimension','2',
                                      '--llm',mode,'--output-dir',str(output)]),0)
                summary=json.loads((output/'summary.json').read_text())
                self.assertEqual(summary['llm']['calls'],0)
