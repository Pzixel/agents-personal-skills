#!/usr/bin/env python3
"""PostToolUse hook for Bash, Read, Grep, Glob and Agent: secret-looking values
are masked before the result reaches a transcript.

Masks every value held in the secret vault (`~/.secrets`, exact match), every
standalone API-key-shaped token, `<name>=value` / `<name>: value` where the
name contains password, secret, token, api key, authorization, private key or
credential; `Bearer` tokens; JWTs; PEM private keys; and the `data:` fields of
a Kubernetes Secret (`kind: Secret`). Each value becomes `<redacted sha256:8>`,
so two outputs of the same secret still compare equal. Rewrites every string
inside `tool_response` via `updatedToolOutput`, keeping the tool's own shape.
Blind spots: PostToolUse does not fire for a non-zero exit (secretctl run/each
therefore always exit 0), and a value the vault does not hold is only caught by
shape. Pure policy: gate_policy.redact_secrets, vault_policy.redact_tree.
Any error exits 0.

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
import vault_files  # noqa: E402
import vault_policy  # noqa: E402


def _redact(value, tokens):
    if isinstance(value, str):
        return gate_policy.redact_secrets(value, tokens)
    if isinstance(value, dict):
        return {k: _redact(v, tokens) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, tokens) for v in value]
    return value


def main():
    payload = json.load(sys.stdin)
    response = payload.get("tool_response")
    if response is None:
        return 0
    texts, _protected = vault_files.load_vault()
    tokens = vault_policy.vault_tokens(texts)
    updated = _redact(response, tokens)
    if updated == response:
        return 0
    if isinstance(updated, dict):
        # Without dropping the persisted-output keys the harness shows its own unmasked preview.
        updated = {k: v for k, v in updated.items() if not k.startswith("persistedOutput")}
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
