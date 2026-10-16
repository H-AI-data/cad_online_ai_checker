from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]


class EntryTests(unittest.TestCase):
    def test_all_eight_independent_entries_exist(self):
        from common.pipeline import DIMENSIONS
        for module,dimensions in DIMENSIONS.items():
            for number,(dimension,title) in dimensions.items():
                folder=ROOT/f'modules/module_{int(module):02d}'/dimension
                with self.subTest(module=module,dimension=number):
                    self.assertTrue((folder/'run.sh').is_file())
                    result=subprocess.run(['sh',str(folder/'run.sh'),'--help'],capture_output=True,text=True,timeout=30)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertIn('--input-dir',result.stdout)
                    self.assertIn('--llm {off,auto}',result.stdout)
                    self.assertTrue((folder/'README.md').is_file())


if __name__=='__main__':
    unittest.main()
