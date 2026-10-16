# agents-personal-skills

Personal skills for Codex and Claude Code, plus `orch`, a Claude Code plugin that runs a measured build-and-review flow. Five skills are forks of [obra/superpowers](https://github.com/obra/superpowers) cut down to their skill-specific contracts; the rest are repository-authored or adapted from other MIT-licensed sources. The original Superpowers plugin is not required.

## Skills

| Skill | Trigger |
|---|---|
| `orch` | Claude Code plugin: `/orch:orchestrate` for every code change beyond one line (builder, two reviewers, cross-check, fix rounds), hooks that enforce the gates, agent definitions. See [orch/README.md](orch/README.md) |
| `superpowers-writing-plans-psilon` | Durable multi-stage, coordinated, risky, or cross-session plans |
| `superpowers-dispatching-parallel-agents-psilon` | Two or more proved-independent substantial workstreams |
| `superpowers-requesting-code-review-psilon` | Independent review at a material integration-risk boundary |
| `superpowers-receiving-code-review-psilon` | Concrete review feedback to assess or implement |
| `superpowers-verification-before-completion-psilon` | Fresh proof before production, security, data, concurrency, migration, contract, release, or broad-system completion |
| `dragonfly-scripting` | Any Dragonfly server-side scripting work (Lua/EVALSHA design, review, optimization, measurement) |
| `test-audit` | Authoring or changing tests, focused test review, or a requested test-pruning campaign |
| `pareto-design-selection` | Several candidate decision strategies with a visible cost of error, an owner who decides, and a pure decision core |
| `retro` | Manual only (`/retro`, `$retro`): retrospective of the current session, named sessions, or a period |
| `codex-simplify` | Codex only: any request containing `/simplify`, or a request to review changed code for reuse, simplification, efficiency, and altitude cleanups |

Development material lives next to its skill and is not a skill: `dragonfly-scripting-workbench/` (lab, benchmarks, sources; start at [REPORT.md](dragonfly-scripting-workbench/REPORT.md)), `test-audit-workbench/` (20-case behavioural eval, [EVAL.md](test-audit-workbench/EVAL.md)), `retro-workbench/` (eval protocol, [EVALS.md](retro-workbench/EVALS.md)).

`codex-simplify` is a verbatim export of Claude Code's built-in `/simplify` body for Codex, which has no built-in equivalent; it is linked into the Codex directory only. `retro` is adapted from Matt Pocock's MIT-licensed `retro` skill (`retro/LICENSE`).

## Install for one user

On macOS, clone the repository and run the installer. It links the skills into the personal skill directories of both clients (`codex-simplify` into the Codex directory only, `orch` into the Claude Code directory only) and links `agents`, `hooks` and `tools` of the Claude Code directory to `orch/`; `CLAUDE_CONFIG_DIR` selects the Claude Code directory, default `~/.claude`. An existing link to this checkout is kept; any other existing destination stops the installer before it changes anything. Links into this checkout whose skill was retired are removed. The hooks and settings of `orch` are described in [orch/README.md](orch/README.md).

```bash
git clone https://github.com/Pzixel/agents-personal-skills.git && cd agents-personal-skills && ./install.sh both
```

The argument picks the client: `claude`, `codex`, or `both`. Rerun it after every pull. Both clients follow the links, so updating the checkout updates the skills; a running session keeps the body it loaded.

## Validate changes

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python scripts/validate-skills.py
for script in dragonfly-scripting/scripts/*.sh; do bash -n "$script"; done
for test in orch/tools/tests/test_*.py; do python3 "$test"; done
git diff --check
```

GitHub Actions runs the same checks on every push and scans the full history for secrets.

## Maintenance rules

- Descriptions carry positive and negative trigger boundaries. The validator caps them at 120 words and 1,024 characters and a `SKILL.md` body at 250 lines.
- A skill body keeps only what moves decisions: decision rules the model gets wrong on its own, output shapes, named thresholds, and tokens a hook or tool checks. Examples and procedures go to one-level companion files.
- Record lineage and every behaviour change with its evidence in [ORIGINS.md](ORIGINS.md).
- How changes land, and what must never be committed, is in [AGENTS.md](AGENTS.md).

## Provenance and license

[ORIGINS.md](ORIGINS.md) pins the upstream release and records each adaptation. Upstream-derived material remains under the MIT license in [LICENSE.superpowers](LICENSE.superpowers).
