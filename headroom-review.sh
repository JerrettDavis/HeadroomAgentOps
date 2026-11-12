#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
task_python="${AGENTOPS_PYTHON:-python3}"
if [ -z "${AGENTOPS_PYTHON:-}" ] && [ -x .venv/bin/python ]; then
    task_python=.venv/bin/python
fi
exec "$task_python" -m agentops "$@"
