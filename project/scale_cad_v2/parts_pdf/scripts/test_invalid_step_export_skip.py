"""Regression for withdrawing a case after an invalid single-part STEP export."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_parts import run_family_step_export
from export_family_steps import (InvalidExportedPartStep,
                                 read_valid_exported_solids)
from step_export_status import (INVALID_EXPORTED_STEP_EVENT,
                                INVALID_EXPORTED_STEP_EXIT_CODE)


class InvalidStepExportSkipTests(unittest.TestCase):
    def test_invalid_roundtrip_is_classified(self):
        class InvalidSolid:
            Volume = 1.0

            def isValid(self):
                return False

            def isClosed(self):
                return True

        fake_part = mock.Mock()
        fake_part.read.return_value.Solids = [InvalidSolid()]
        with self.assertRaises(InvalidExportedPartStep) as error:
            read_valid_exported_solids('part.step', fake_part, 'family_017',
                                       83, 'source_roundtrip')
        self.assertEqual(error.exception.family_id, 'family_017')

    def test_invalid_export_withdraws_public_parts_and_marks_assembly_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root/'work'
            work.mkdir()
            output = root/'case/environment/input/parts.pdf'
            output.parent.mkdir(parents=True)
            output.write_bytes(b'incomplete pdf')
            step_dir = output.parent/'provided_part_files'
            step_dir.mkdir()
            (step_dir/'family_001.step').write_bytes(b'incomplete step')
            source = root/'reference.step'
            source.write_bytes(b'source')
            event = {'event': INVALID_EXPORTED_STEP_EVENT,
                     'family': 'family_017', 'reference_index': 83,
                     'phase': 'source_roundtrip', 'detail': 'invalid solid'}
            result = subprocess.CompletedProcess(
                ['export'], INVALID_EXPORTED_STEP_EXIT_CODE,
                stdout=json.dumps(event) + '\n', stderr='')
            with mock.patch('build_parts.subprocess.run', return_value=result):
                completed = run_family_step_export(
                    ['export'], work=work, output=output, step_dir=step_dir,
                    source_step=source)
            self.assertFalse(completed)
            self.assertFalse(output.exists())
            self.assertFalse(step_dir.exists())
            report = json.loads((work/'skipped_assembly.json').read_text())
            self.assertEqual(report['status'], 'skipped_assembly')
            self.assertEqual(report['family'], 'family_017')

    def test_other_export_errors_still_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'reference.step'
            source.write_bytes(b'source')
            result = subprocess.CompletedProcess(['export'], 1, stdout='',
                                                 stderr='ordinary failure')
            with mock.patch('build_parts.subprocess.run', return_value=result):
                with self.assertRaises(subprocess.CalledProcessError):
                    run_family_step_export(
                        ['export'], work=root, output=root/'parts.pdf',
                        step_dir=root/'provided_part_files', source_step=source)
            self.assertFalse((root/'skipped_assembly.json').exists())


if __name__ == '__main__':
    unittest.main()
