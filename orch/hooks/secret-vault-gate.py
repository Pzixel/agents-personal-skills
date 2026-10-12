#!/usr/bin/env python3
"""PreToolUse hook: no tool call may read secret material directly.

Refuses a Bash command that names the vault (`~/.secrets`), globs hidden
directories under HOME, prints Kubernetes Secret payloads, or names a path from
the vault's `.protected-paths`; a Read/Edit/Write/Grep/Glob inside the vault or
a search whose root contains it; and any tool input (prompt, message, file
content) that carries a known vault value. The alternative is always
`~/.claude/tools/secretctl`, which uses values without printing them.

Pure policy: vault_policy.secret_access_violation. Fails closed when the hook
itself breaks and the input mentions the vault; otherwise a broken hook allows.
"""
import json
import os
import sys

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = (
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)
sys.path.insert(0, TOOLS_DIR)

import vault_files  # noqa: E402
import vault_policy  # noqa: E402

RAW = ""


def main():
    global RAW
    RAW = sys.stdin.read()
    payload = json.loads(RAW)
    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    texts, protected = vault_files.load_vault()
    tokens = vault_policy.vault_tokens(texts)
    reason = vault_policy.secret_access_violation(
        tool, tool_input, cwd, vault_files.VAULT_DIR, tokens, protected
    )
    if reason:
        print(
            "Refused by secret-vault gate: %s. Secret values never enter a transcript. "
            "Use `~/.claude/tools/secretctl` (list / run / each / import / pull-k8s / merge / "
            "apply-k8s) to use them without printing, and continue the task." % reason,
            file=sys.stderr,
        )
        return 2
    return 0


try:
    sys.exit(main())
except SystemExit:
    raise
except BaseException:
    if vault_policy.VAULT_DIR_NAME in RAW:
        print("Refused by secret-vault gate: hook error while the input names the vault.", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)
