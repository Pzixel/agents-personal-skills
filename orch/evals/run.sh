#!/bin/bash
# Eval suite wrapper. Usage:
#   evals/run.sh iterate <case>   one run of one case, with-plugin arm only
#   evals/run.sh tierA            tier A + negatives, with and without the plugin, 3 runs
#   EVAL_MODEL=<model id> pins the main-session model for any mode.
#   evals/run.sh full             every tier (A, private B/C), 3 runs, with and without,
#                                 cost ceiling $300, two runs at a time, temp dirs kept
# Extra arguments after the mode are passed to `claude plugin eval` (e.g. --keep-temp).
# `full` prints its projected cost first (evals/tools/project_cost.py).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-}"; shift || true
STAMP="$(date +%Y%m%d-%H%M%S)"
RESULTS="$ROOT/evals/results"
mkdir -p "$RESULTS"
COMMON=(--trust-plugin --no-publish --scaffold --allow-tools Bash Write Edit)
# Pin the main-session model: without it the run follows the account default and mixes models.
[ -n "${EVAL_MODEL:-}" ] && COMMON+=(--model "$EVAL_MODEL")

case "$MODE" in
    iterate)
        CASE="${1:?usage: run.sh iterate <case>}"; shift
        ARGS=(--case "$CASE" --runs 1 --ablation none)
        ;;
    tierA)
        ARGS=(--tag tierA --runs 3 --ablation with-without)
        ;;
    full)
        # Tags are OR-ed; untagged probes (evals/private/probe-*) stay out.
        TAGS=(tierA tierB tierC)
        ARGS=()
        for t in "${TAGS[@]}"; do ARGS+=(--tag "$t"); done
        ARGS+=(--runs 3 --ablation with-without --max-cost-usd 300 -j 2 --keep-temp)
        python3 "$ROOT/evals/tools/project_cost.py" --runs 3 --arms 2 "${TAGS[@]}"
        ;;
    *)
        sed -n '2,6p' "$0" >&2
        exit 64
        ;;
esac

OUT="$RESULTS/$MODE-$STAMP"
exec claude plugin eval "$ROOT" "${COMMON[@]}" "${ARGS[@]}" \
    --json "$OUT.json" --report "$OUT.html" "$@"
