#!/bin/sh
set -eu
checker_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sh "$checker_dir/../../scripts/run_python.sh" "$checker_dir/evaluate.py" "$@"

