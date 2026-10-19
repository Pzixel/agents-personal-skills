# Rule placement and agent authority (2026-10-19)

## Decision

One owner file per rule. A rule lives where the harness injects it at the latest moment before the decision it governs: a hook refusal text, a skill step, an agent definition. The always-on files (`rules/always-on.md`, `rules/always-on-claude.md`) keep only rules that apply on every turn, plus one-line pointers. Codex gets the same text through the `install.sh` render of the always-on block. Every other copy of a rule is deleted or reduced to a pointer.

## Owner decisions (retro-evals `owner-criteria.md`, 2026-10-19)

- K27. «Все что дешево и легко откатывается агент может делать сам без апрува. Важное и дорогое - решаю сам, исполнение через план с одним одобрением.» English rule: a production write on our own objects that is cheap and easily reversible is the agent's decision, done at once, no plan and no approval. An important or expensive write, or any write without undo: the owner decides; it runs as one destructive plan.
- K28. «Агент говорит что точно команда делает (дает путь к файлу скрипта и тп) + APPROVE хук». English rule: the plan states exactly what the command does (script path, scope, count, undo) and starts only after the owner's typed `APPROVE <hash>`; an irreversible effect on client data additionally needs the owner's approval of the effect.
- K29. Decide technical questions myself and say what and why, including irreversible changes on our own objects when the undo cost is stated and recorded in `decisions/`. Escalate only money, business priorities, and irreversible effects on client data or client-visible flows, as one plain question with a recommendation.
- K30. The agent picks the Pareto finalist when the set differs only on technical axes; the owner picks, through AskUserQuestion with the table, when finalists differ on money, priority, or a client-visible effect. The agent recommends in both cases.
- K31. CASE-21 Minimum: every reply rate carries RR or hRR; the ESP breakdown shows Other and Unknown apart wherever the data separates them. (Already in `~/.codex/AGENTS.md`; no corpus change here.)

## Owner files after this change

| Rule | Owner | Pointers only |
|---|---|---|
| Authority (K29) | `~/.codex/AGENTS.md` "Decide technical questions" | `rules/always-on.md` tunable bullet, orchestrate stop condition |
| Production writes (K27, K28) | `~/.codex/AGENTS.md`; classes in `gate_policy.py` + `orchestrate-params.json` | `always-on-claude.md`, details "Destructive", orchestrate "Destructive commands", `orch/README.md` |
| Delegate brief fields | `gate_policy.brief_decision` refusal text and orchestrate step-1 template | removed from `always-on-claude.md` and details |
| Pareto chooser (K30) | `pareto-design-selection/SKILL.md` section 6 | reviewer `mode: pareto` |
| Worktree per task | `~/.codex/AGENTS.md` git section | orchestrate `<ws>` definition |

## Destructive classes, before and after

| Command | Before | After | Undo named in the reason |
|---|---|---|---|
| `kubectl scale` | approval | ours (runs at once) | scale back to the previous replica count |
| `kubectl rollout restart` | approval | ours | none needed, same spec |
| `kubectl rollout undo` | approval | ours | rollout undo to the revision before |
| `kubectl rollout pause` / `resume` | approval | ours | the opposite verb |
| Flux reconcile / suspend / resume | not classed (runs) | unchanged | - |
| `kubectl apply` / `patch` / `delete` (controller objects), `drain`, other verbs | approval | approval | - |
| SQL INSERT/UPDATE/DELETE ... WHERE, SQL from an unread file | approval | approval | - |
| `helm uninstall`, `flux delete`, `rsync --delete`, inline code that deletes files | approval | approval | - |
| DROP/TRUNCATE, DELETE without WHERE, Secret writes, namespace/PVC/CRD delete, force push, rm outside ours | impossible (refused, step skipped) | impossible as a bare command; refusal tells the agent to write the plan and ask for `APPROVE <hash>` | - |

## Always-on budget

`wc -c`: `rules/always-on.md` 3833 -> 3844 (+11, the authority parenthetical now points at the rule); `rules/always-on-claude.md` 2498 -> 2245 (-253). Net -242. The removal paying for growth: the delegate-brief bullet (old line 15), whose owner is the spawn gate's refusal.

## Rejected alternatives

- Shrinking always-on text to save tokens: always-on text measured at 3 % of spend; the gain does not pay for lost rules.
- Moving rules into skills found by description: 0 of 3 runs read the skill twice; a rule the agent must find by itself is not applied at the decision.
- Ablation of rules one by one: measures redundancy between copies, not the observed failure (two copies giving two answers).
