"""Check that each parts.pdf has an unambiguous matching source spec."""

import json

from config import WORK
from run_complexity_screen import current_case_source


cases = json.loads((WORK / "selected.json").read_text())
for case in cases:
    root, spec = current_case_source(case)
    print(f"{case['name']}: {len(spec['families'])} families; {root}")
print(f"Inputs aligned: {len(cases)} cases")
