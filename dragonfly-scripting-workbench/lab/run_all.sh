#!/usr/bin/env bash
# Bring the lab up and run every benchmark in lab/bench/, regenerating RESULTS.md.
# Safe to re-run. Does NOT tear the lab down -- the phase lead does that at the end
# with `docker compose -f lab/compose.yaml down -v`.
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
[ -x "$PY" ] || { echo "missing $PY -- run: python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }

echo "== bringing the lab up =="
docker compose up -d --wait

# Q8 (part B) needs the single-shard node; start it only if a benchmark asks for
# port 6381, so the default run stays within the memory budget.
if grep -rqs "SINGLE_PORT\|6381" bench/*.py; then
  echo "== starting the 'single' profile (proactor_threads=1, :6381) =="
  docker compose --profile single up -d --wait single
fi

mkdir -p results
status=0
for f in $(ls bench/q*.py | sort -V); do
  echo
  echo "================ $f ================"
  if ! "$PY" "$f"; then
    echo "!! $f FAILED (continuing)" >&2
    status=1
  fi
done

echo
echo "== RESULTS.md =="
head -40 RESULTS.md
exit $status
