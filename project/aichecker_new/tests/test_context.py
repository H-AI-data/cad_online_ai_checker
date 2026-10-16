from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common.context import prepare


class ContextTests(unittest.TestCase):
    def test_complexity_uses_original_scanner_and_never_judge(self):
        class Judge:
            def assess(self,task,images,evidence):
                raise AssertionError('复杂特征不得再调用自写LLM规则')
        step=Path('/input/part.step')
        model=dict(complete=True,families=[],images=['a.png'])
        expected=dict(status='passed',reason='通过',evidence={'engine':'cad-complexity-screen'})
        with tempfile.TemporaryDirectory() as d, patch('common.context.screen_complexity',create=True,return_value=expected) as scan:
            ctx=prepare('1',['2'],dict(steps=[step],drawings=[]),{str(step):model},Judge(),Path(d),10)
            self.assertEqual(ctx['complexity'],expected)
            self.assertEqual(scan.call_args.args[0],step)
