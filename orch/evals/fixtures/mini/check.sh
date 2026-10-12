#!/bin/bash
# Runs the test suite; the last line is `check: passed` or `check: failed`.
cd "$(dirname "$0")"
if python3 -c '' 2>/dev/null; then PY=python3; else PY=/usr/bin/python3; fi
if "$PY" -m unittest discover -s tests -t . 2>&1; then
    echo "check: passed"
else
    echo "check: failed"
    exit 1
fi
