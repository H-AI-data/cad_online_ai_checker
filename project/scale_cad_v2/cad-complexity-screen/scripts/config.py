"""Paths shared by the complexity scanners, supplied by the public runner."""

import os
from pathlib import Path


def required_path(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is unset; start the scan with run.py")
    return Path(value).expanduser().resolve()


WORK = required_path("CAD_COMPLEXITY_WORK")
HARBOR = required_path("CAD_COMPLEXITY_HARBOR")
OUTPUT = required_path("CAD_COMPLEXITY_OUTPUT")
SCRIPTS = Path(__file__).resolve().parent
