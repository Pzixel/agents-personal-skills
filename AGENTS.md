# Repository rules

## No private information

This repository is public. Nothing here may identify or reveal other repositories or organizations: no client or company names, private repository names, their paths, code, schemas, hostnames, URLs, people, metrics, or incidents. This covers skills, examples, evals, fixtures, notes, and commit messages. Use generic placeholders (`example-service`, `orders` table, `acme`).

Two exceptions:
- `ORIGINS.md` may name the internal repositories a skill came from; every other detail there stays anonymized.
- A small script may serve as an eval input or example once scrubbed of identifying names, paths, and internal structure.

## How changes land

Pull requests are disabled on this repository. A change is built on a branch cut from the fresh tip of `origin/master` in a worktree under `.worktrees/<task>/`, reviewed through the `orch` flow when it touches code, and merged by a fast-forward push to `master`. The push command carries the `CHECK_RAN`, `PROD_POSITION`, and `WAY_BACK` lines the merge gate requires. The branch and its worktree are deleted in the same step. GitHub Actions runs on every push: the skill validator, shell syntax, `git diff --check`, the `orch` tool tests on Python 3.9 and 3.12, and a gitleaks scan of the full history.

Record upstream lineage and behaviour changes with their evidence in `ORIGINS.md`.
