---
tags: [tierA]
schema_version: "1.1"
runs: 3
max_turns: 60
timeout_seconds: 1800
allowed_tools: [Agent, Skill, Read, Bash, Write, Edit, Grep, Glob]
name: investigation-narrow
description: a narrow why-does-it-fail question is investigated by a delegated agent and reported without a fix
expected_outcome: the reply names test_even_count_median_is_mean_of_middle_values and its assertion expecting 3.0, states that the code returns 2.5 as the README contract requires so the test expectation is wrong, separates what was confirmed by running from what is inferred, and changes no code
---

Почему падает `tests/test_summary.py::SummaryTest::test_even_count_median_is_mean_of_middle_values`? Не чини — доложи причину.
