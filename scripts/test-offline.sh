#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
export PYTHONPATH="$ROOT/src/subtranslate${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -B "$ROOT/scripts/run_offline_tests.py"
