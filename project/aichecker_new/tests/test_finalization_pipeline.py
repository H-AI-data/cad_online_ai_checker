"""终检集成：隔离昂贵的几何/AI观察，真实执行维度规则、原查重脚本和临时库事务。"""
from contextlib import redirect_stdout,redirect_stderr
import io
import fcntl
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from common.io import result,sha256
from common.pipeline import main

PROJECT=Path(__file__).resolve().parents[1]


class FinalizationPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.registry=self.root/'临时查重.json'
        shutil.copyfile(PROJECT.parent.parent/'step查重.json',self.registry)
        self.before=self.registry.read_bytes();self.sequence=0

    def fixture(self,module):
        folder=self.root/f'输入{module}';folder.mkdir()
        if module=='2':
            assembly=folder/'总装';parts=folder/'非标准件';assembly.mkdir();parts.mkdir()
            step=assembly/'assembly.step';step.write_text('module2 assembly fixture')
            (parts/'part.step').write_text('module2 nonstandard fixture')
        else:
            step=folder/'part.step';step.write_text(f'module{module} fixture')
            if module=='1':
                (folder/'source.sldprt').write_text('SolidWorks provision fixture')
        return folder,step

    def run_pipeline(self,module,folder,dimension='all',status='passed',mutate=False):
        self.sequence+=1;output=self.root/f'结果{self.sequence}'
        ctx=dict(solidworks=[str(p) for p in folder.rglob('*.sldprt')],
            steps=[str(p) for p in folder.rglob('*.step')],
            correspondence=result(status,'测试观察'),complexity=result('passed','通过'),
            drawing_quality=result('passed','通过'),stats=dict(complete=True,total=100,nonstandard=30,standard=70),
            membership=dict(complete=True,foreign=[],ambiguous=[]),membership_visual=[result('passed','通过')])
        def geometry(path,*args):
            kind='part' if module=='1' or path.parent.name=='非标准件' else 'assembly'
            return dict(kind=kind,complete=True,families=[{'id':'1'}],images=[])
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()), \
             patch('common.pipeline.run_preflight',return_value=dict(status='passed',reason='通过',files=[])), \
             patch('common.pipeline.analyze_step',side_effect=geometry), \
             patch('common.context.prepare',return_value=ctx):
            command=['--input-dir',str(folder),'--module',module,'--dimension',dimension,'--llm','off',
                     '--output-dir',str(output),'--dedup-registry',str(self.registry)]
            if mutate:
                import modules.module_01.dimension_03_drawing_quality.checker as check
                def changed(context):
                    next(folder.rglob('*.step')).write_text('changed after assessment')
                    return result('passed','通过')
                with patch.object(check,'assess',side_effect=changed):
                    code=main(command)
            else:
                code=main(command)
        return code,json.loads((output/'summary.json').read_text()),output

    def test_each_module_full_pass_registers_then_duplicate_rejects(self):
        for module in ('1','2','3'):
            with self.subTest(module=module):
                folder,target=self.fixture(module)
                code,summary,output=self.run_pipeline(module,folder)
                self.assertEqual(code,0,summary)
                self.assertEqual(summary['acceptance_status'],'accepted')
                self.assertEqual(summary['finalization']['evidence']['sha256'],sha256(target))
                self.assertEqual(summary['finalization']['evidence']['path'],str(target.resolve()))
                self.assertTrue(all(row['status']=='passed' for row in summary['results']))
                committed=self.registry.read_bytes()
                code,summary,output=self.run_pipeline(module,folder)
                self.assertEqual(code,1)
                self.assertEqual(summary['acceptance_status'],'rejected')
                self.assertEqual(summary['finalization']['evidence']['action'],'duplicate')
                self.assertIn('拒绝',(output/'finalization/report.md').read_text())
                self.assertEqual(self.registry.read_bytes(),committed)
        new=json.loads(self.registry.read_text())['files']
        old=json.loads(self.before)['files']
        self.assertEqual(new[:len(old)],old)
        self.assertEqual(len(new),len(old)+3)

    def test_each_modules_partial_pass_never_registers(self):
        for module in ('1','2','3'):
            folder,_=self.fixture(module)
            code,summary,_=self.run_pipeline(module,folder,dimension='1')
            self.assertEqual(code,0)
            self.assertEqual(summary['acceptance_status'],'not_submitted')
            self.assertEqual(summary['finalization']['status'],'skipped')
            self.assertEqual(self.registry.read_bytes(),self.before)
        self.assertFalse(Path(str(self.registry)+'.lock').exists())

    def test_not_passed_or_disabled_ai_evidence_does_not_register(self):
        folder,_=self.fixture('1')
        for status in ('failed','unknown','error','skipped'):
            code,summary,_=self.run_pipeline('1',folder,status=status)
            self.assertNotEqual(code,0)
            self.assertEqual(summary['finalization']['status'],'skipped')
            self.assertEqual(self.registry.read_bytes(),self.before)

    def test_input_mutated_in_dimension_rule_invalidates_registration(self):
        folder,_=self.fixture('1')
        code,summary,_=self.run_pipeline('1',folder,mutate=True)
        self.assertEqual(code,2)
        self.assertEqual(summary['finalization']['status'],'skipped')
        self.assertEqual(self.registry.read_bytes(),self.before)

    def test_bad_database_full_pass_is_error_and_never_replaced(self):
        folder,_=self.fixture('3');self.registry.write_text('{损坏')
        code,summary,_=self.run_pipeline('3',folder)
        self.assertEqual(code,2)
        self.assertEqual(summary['finalization']['status'],'error')
        self.assertEqual(self.registry.read_text(),'{损坏')

    def assert_changed_while_waiting_rejected(self,module,change):
        folder,target=self.fixture(module)
        drawing=target.with_suffix('.pdf');drawing.write_text('checked drawing')
        waiting=threading.Event();failures=[]
        flock=fcntl.flock
        with Path(str(self.registry)+'.lock').open('wb') as held:
            flock(held.fileno(),fcntl.LOCK_EX)
            def edit_after_wait():
                try:
                    if not waiting.wait(10):
                        raise AssertionError('事务未进入真实锁等待')
                    change(folder,drawing)
                except BaseException as exc:
                    failures.append(exc)
                finally:
                    flock(held.fileno(),fcntl.LOCK_UN)
            def observed_flock(fd,operation):
                try:
                    return flock(fd,operation)
                except BlockingIOError:
                    waiting.set()
                    raise
            editor=threading.Thread(target=edit_after_wait)
            editor.start()
            try:
                with patch('fcntl.flock',side_effect=observed_flock):
                    code,summary,_=self.run_pipeline(module,folder)
            finally:
                waiting.set();editor.join(timeout=10)
            self.assertFalse(editor.is_alive())
        self.assertFalse(failures,failures)
        self.assertEqual(code,2,summary)
        self.assertEqual(summary['acceptance_status'],'error')
        self.assertEqual(summary['finalization']['status'],'error')
        self.assertEqual(self.registry.read_bytes(),self.before)

    def test_drawing_change_while_waiting_for_registry_lock_rejects(self):
        self.assert_changed_while_waiting_rejected('1',lambda folder,drawing: drawing.write_text('changed drawing'))

    def test_solidworks_change_while_waiting_for_registry_lock_rejects(self):
        self.assert_changed_while_waiting_rejected('1',lambda folder,drawing:
            (folder/'source.sldprt').write_text('changed SolidWorks'))

    def test_solidworks_removed_while_waiting_for_registry_lock_rejects(self):
        self.assert_changed_while_waiting_rejected('1',lambda folder,drawing:
            (folder/'source.sldprt').unlink())

    def test_solidworks_added_while_waiting_for_registry_lock_rejects(self):
        self.assert_changed_while_waiting_rejected('1',lambda folder,drawing:
            (folder/'extra.SLDJOIN').write_text('new SolidWorks'))

    def test_nonstandard_change_while_waiting_for_registry_lock_rejects(self):
        self.assert_changed_while_waiting_rejected('2',lambda folder,drawing:
            (folder/'非标准件/part.step').write_text('changed nonstandard'))

    def test_new_file_while_waiting_for_registry_lock_rejects(self):
        self.assert_changed_while_waiting_rejected('3',lambda folder,drawing:
            (folder/'新增图纸.pdf').write_text('new drawing'))


if __name__=='__main__':
    unittest.main()
