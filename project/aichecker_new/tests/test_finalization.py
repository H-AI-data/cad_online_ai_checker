import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common.io import sha256


class FinalizationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('common.finalization'),'缺少最终入库门禁')
        self.api=importlib.import_module('common.finalization')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.step=self.root/'part.step';self.step.write_text('STEP fixture')
        self.registry=self.root/'registry.json'
        self.expected=['dimension_01','dimension_02','dimension_03']
        self.rows=[dict(dimension=x,status='passed',reason='通过') for x in self.expected]
        self.inventory={'steps':[self.step],'assembly_dir':None}
        self.args=dict(module='1',requested_dimension='all',expected_dimensions=self.expected,
            preflight=dict(status='passed'),routing=dict(status='passed'),rows=self.rows,
            inventory=self.inventory,hashes={str(self.step):sha256(self.step)},
            output=self.root/'output',registry=self.registry)

    def test_partial_or_incomplete_never_invokes_registry(self):
        cases=[dict(requested_dimension='2'),dict(rows=self.rows[:2]),
            dict(rows=[self.rows[0],self.rows[1],self.rows[1]]),dict(preflight=dict(status='failed')),
            dict(routing=dict(status='unknown')),dict(module=None)]
        for status in ('failed','unknown','error','skipped'):
            cases.append(dict(rows=[{**self.rows[0],'status':status},*self.rows[1:]]))
        with patch.object(self.api,'register_step',side_effect=AssertionError('不应访问查重库')):
            for updates in cases:
                with self.subTest(updates=updates):
                    answer=self.api.finalize(**{**self.args,**updates})
                    self.assertEqual(answer['status'],'skipped')
        self.assertFalse(self.registry.exists())

    def test_modules_1_and_3_register_the_single_step(self):
        with patch.object(self.api,'register_step',return_value={'status':'passed','reason':'通过','evidence':{}}) as register:
            for module in ('1','3'):
                result=self.api.finalize(**{**self.args,'module':module})
                self.assertEqual(result['status'],'passed')
                self.assertEqual(register.call_args.args[0],self.step)
                self.assertEqual(register.call_args.kwargs['expected_sha256'],sha256(self.step))

    def test_module2_registers_only_assembly_not_nonstandard(self):
        assembly=self.root/'总装';nonstandard=self.root/'非标准件'
        assembly.mkdir();nonstandard.mkdir()
        target=assembly/'assembly.stp';target.write_text('assembly')
        other=nonstandard/'part.step';other.write_text('nonstandard')
        inventory=dict(steps=[other,target],assembly_dir=str(assembly))
        with patch.object(self.api,'register_step',return_value={'status':'passed','reason':'通过','evidence':{}}) as register:
            answer=self.api.finalize(**{**self.args,'module':'2','inventory':inventory,
                                      'hashes':{str(p):sha256(p) for p in inventory['steps']}})
        self.assertEqual(answer['status'],'passed')
        self.assertEqual(register.call_args.args[0],target)
        register.assert_called_once()

    def test_missing_ambiguous_or_changed_target_does_not_register(self):
        second=self.root/'other.step';second.write_text('other')
        cases=[dict(inventory=dict(steps=[])),dict(inventory=dict(steps=[self.step,second])),dict(hashes={}),
               dict(hashes={str(self.step):'0'*64})]
        with patch.object(self.api,'register_step',side_effect=AssertionError('不应入库')):
            for updates in cases:
                self.assertEqual(self.api.finalize(**{**self.args,**updates})['status'],'error')

    def test_registry_result_is_not_replaced_by_all_dimension_pass(self):
        for status in ('failed','error'):
            with patch.object(self.api,'register_step',return_value=dict(status=status,reason='重复或错误',evidence={})):
                self.assertEqual(self.api.finalize(**self.args)['status'],status)

    def test_report_partial_success_is_not_full_acceptance(self):
        from common.reporting import write_reports
        self.root.joinpath('out').mkdir()
        row=dict(dimension='dimension_01',title='单维度',status='passed',reason='通过',evidence={})
        summary=write_reports(self.root/'out','1',dict(status='passed'),dict(status='passed',reason='通过'),
            [row],finalization=dict(status='skipped',reason='仅单项评测，未入库',evidence={'mode':'partial'}))
        self.assertEqual(summary['exit_code'],0)
        self.assertEqual(summary['acceptance_status'],'not_submitted')
        self.assertIn('未入库',(self.root/'out/summary.md').read_text())

    def test_report_duplicate_or_error_overrides_successful_dimensions(self):
        from common.reporting import write_reports
        for status,code,acceptance in [('passed',0,'accepted'),('failed',1,'rejected'),('error',2,'error')]:
            output=self.root/status;output.mkdir()
            summary=write_reports(output,'1',dict(status='passed'),dict(status='passed',reason='通过'),
                [dict(dimension='dimension_01',title='维度',status='passed',reason='通过',evidence={})],
                finalization=dict(status=status,reason='通过' if status=='passed' else '重复拒绝或错误',
                                  evidence={'action':'registered' if status=='passed' else 'none'}))
            self.assertEqual(summary['exit_code'],code)
            self.assertEqual(summary['acceptance_status'],acceptance)
            self.assertTrue((output/'finalization/report.md').is_file())

    def test_report_incomplete_full_module_does_not_exit_success(self):
        from common.reporting import write_reports
        output=self.root/'incomplete';output.mkdir()
        summary=write_reports(output,'1',dict(status='passed'),dict(status='passed',reason='通过'),
            [dict(dimension='dimension_01',title='现有维度',status='passed',reason='通过',evidence={})],
            finalization=dict(status='skipped',reason='缺少其余维度',evidence={'mode':'incomplete'}))
        self.assertEqual(summary['exit_code'],1)
        self.assertEqual(summary['acceptance_status'],'incomplete')


if __name__=='__main__':
    unittest.main()
