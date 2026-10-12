#!/usr/bin/env python3
"""PostToolUse hook for Bash: secret-looking values are masked before the
output reaches a transcript.

Masks `<name>=value` / `<name>: value` where the name contains password,
secret, token, api key, authorization, private key or credential; `Bearer`
tokens; JWTs; PEM private keys; and the `data:` fields of a Kubernetes Secret
(`kind: Secret`). Each value becomes `<redacted sha256:8>`, so two outputs of
the same secret still compare equal. Rewrites `tool_response.stdout/stderr`
via `updatedToolOutput` (Claude Code >= 2.1.287 accepts the Bash response
shape). Blind spots: PostToolUse does not fire for a non-zero exit, and a raw
`-o jsonpath` value has no name to match. Pure policy:
gate_policy.redact_secrets. Any error exits 0.

Why: six irreversible secret exposures in the reviewed sessions, two with no
right to rotate (retro 2026-10-12).
"""
import json
import os
import sys

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = (
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)
sys.path.insert(0, TOOLS_DIR)

import gate_policy  # noqa: E402


def main():
    payload = json.load(sys.stdin)
    response = payload.get("tool_response")
    if not isinstance(response, dict):
        return 0
    stdout = response.get("stdout") or ""
    stderr = response.get("stderr") or ""
    new_out = gate_policy.redact_secrets(stdout)
    new_err = gate_policy.redact_secrets(stderr)
    if new_out == stdout and new_err == stderr:
        return 0
    # Without dropping the persisted-output keys the harness shows its own unmasked preview.
    updated = {k: v for k, v in response.items() if not k.startswith("persistedOutput")}
    updated["stdout"] = new_out
    updated["stderr"] = new_err
    json.dump(
        {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": updated}},
        sys.stdout,
    )
    return 0


try:
    sys.exit(main())
except SystemExit:
    raise
except BaseException:
    sys.exit(0)
