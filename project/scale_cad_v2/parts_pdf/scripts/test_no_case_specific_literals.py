"""Guard the parts-PDF pipeline against source-specific defaults and branches."""

from pathlib import Path
import re
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
PIPELINE_SCRIPTS = (
    "parts_pdf/scripts",
    "cad-complexity-screen/scripts",
    "step-exploded-view/scripts",
    "cad-dimension-sheets/scripts",
)
SOURCE_SPECIFIC = re.compile(
    r"\bcad_\d+_g\d+\b|\bfamily_\d{3}\b|/Users/",
)


class SourceSpecificLiteralTest(unittest.TestCase):
    def test_production_scripts_do_not_name_an_individual_case_or_part(self):
        findings = []
        for directory in PIPELINE_SCRIPTS:
            for path in (REPOSITORY / directory).rglob("*.py"):
                if path.name.startswith("test_"):
                    continue
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if SOURCE_SPECIFIC.search(line):
                        findings.append(f"{path.relative_to(REPOSITORY)}:{number}")
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
