---
tags: [tierA, negative]
schema_version: "1.1"
name: one-line-typo-fix
runs: 3
max_turns: 60
timeout_seconds: 1800
allowed_tools: [Agent, Skill, Read, Bash, Write, Edit, Grep, Glob]
description: a one-line edit is done in the main session without spawning agents
expected_outcome: the typo memroy in README.md is corrected and committed, no Agent call
---

fix the typo in README.md, commit
