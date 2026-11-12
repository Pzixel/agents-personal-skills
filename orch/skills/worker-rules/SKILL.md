---
name: worker-rules
description: Preloaded into orch agents in place of the user-level instruction files; never invoke.
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/skills/worker-rules/assemble.py" *)
---
The rules below bind you as the user-level instructions would. Owner-only text (reply shape, approvals, questions, waits, memory) is left out where its source marks it; a source without well-formed owner-only markers arrives whole, so it may hold rules addressed to the owner's session, which do not bind you.

!`python3 "${CLAUDE_PLUGIN_ROOT}/skills/worker-rules/assemble.py" 1`

!`python3 "${CLAUDE_PLUGIN_ROOT}/skills/worker-rules/assemble.py" 2`

!`python3 "${CLAUDE_PLUGIN_ROOT}/skills/worker-rules/assemble.py" 3`

!`python3 "${CLAUDE_PLUGIN_ROOT}/skills/worker-rules/assemble.py" 4`
