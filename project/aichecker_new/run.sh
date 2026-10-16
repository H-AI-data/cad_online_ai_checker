#!/bin/sh
set -eu
checker_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sh "$checker_root/scripts/run_python.sh" "$checker_root/evaluate.py" "$@"
