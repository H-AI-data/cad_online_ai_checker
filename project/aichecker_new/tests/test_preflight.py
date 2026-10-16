import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.step = self.root/'零件.step'
        self.step.write_text('ISO-10303-21;\nEND-ISO-10303-21;')

    def run_gate(self, code=0, passed=True, digest=True):
        script = self.root/'检查.py'
        script.write_text('import argparse,json,hashlib\nfrom pathlib import Path\n'
            'p=argparse.ArgumentParser();p.add_argument("step");p.add_argument("--output");a=p.parse_args()\n'
            's=Path(a.step);o=Path(a.output);o.parent.mkdir(parents=True,exist_ok=True)\n'
            f'd={{"source":str(s.resolve()),"source_sha256":hashlib.sha256(s.read_bytes()).hexdigest() if {digest!r} else "bad","summary":{{"solid_count":1,"all_solids_pass":{passed!r},"invalid_solid_count":0,"not_closed_count":0,"non_positive_volume_count":0,"parts_with_errors_count":0}}}}\n'
            f'o.write_text(json.dumps(d));raise SystemExit({code})\n')
        from common.preflight import run_preflight
        return run_preflight([self.step],self.root/'output',script,sys.executable,10)

    def test_valid_report_passes_and_source_is_unchanged(self):
        before=hashlib.sha256(self.step.read_bytes()).hexdigest()
        result=self.run_gate()
        self.assertEqual(result['status'],'passed')
        self.assertEqual(before,hashlib.sha256(self.step.read_bytes()).hexdigest())
        self.assertFalse(self.step.with_name(self.step.name+'.检查.json').exists())

    def test_script_failure_is_not_pass(self):
        self.assertEqual(self.run_gate(1,False)['status'],'failed')

    def test_zero_exit_with_false_report_is_not_pass(self):
        self.assertNotEqual(self.run_gate(0,False)['status'],'passed')

    def test_wrong_source_hash_is_error(self):
        self.assertEqual(self.run_gate(digest=False)['status'],'error')

    def test_script_error_stays_error(self):
        self.assertEqual(self.run_gate(2,False)['status'],'error')

    def test_no_step_fails(self):
        from common.preflight import run_preflight
        self.assertEqual(run_preflight([],self.root/'out',self.root/'missing',sys.executable,1)['status'],'failed')

    def test_external_reference_is_blocked_before_script(self):
        self.step.write_text("ISO-10303-21; #1=DOCUMENT_FILE('secret'); END-ISO-10303-21;")
        self.assertEqual(self.run_gate()['status'],'failed')


if __name__=='__main__':
    unittest.main()
