# The orch code-search agent is named `scout`, not `Explore`

Decision: the orch agent that locates code is `orch:scout`. The main session's spawn rights refuse bare `Explore` with a reason naming `orch:scout`.

Why: Claude Code ships a built-in agent named `Explore` (a generic explorer: no bounded 8-question brief, its own model). A user-level `~/.claude/agents/Explore.md` used to shadow it; once orch agents are installed only through the plugin (`orch:<name>`), the model sees the built-in `Explore` next to `orch:Explore`. The gates strip the `<plugin>:` prefix, so a spawn of `Explore` was allowed and reached the built-in agent.

Compared against:
- Keep `Explore` and teach the duplicate detector a builtin source: a builtin collision is permanent, so the SessionStart line would fire in every session (a false fire by the alert rule), and the repository cannot hide a built-in agent.
- Keep a user-level `Explore.md` shadowing the built-in: that is the duplicate (bare and `orch:` copy of every agent) this change removes.

Hiding the built-in from the model is a user permission (`Agent(Explore)` in `permissions.deny`), outside this repository.
