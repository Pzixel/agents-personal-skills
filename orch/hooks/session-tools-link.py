#!/usr/bin/env python3
"""SessionStart hook: make ~/.claude/tools and ~/.claude/hooks resolve to this plugin.

Agent and skill texts call `~/.claude/tools/wait-for`, `~/.claude/hooks/handoff.sh`
and friends, but the model's Bash never sees $CLAUDE_PLUGIN_ROOT, and a fresh
HOME (a `claude plugin eval` run) has neither path. For each of `tools` and
`hooks`: when ~/.claude/<name> does not exist, link it to
$CLAUDE_PLUGIN_ROOT/<name>; an existing path (file, directory or link, dangling
or not) is left alone. No output, exit 0 on anything.
"""

import os
import sys


def link_one(root, config, name):
    target = os.path.join(root, name)
    link = os.path.join(config, name)
    if not os.path.isdir(target) or os.path.lexists(link):
        return
    os.makedirs(config, exist_ok=True)
    os.symlink(target, link)


def main():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or ""
    if not root:
        return
    config = os.path.join(os.path.expanduser("~"), ".claude")
    for name in ("tools", "hooks"):
        link_one(root, config, name)


try:
    sys.stdin.read()
    main()
except BaseException:
    pass
sys.exit(0)
