#!/bin/bash
# Shim: the budget gate lives in agent-turn-budget.py (context/cost measurement
# plus gate_policy.agent_budget_decision). Registered command path unchanged.
# Any failure of the Python side is fail-open and quiet, as before.
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$D/agent-turn-budget.py" 2>/dev/null || exit 0
