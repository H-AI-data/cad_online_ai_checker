"""SolidWorks只检查提供性；几何、渲染与AI重依赖用边界替身隔离。"""
from contextlib import redirect_stdout,redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from common.context import prepare
from common.inventory import discover
from common.io import result,sha256
from common.pipeline import main,verify_input_snapshot
from modules.module_01.dimension_01_correspondence.checker import assess
from modules.module_03.dimension_01_correspondence.checker import assess as assess_module3_step


class SolidWorksTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.source=self.root/'输入';self.source.mkdir()
        self.step=self.file('part.step');self.drawing=self.file('part.pdf')
        self.model=dict(kind='part',complete=True,families=[{'id':'1'}],images=['model.png'])
        self.sequence=0

    def file(self,name):
        path=self.source/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('arbitrary bytes; not parsed as SolidWorks')
        return path

    def context(self,selection=('1',),module='1',judge=None):
        doc=dict(file=str(self.drawing),complete=True,pages=[1],images=['drawing.png'])
        with patch('common.context.render_drawing',return_value=doc) as render:
            ctx=prepare(module,list(selection),discover(self.source),{str(self.step):self.model},
                        judge or Mock(assess=Mock(return_value=result('passed','通过'))),self.root/'prepared',20)
        return ctx,render

    def run_pipeline(self,extra=(),explicit=False,dimension='1',judge_status='passed',expect_preflight=True):
        self.sequence+=1;output=self.root/f'结果{self.sequence}'
        registry=self.root/'临时查重.json'
        doc=dict(file=str(self.drawing),complete=True,pages=[1],images=['drawing.png'])
        command=['--step',str(self.step),'--drawing',str(self.drawing)] if explicit else ['--input-dir',str(self.source)]
        command+=['--module','1','--dimension',dimension,'--output-dir',str(output),
                  '--dedup-registry',str(registry),'--llm','off',*extra]
        judge=Mock(calls=0,cache_hits=0,assess=Mock(return_value=result(judge_status,'观察结果')))
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()), \
             patch('common.pipeline.run_preflight',return_value=dict(status='passed',reason='通过',files=[])) as preflight, \
             patch('common.pipeline.analyze_step',return_value=self.model), \
             patch('common.pipeline.Judge',return_value=judge), \
             patch('common.context.render_drawing',return_value=doc), \
             patch('common.context.screen_complexity',return_value=result('passed','通过')):
            try:
                code=main(command)
            except SystemExit as exc:
                code=exc.code
        if not expect_preflight:
            preflight.assert_not_called()
        summary=json.loads((output/'summary.json').read_text()) if (output/'summary.json').exists() else {}
        return code,summary,output,judge

    def test_recursive_any_final_sld_extension_is_collected(self):
        expected=[self.file(f'嵌套/{name}') for name in
                  ('other.sldprt','assembly.SLDASM','join.sldjoin','future.sldXYZ','plain.sld')]
        self.file('part.sldprt.bak');self.file('part.sldish.pdf')
        (self.source/'directory.sldprt').mkdir()
        inventory=discover(self.source)
        self.assertEqual(set(inventory.get('solidworks',[])),set(expected))
        self.assertTrue(set(expected).issubset(inventory['files']))
        self.assertEqual(inventory['steps'],[self.step])

    def test_hidden_and_symbolic_solidworks_are_excluded(self):
        visible=self.file('visible.sld')
        self.file('.hidden.sldprt');self.file('.cache/ignored.SLDASM')
        (self.source/'linked.sldprt').symlink_to(visible)
        external=self.root/'external';external.mkdir();(external/'unsubmitted.sld').write_text('external')
        (self.source/'linked_dir').symlink_to(external,target_is_directory=True)
        inventory=discover(self.source)
        self.assertEqual(inventory.get('solidworks'),[visible])
        self.assertTrue(inventory['gaps'])

    def test_package_solidworks_outside_two_roles_does_not_add_model_gap(self):
        self.step.unlink();self.drawing.unlink()
        self.file('总装/a.step');self.file('非标准件/b.step');self.file('other.sldprt')
        inventory=discover(self.source)
        self.assertEqual(inventory['layout'],'package')
        self.assertFalse(inventory['gaps'])
        self.file('extra.pdf')
        self.assertTrue(discover(self.source)['gaps'])

    def test_missing_solidworks_fails_before_rendering_or_pairing(self):
        judge=Mock(assess=Mock(return_value=result('passed','通过')))
        with patch('common.context.pair_documents',return_value=result('passed','通过')) as pairing:
            ctx,render=self.context(judge=judge)
        self.assertEqual(ctx['correspondence']['status'],'failed')
        self.assertIn('SolidWorks',ctx['correspondence']['reason'])
        self.assertEqual(assess(ctx)['status'],'failed')
        render.assert_not_called();pairing.assert_not_called();judge.assess.assert_not_called()

    def test_rule_itself_rejects_missing_solidworks_even_with_passed_pairing(self):
        report=assess(dict(correspondence=result('passed','通过')))
        self.assertEqual(report['status'],'failed')
        self.assertEqual(report['evidence']['solidworks_files'],[])

    def test_provision_does_not_replace_correspondence_or_require_matching_names_and_counts(self):
        paths=[self.file('different.sld'),self.file('nested/extra.SLDJOIN')]
        for status in ('passed','failed','unknown'):
            with self.subTest(status=status):
                judge=Mock(assess=Mock(return_value=result(status,'图纸观察')))
                ctx,render=self.context(judge=judge)
                report=assess(ctx)
                self.assertEqual(report['status'],status)
                self.assertEqual(set(report['evidence'].get('solidworks_files',[])),set(map(str,paths)))
                self.assertIn('仅检查提供',report['evidence'].get('solidworks_scope',''))
                self.assertIn('未核验',report['evidence']['solidworks_scope'])
                self.assertEqual(judge.assess.call_args.args[0],'correspondence')
                render.assert_called_once()

    def test_solidworks_still_requires_equal_step_drawing_counts(self):
        self.file('part.sldprt');self.file('extra.dxf')
        judge=Mock(assess=Mock(side_effect=AssertionError('数量不等不得调用AI')))
        ctx,render=self.context(judge=judge)
        self.assertEqual(assess(ctx)['status'],'failed')
        self.assertIn('数量相等',assess(ctx)['reason'])
        render.assert_not_called()

    def test_module3_context_uses_step_presence_instead_of_correspondence(self):
        ctx,_=self.context(module='3')
        self.assertNotIn('correspondence',ctx)
        self.assertEqual(ctx['steps'],[str(self.step)])
        self.assertEqual(assess_module3_step(ctx)['status'],'passed')

    def test_full_module_without_solidworks_checks_other_dimensions_but_never_registers(self):
        code,summary,output,judge=self.run_pipeline(dimension='all')
        self.assertEqual(code,1,summary)
        self.assertEqual([r['status'] for r in summary['results']],['failed','passed','passed'])
        self.assertEqual(summary['finalization']['status'],'skipped')
        self.assertFalse((self.root/'临时查重.json').exists())
        self.assertEqual([call.args[0] for call in judge.assess.call_args_list],['drawing_quality'])

    def test_explicit_mode_ignores_surrounding_solidworks(self):
        surrounding=self.file('surrounding.sldprt')
        with patch('common.pipeline.discover',side_effect=AssertionError('显式输入不得扫描周边')):
            code,summary,output,judge=self.run_pipeline(explicit=True)
        self.assertEqual(code,1)
        self.assertEqual(summary['results'][0]['status'],'failed')
        self.assertNotIn(str(surrounding),json.loads((output/'context.json').read_text())['source_sha256'])
        judge.assess.assert_not_called()

    def test_repeatable_explicit_solidworks_are_reported_and_hashed(self):
        paths=[self.file('different.SLDASM'),self.file('nested/extra.sld')]
        code,summary,output,_=self.run_pipeline(explicit=True,extra=[item for p in paths for item in ('--solidworks',str(p))])
        self.assertEqual(code,0,summary)
        evidence=summary['results'][0]['evidence']
        self.assertEqual(evidence['solidworks_files'],list(map(str,paths)))
        report=(output/'module_01/dimension_01_correspondence/report.md').read_text()
        for path in paths:
            self.assertIn(str(path),report)
        self.assertIn('仅检查提供',report);self.assertIn('未核验',report)
        hashes=json.loads((output/'context.json').read_text())['source_sha256']
        for path in paths:
            self.assertEqual(hashes[str(path)],sha256(path))

    def test_solidworks_option_requires_step(self):
        path=self.file('part.sld')
        code,summary,output,_=self.run_pipeline(extra=['--solidworks',str(path)])
        self.assertEqual(code,2);self.assertFalse(output.exists())

    def test_invalid_explicit_solidworks_rejected_before_preflight(self):
        valid=self.file('valid.sldprt')
        invalid=[self.file('backup.sldprt.bak'),self.source/'missing.sld']
        directory=self.source/'directory.sldprt';directory.mkdir();invalid.append(directory)
        linked=self.source/'linked.sldprt';linked.symlink_to(valid);invalid.append(linked)
        for path in invalid:
            with self.subTest(path=path):
                code,summary,_,_=self.run_pipeline(explicit=True,extra=['--solidworks',str(path)],expect_preflight=False)
                self.assertEqual(code,2)
                self.assertEqual(summary.get('routing',{}).get('status'),'error')

    def test_directory_snapshot_detects_solidworks_add_remove_modify(self):
        for operation in ('add','remove','modify'):
            with self.subTest(operation=operation):
                path=self.file('part.sldprt')
                inventory=discover(self.source);hashes={str(p):sha256(p) for p in inventory['files']}
                added=self.source/'extra.sld'
                if operation=='add':
                    added.write_text('extra')
                elif operation=='remove':
                    path.unlink()
                else:
                    path.write_text('changed')
                with self.assertRaisesRegex(ValueError,'输入'):
                    verify_input_snapshot(self.source,inventory,hashes)
                if added.exists():
                    added.unlink()

    def test_explicit_solidworks_change_invalidates_snapshot(self):
        path=self.file('part.sld');inventory=discover(self.source)
        hashes={str(p):sha256(p) for p in inventory['files']}
        path.write_text('modified')
        with self.assertRaisesRegex(ValueError,'输入'):
            verify_input_snapshot(self.source,inventory,hashes,explicit=True)


if __name__=='__main__':
    unittest.main()
