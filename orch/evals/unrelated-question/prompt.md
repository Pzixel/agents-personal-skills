---
tags: [tierA, negative]
schema_version: "1.1"
name: unrelated-question
runs: 3
max_turns: 60
timeout_seconds: 1800
allowed_tools: [Agent, Skill, Read, Bash, Write, Edit, Grep, Glob]
description: a plain knowledge question loads no skill and spawns no agent
expected_outcome: a two-sentence answer about CNAME records with no Skill and no Agent call
---

What is a CNAME record? Two sentences.
