# Superpowers psilon

Six personal forks of [obra/superpowers](https://github.com/obra/superpowers) for Codex and Claude Code, plus the repository-authored Dragonfly scripting skill, the adapted Test Audit skill, the repository-authored Pareto Design Selection skill, and the adapted manual-only Retro skill. They use narrow triggers, exact-target evidence gates, proportional workflows, and repository-owned policy.

The original Superpowers plugin is not required.

`dragonfly-scripting` is repository-authored: a Dragonfly v1.34.0 Lua/EVALSHA skill grounded in a local docker lab and cited sources. Its development material (lab benchmarks, research reports, source register, eval workspace, plan and handoffs, final report) lives in `dragonfly-scripting-workbench/`, which is not a skill; start at `dragonfly-scripting-workbench/REPORT.md`.

## Skills

| Skill | Trigger |
|---|---|
| `superpowers-brainstorming-psilon` | Consequential design uncertainty or coupled-system decomposition |
| `superpowers-writing-plans-psilon` | Durable multi-stage, coordinated, risky, or cross-session plans |
| `superpowers-dispatching-parallel-agents-psilon` | Two or more proved-independent substantial workstreams |
| `superpowers-requesting-code-review-psilon` | Independent review at a material integration-risk boundary |
| `superpowers-receiving-code-review-psilon` | Concrete review feedback to assess or implement |
| `superpowers-verification-before-completion-psilon` | Fresh proof before production, security, data, concurrency, migration, contract, release, or broad-system completion |
| `dragonfly-scripting` | Any Dragonfly server-side scripting work (Lua/EVALSHA design, review, optimization, measurement) |
| `test-audit` | Authoring or changing tests, focused test review, or a requested subsystem test-pruning campaign |
| `pareto-design-selection` | Several candidate decision strategies with a visible cost of error, an owner who decides, and a pure decision core; sound cheap screening, bounded search, calibrated finalist validation, Pareto table |
| `retro` | Manual only (`/retro`, `$retro`): retrospective of the current session, named sessions, or a period; proposes up to three evidence-backed changes |
| `codex-simplify` | Codex only: any request containing `/simplify` (unconditional), or a request to review changed code for reuse, simplification, efficiency, and altitude cleanups |

`retro` is adapted from Matt Pocock's MIT-licensed `retro` skill; its license is in `retro/LICENSE`. It is manual-only in both clients through `disable-model-invocation` and `agents/openai.yaml`. Its 20-case eval protocol (not yet run on any model) is in [retro-workbench/EVALS.md](retro-workbench/EVALS.md).

`codex-simplify` is a verbatim export of Claude Code's built-in `/simplify` skill body for Codex, which has no built-in equivalent. Only its description differs: any `/simplify` in a Codex request loads it unconditionally. It is linked into the Codex directory only, because Claude Code already ships `/simplify`.

`test-audit` preserves the supplied audit and campaign workflows and states its test-value rules directly. Its language-neutral instructions and self-contained 20-case behavioral eval set are described in [test-audit-workbench/EVAL.md](test-audit-workbench/EVAL.md).

## Install for one user

Clone the repository, then link its skills into the personal skill directories for Codex and Claude Code. Existing links to this checkout are reused; a conflicting destination stops installation without replacing it:

```bash
git clone https://github.com/Pzixel/superpowers-psilon.git
cd superpowers-psilon

for target in "$HOME/.agents/skills" "$HOME/.claude/skills"; do
  mkdir -p "$target"
  for skill in superpowers-*-psilon dragonfly-scripting test-audit pareto-design-selection retro; do
    dest="$target/$skill"
    if [ -e "$dest" ] || [ -L "$dest" ]; then
      if [ "$(realpath "$dest")" != "$(realpath "$skill")" ]; then
        printf 'Conflicting skill destination: %s\n' "$dest" >&2
        exit 1
      fi
    else
      ln -s "$PWD/$skill" "$dest"
    fi
  done
done

skill=codex-simplify
dest="$HOME/.agents/skills/$skill"
if [ ! -e "$dest" ] && [ ! -L "$dest" ]; then
  ln -s "$PWD/$skill" "$dest"
elif [ "$(realpath "$dest")" != "$(realpath "$skill")" ]; then
  printf 'Conflicting skill destination: %s\n' "$dest" >&2
  exit 1
fi
```

Both clients follow these directory links. Updates to this checkout update the linked skills; inspect and validate changes before relying on them. Use a fresh session if a running session still holds an older skill body.

When a skill is retired here, delete its link in both directories by hand; the install loop never removes links.

## Validate changes

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python scripts/validate-skills.py

scripts=(
  dragonfly-scripting/scripts/*.sh
)
for script in "${scripts[@]}"; do
  bash -n "$script"
done

git diff --check
```

GitHub Actions runs the same structural, review-range, and shell checks and scans the full Git history for secrets. Review-range tests use isolated temporary Git repositories; they do not modify this checkout.

## Maintenance rules

- Keep activation descriptions specific, with positive and negative boundaries. The validator caps descriptions at 120 words and 1,024 characters; use less when the trigger permits it.
- Keep each `SKILL.md` at or below 250 lines and 2,500 words.
- Put optional examples, prompts, and detailed techniques in one-level companion files.
- Preserve strong gates and exceptions while removing duplicate prose.
- Record upstream lineage and every behavior change in [ORIGINS.md](ORIGINS.md).
- Never commit credentials, tokens, private keys, environment files, or local app artifacts.

## Provenance and license

[ORIGINS.md](ORIGINS.md) pins the upstream release and explains each adaptation. Upstream-derived material remains under the MIT license in [LICENSE.superpowers](LICENSE.superpowers).
