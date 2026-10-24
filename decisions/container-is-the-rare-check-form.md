# A container is the rare check form

## Decision

The always-on bullet (rules/always-on.md, section "Scope"), verbatim:

- A check runs in the cheapest form that decides its kind: logic as pure tests run natively; a third-party store through the one shared local container per store on this machine (a database per task, dropped when the task ends; never an image, container or volume per task); real behaviour and load on production as the last step, never reproduced locally. A container or image is built only to compare two adjacent implementation variants when the conclusion holds without a deploy; it carries that reason and is removed when the number is read. A constraint that forces a container (a crate that builds only under Linux) is the defect to fix, never to wrap.

The orchestrate builder template `check:` line and the brief-gate refusal text (orch/tools/gate_policy.py) say the same.

## Why

Owner, 2026-10-23: docker rarely, only for a two-variant comparison without a cluster deploy; logic in pure tests; load on production as the last step.

Measured over 5 weeks to 2026-10-23: 4468 docker calls in 67 sessions, 592 image builds; 174 images (~141 GB) and 504 volumes deleted on 2026-10-23, after a full disk the same day.

## Drivers found

- one repository's AGENTS.md mandated docker for all third-party infrastructure.
- The orchestrate `check:` template: "the run on the real path that proves the work; never only unit tests", which read as a container per task.
- another repository's tests linked only in a Linux image stage because the FoundationDB client library was missing on the host, while its README documents the native route.

## Alternatives compared

- PreToolUse gate on docker build/run with a `# DOCKER:` acknowledgement: held back. The drivers were rule text and one missing library; usage is measured again live before a refusal is added. Measured 2026-10-24: two sessions started after the rule merged left no per-task container; the 138 anonymous volumes seen that day came from a dry-run script (`bash run.sh` with six `docker run` inside) written before the rule in a session on the old snapshot, a shape a Bash-command gate would not see. Still held back (retro-evals runs/docker-form/RESULT.md, «P3 decision»).
- A dev ClickHouse in the cluster: a shared production-adjacent resource (CASE-48 class: an exec in a prod broker OOM-killed it).
- Pure tests only, SQL verified on production: leaves no pre-merge SQL check.

## Evidence

Rule text comparison: CASE-49B 1/3 -> 3/3; controls CASE-26, CASE-5, CASE-7 held. Retro pointers: retro-evals cases/CASE-49, runs/docker-form (RESULT.md).
