#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"
PYTHON_BIN="${PYTHON:-python3}"
export PIP_NO_DEPS=false

if [ ! -x .venv/bin/python ]; then
  "$PYTHON_BIN" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
exec .venv/bin/python start_yanggumi.py
