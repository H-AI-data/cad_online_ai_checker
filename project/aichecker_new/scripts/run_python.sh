#!/bin/sh
set -eu
checker_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -n "${AI_CHECKER_PYTHON:-}" ]; then
    checker_python=$AI_CHECKER_PYTHON
elif [ -x "$checker_root/.venv/bin/python" ]; then
    checker_python="$checker_root/.venv/bin/python"
elif [ -x /opt/anaconda3/envs/aichecker_new/bin/python ]; then
    checker_python=/opt/anaconda3/envs/aichecker_new/bin/python
else
    checker_python=python3
fi
exec "$checker_python" "$@"
