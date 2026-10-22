#!/usr/bin/env python3
"""SessionStart hook: tell the session when a skill or agent name has two sources.

Silent when none; otherwise one line (SessionStart stdout on exit 0 goes into
the session context). Exit 0 on anything: a scan error never blocks a session.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "tools"))

try:
    sys.stdin.read()
    import dup_policy

    found = dup_policy.duplicates(dup_policy.scan(dup_policy.config_dir()))
    if found:
        print(
            "Skill/agent names visible from two sources (remove one copy; "
            "rerun install.sh for links into agents-personal-skills): " + "; ".join(found)
        )
except BaseException:
    pass
sys.exit(0)
