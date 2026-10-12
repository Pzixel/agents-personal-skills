"""Pure decision policy for the no-poll and main-budget PreToolUse gates.

Convention: ``cmd_or_path`` is the Bash ``command`` for Bash calls, and the bare
target path (``file_path``/``path``/``pattern``) for Read/Grep/Glob calls; a
bare path is recognised by having no whitespace and starting with ``/``, ``~``
or ``./``.

No I/O of any kind lives here: every function takes facts as values and returns
a complete decision dict.
"""

import fnmatch
import os
import re
import shlex

POLL = "poll"
WAIT_FOR = "wait_for"
EXEMPT = "exempt"
HANDS_ON = "hands_on"

# Tools that exist to keep work off the main context; invoking them must never
# be charged to the main-session budget.
_EXEMPT_TOOLS = frozenset(
    {
        "scout",
        "scout.py",
        "wait-for",
        "usage-audit",
        "usage-audit.py",
        "usage-report.py",
    }
)

# Handoffs and reports are the cheap summaries the whole delegation scheme is
# built on: reading one is never the expensive thing.
_REPORT_GLOBS = ("handoff-*.md", "handoff.md", "*-report*.md", "report-*.md")

_SHELL_KEYWORDS = frozenset(
    {
        "while",
        "until",
        "for",
        "do",
        "done",
        "if",
        "then",
        "elif",
        "else",
        "fi",
        "case",
        "esac",
        "select",
        "repeat",
        "time",
        "nohup",
        "exec",
        "command",
        "builtin",
        "!",
    }
)

# Filters that neither justify an exemption nor destroy one when piped onto an
# inspection command.
_NEUTRAL_TOOLS = frozenset({"head", "tail", "wc", "tr", "sort", "uniq", "cut", "jq"})

_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SEPARATORS = (";", "&", "|", "\n", "{", "}", "(", ")")

_STAT_FLAGS = ("--stat", "--numstat", "--shortstat", "--name-status", "--name-only")

# A plugin agent carries its plugin's name as a prefix (`orch:builder`); rights
# and budgets are keyed by the bare type.
_AGENT_TYPE_PREFIX = re.compile(r"^[A-Za-z0-9_-]+:")


def agent_type_name(raw):
    """The agent type without one leading ``<plugin>:`` prefix."""
    return _AGENT_TYPE_PREFIX.sub("", (raw or "").strip(), count=1)


def _strip_quotes(cmd):
    """Blank out single/double-quoted spans so their contents cannot be read as
    shell structure (``grep -r "sleep" .`` must not look like a sleep call)."""
    out = []
    quote = None
    i = 0
    n = len(cmd)
    while i < n:
        ch = cmd[i]
        if quote is None:
            if ch == "\\":
                out.append(" ")
                i += 2
                continue
            if ch in ("'", '"'):
                quote = ch
                out.append(" ")
                i += 1
                continue
            out.append(ch)
            i += 1
            continue
        if ch == "\\" and quote == '"':
            i += 2
            continue
        if ch == quote:
            quote = None
        i += 1
    return "".join(out)


def _segments(cmd):
    """Split a quote-stripped command into shell segments on separators."""
    parts = []
    buf = []
    for ch in cmd:
        if ch in _SEPARATORS:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p for p in (s.strip() for s in parts) if p]


def _tokens_after_keywords(segment):
    """Tokens of one segment with leading keywords/env-assignments removed, so
    that ``tokens[0]`` is the command actually being invoked."""
    tokens = segment.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        low = tok.lower()
        if low in _SHELL_KEYWORDS:
            if low == "repeat" and i + 1 < len(tokens) and tokens[i + 1].isdigit():
                i += 1
            i += 1
            continue
        if _ENV_ASSIGN.match(tok):
            i += 1
            continue
        break
    return _unwrap(tokens[i:])


# Wrappers that stand in front of the command that actually matters
# (`rtk git status`, `python3 usage-audit.py`, `poetry run pytest ...`).
_WRAPPERS = {
    "rtk": ("proxy",),
    "poetry": ("run",),
    "uv": ("run", "tool"),
    "env": (),
    "node": (),
    "bash": (),
    "sh": (),
    "zsh": (),
}
_PYTHON = re.compile(r"^python[0-9.]*$")


def _unwrap(tokens):
    """Drop interpreter/wrapper prefixes so the wrapped program is classified."""
    for _ in range(4):
        if not tokens:
            return tokens
        prog = os.path.basename(tokens[0].rstrip("/"))
        if prog in _WRAPPERS:
            tokens = tokens[1:]
            while tokens and tokens[0] in _WRAPPERS.get(prog, ()):
                tokens = tokens[1:]
        elif _PYTHON.match(prog):
            tokens = tokens[1:]
        else:
            return tokens
        while tokens and tokens[0].startswith("-"):
            tokens = tokens[1:]
    return tokens


def _program(tokens):
    if not tokens:
        return ""
    return os.path.basename(tokens[0].rstrip("/"))


def _is_bare_path(text):
    return bool(text) and not any(c.isspace() for c in text) and (
        text.startswith("/") or text.startswith("~") or text.startswith("./")
    )


def _is_report_path(text):
    base = os.path.basename(text.rstrip("/"))
    return any(fnmatch.fnmatch(base, pat) for pat in _REPORT_GLOBS)


def _segment_class(tokens):
    prog = _program(tokens)
    args = tokens[1:]
    if prog in _EXEMPT_TOOLS:
        return EXEMPT
    if prog == "git":
        sub = args[0] if args else ""
        if sub == "status":
            return EXEMPT
        if sub == "log" and "--oneline" in args:
            return EXEMPT
        if sub in ("diff", "show") and any(f in args for f in _STAT_FLAGS):
            return EXEMPT
        return HANDS_ON
    if prog == "gh":
        if args[:2] in (["pr", "view"], ["pr", "checks"], ["run", "view"]):
            return EXEMPT
        return HANDS_ON
    if prog in _NEUTRAL_TOOLS:
        return "neutral"
    return HANDS_ON


def _is_pacing_sleep(tokens, min_seconds):
    """A ``sleep`` whose every argument is a number of seconds below the
    polling threshold paces requests; it is work, not a wait."""
    args = tokens[1:]
    if not args:
        return False
    total = 0.0
    for arg in args:
        try:
            total += float(arg)
        except ValueError:
            return False
    return total < min_seconds


def classify_command(cmd_or_path, params=None):
    """Return one of ``poll``/``wait_for``/``exempt``/``hands_on``.

    A ``sleep`` shorter than ``poll_sleep_min_seconds`` (from ``params``) is
    pacing between requests and does not make the command a poll."""
    if not cmd_or_path or not cmd_or_path.strip():
        return HANDS_ON

    text = cmd_or_path.strip()
    if _is_bare_path(text) and _is_report_path(text):
        return EXEMPT

    stripped = _strip_quotes(text)
    segment_tokens = [_tokens_after_keywords(seg) for seg in _segments(stripped)]
    programs = [_program(t) for t in segment_tokens]

    if any(p == "wait-for" for p in programs):
        return WAIT_FOR
    min_sleep = float(_merged_params(params)["poll_sleep_min_seconds"])
    if any(
        p == "sleep" and not _is_pacing_sleep(t, min_sleep)
        for p, t in zip(programs, segment_tokens)
    ):
        return POLL

    classes = [_segment_class(t) for t in segment_tokens if t]
    if classes and EXEMPT in classes and HANDS_ON not in classes:
        return EXEMPT
    return HANDS_ON


_SUBAGENT_PROTOCOL = (
    "A Bash call without a timeout is moved to the background by the harness after the "
    "default 2 minutes (120000 ms), and a command the harness moves to the background "
    "is the trap: every Bash call that can run past 2 minutes sets timeout 600000 and "
    "finishes under it. "
    "A subagent runs every command in the foreground with Bash timeout 600000. Work "
    "longer than one call is a script that keeps its checkpoint on disk (cursor, "
    "counter, partial output) and runs in slices of at most ~570 s, one slice per Bash "
    "call, all calls in the same turn, the last slice reporting. External state is "
    "awaited with `~/.claude/tools/wait-for --every 30 --max 570 -- <condition>` in "
    "the foreground, repeated in the same turn; a subagent never ends its turn to wait."
)

_POLL_REASON = (
    "Polling loops are refused: a `sleep` of the polling threshold or longer, alone or "
    "in a `while`/`until` loop, burns a model turn per tick and blocks the call. A short "
    "`sleep` pacing requests is allowed. A loop inside ONE Bash call does not itself "
    "cost a model turn per iteration; repeated tool round trips do. " + _SUBAGENT_PROTOCOL + " The main session "
    "waits on external state with a Monitor whose command prints nothing until the "
    "state is terminal, or a `watcher` agent, or background wait-for only for a few "
    "minutes (the harness kills background Bash tasks with exit 144 at its own events, "
    "and a killed task reports nothing). Escape hatch: include the literal "
    "NO_POLL_OVERRIDE in the command with a reason; it overrides the polling heuristic "
    "only, and every use is logged."
)

_SUBAGENT_BACKGROUND_REASON = (
    "run_in_background is refused for subagents: the harness kills background Bash "
    "tasks with exit 144 at its own events, and a subagent that ends its turn to wait "
    "for the notification is never resumed. " + _SUBAGENT_PROTOCOL + " Escape hatch: "
    "include the literal NO_POLL_OVERRIDE in the command with a reason; it is logged, "
    "and you must then block in the same turn on a foreground `wait-for` (timeout "
    "600000, `--max 570`, repeated on exit 124) until that task has finished."
)

_BASH_TIMEOUT_DEFAULT_MS = 120000  # the harness default when a call sets none
_WAIT_FOR_DEFAULT_MAX = 1800.0  # tools/wait-for parse_args default
_WAIT_FOR_REASON = (
    "This foreground wait-for would outlive its Bash call: the harness moves a call "
    "that reaches its timeout (default 120000 ms) to the background, which is the "
    "trap a subagent never recovers from. Fix: pass `--max {max_s}` to wait-for and "
    "set `timeout: {timeout_ms}` on the Bash call; on exit 124 repeat the same call in "
    "the same turn. (Seen: --max {seen_max:g}, timeout {seen_timeout} ms.)"
)


def _wait_for_max(cmd):
    """The ``--max`` seconds of the first wait-for invocation in ``cmd``, the
    tool's default when absent, or None when the value is not a number."""
    for segment in _segments(_strip_quotes(cmd)):
        tokens = _tokens_after_keywords(segment)
        if _program(tokens) != "wait-for":
            continue
        args = tokens[1:]
        for i, arg in enumerate(args):
            if arg == "--":
                break
            value = None
            if arg == "--max" and i + 1 < len(args):
                value = args[i + 1]
            elif arg.startswith("--max="):
                value = arg.split("=", 1)[1]
            if value is not None:
                try:
                    return float(value)
                except ValueError:
                    return None
        return _WAIT_FOR_DEFAULT_MAX
    return _WAIT_FOR_DEFAULT_MAX


def poll_decision(cmd, run_in_background, agent_type="", params=None, timeout_ms=None):
    """Subagents: refuse any background command, and a foreground wait-for
    whose --max would outlive its Bash timeout. Everyone: refuse a foreground
    polling construct. Main session background calls pass.

    ``timeout_ms`` is the Bash call's timeout; None means the harness default."""
    if run_in_background:
        if agent_type:
            return {"decision": "deny", "reason": _SUBAGENT_BACKGROUND_REASON}
        return {"decision": "allow"}
    kind = classify_command(cmd, params)
    if kind == POLL:
        return {"decision": "deny", "reason": _POLL_REASON}
    if kind == WAIT_FOR and agent_type:
        merged = _merged_params(params)
        max_allowed = float(merged["wait_for_max_seconds"])
        timeout_cap = int(merged["bash_timeout_max_ms"])
        timeout = _BASH_TIMEOUT_DEFAULT_MS if timeout_ms is None else float(timeout_ms)
        max_s = _wait_for_max(cmd)
        if max_s is None or max_s > max_allowed or timeout < (max_s + 30) * 1000:
            return {
                "decision": "deny",
                "reason": _WAIT_FOR_REASON.format(
                    max_s=int(max_allowed),
                    timeout_ms=timeout_cap,
                    seen_max=max_s if max_s is not None else float("nan"),
                    seen_timeout=int(timeout),
                ),
            }
    return {"decision": "allow"}


# Phrases of a final reply that ends a turn in order to wait. Matched
# case-insensitively against the agent's last assistant text.
WAITING_PHRASES = (
    r"still running",
    r"i(?:'|\u2019)?ll wait",
    r"i will wait",
    r"wait(?:ing)? for the background",
    r"end (?:this|my) turn now",
    r"will resume when",
    r"waiting for the (?:task )?notification",
    r"wait for the (?:task )?notification",
)
_WAITING_RE = re.compile("|".join(WAITING_PHRASES), re.IGNORECASE)

# Fixed sentence of the block reason; the hook finds it in the transcript to
# know the agent was already returned once.
SLEEPING_STOP_MARKER = "Nothing resumes a subagent that ends its turn to wait."

_SLEEPING_STOP_REASON = (
    SLEEPING_STOP_MARKER + " Do not end the turn to wait. " + _SUBAGENT_PROTOCOL + " {tasks}"
    "Then give your COMPLETE final reply again: only your last message reaches the "
    "caller."
)


# Only the end of the final reply decides: a report that mentions a waiting
# phrase early (or quotes one) is not a reply that stops to wait.
WAITING_TAIL_CHARS = 600
_QUOTE_PAIRS = {'"': '"', "`": "`", "'": "'", "\u201c": "\u201d", "\u2018": "\u2019"}


def _blank_quoted(text):
    """Replace the contents of closed "...", `...`, '...' and curly-quoted spans
    with spaces, keeping the length. A single quote between two word characters
    is an apostrophe (I'll, don't), not a quote; an unclosed quote is literal."""
    chars = list(text)
    n = len(chars)
    i = 0
    while i < n:
        opener = chars[i]
        closer = _QUOTE_PAIRS.get(opener)
        if closer is None or (
            opener in ("'", "\u2018") and i > 0 and chars[i - 1].isalnum()
        ):
            i += 1
            continue
        j = i + 1
        while j < n:
            if chars[j] == closer and not (
                closer in ("'", "\u2019")
                and j + 1 < n
                and chars[j + 1].isalnum()
                and chars[j - 1].isalnum()
            ):
                break
            j += 1
        if j >= n:
            i += 1
            continue
        for k in range(i, j + 1):
            chars[k] = " "
        i = j + 1
    return "".join(chars)


def is_waiting_text(text):
    """True when the end of the final reply, outside quoted spans, says the
    agent is waiting."""
    return bool(_WAITING_RE.search(_blank_quoted(text or "")[-WAITING_TAIL_CHARS:]))


def sleeping_stop_decision(
    last_assistant_text, unresolved_background_ids, stop_hook_active, prior_block_seen
):
    """Return a subagent once when its final reply waits, or when a background
    task it launched is still unaccounted for; let the second stop through.

    Only this hook's own marker proves the agent was already returned:
    ``stop_hook_active`` is set by any SubagentStop hook's block (e.g. the
    checks gate), so it is accepted for logging but never allows the stop."""
    if prior_block_seen:
        return {"decision": "allow"}
    ids = sorted(unresolved_background_ids or ())
    if not ids and not is_waiting_text(last_assistant_text):
        return {"decision": "allow"}
    tasks = (
        "Background task(s) {} are unaccounted for: stop them (TaskStop) or wait for "
        "them with foreground wait-for and read their output. ".format(", ".join(ids))
        if ids
        else ""
    )
    return {"decision": "block", "reason": _SLEEPING_STOP_REASON.format(tasks=tasks)}


_BUDGET_DENY = (
    "Main-session hands-on budget spent for this window ({calls} calls at {ctx}k "
    "context). Combine the remaining reads into ONE call -- issue the tool calls in "
    "parallel in a single message, or run one composite shell command -- or hand the "
    "reading to an `Explore` or `prod-probe` subagent that carries a cheap context and "
    "reports back a summary. Validation commands (git status, git diff --stat, gh pr "
    "checks, scout, wait-for, usage-audit, reading handoff/report markdown) "
    "are free and never counted. If this specific call really must happen here, re-run "
    "it with `MAIN_BUDGET_OVERRIDE: <reason>` in the command."
)

_BUDGET_REMIND = (
    "Budget: you are at {calls} hands-on calls in this window at {ctx}k context -- "
    "batch the rest into one message or delegate them to a subagent."
)


def budget_decision(agent_type, ctx_tokens, window_calls, cmd_or_path):
    """Throttle hands-on main-session tool calls once the context is expensive."""
    if agent_type:
        return {"decision": "allow"}
    if "MAIN_BUDGET_OVERRIDE" in (cmd_or_path or ""):
        return {"decision": "override", "reason": "MAIN_BUDGET_OVERRIDE present"}
    if ctx_tokens <= 80000:
        return {"decision": "allow"}
    if classify_command(cmd_or_path) in (EXEMPT, WAIT_FOR):
        return {"decision": "allow"}
    ctx_k = int(round(ctx_tokens / 1000.0))
    if window_calls >= 8:
        return {
            "decision": "deny",
            "reason": _BUDGET_DENY.format(calls=window_calls, ctx=ctx_k),
        }
    if window_calls >= 4:
        return {
            "decision": "remind",
            "message": _BUDGET_REMIND.format(calls=window_calls, ctx=ctx_k),
        }
    return {"decision": "allow"}


# ---------------------------------------------------------------------------
# Agent budget, skill load, and declared-check gates (v7).
#
# Work agents are budgeted by context size and cumulative cost, not by tool
# calls: measured 2026-09-15..18 over 285 code-writing agents, a turn gets dearer only
# through context ($0.050 at turns 1-10, $0.089 at 41-60), the median
# code-writing agent makes its first edit at turn 34, and continuing a stopped agent
# costs $0.038/turn against $1.44 for a replacement. Search/probe agents, whose
# cost is the call and not the context, keep call caps.
# ---------------------------------------------------------------------------

DEFAULT_PARAMS = {
    "work_agent_types": ["builder", "reviewer"],
    "context_soft": 150000,
    "context_hard": 220000,
    "cost_ceiling_usd": {"default": 6, "builder": 25},
    # Types absent from both work_agent_types and call_caps are uncapped.
    "call_caps": {
        "Explore": {"soft": 12, "hard": 18},
        "prod-probe": {"soft": 25, "hard": 40},
    },
    # A `sleep` shorter than this paces requests; this long or longer is a poll.
    "poll_sleep_min_seconds": 5,
    # A subagent's foreground wait-for: --max at most this, and the Bash call's
    # timeout at least (--max + 30) s, never above bash_timeout_max_ms.
    "wait_for_max_seconds": 570,
    "bash_timeout_max_ms": 600000,
    "skill_bytes_threshold": 40000,
    "process_skills": [
        "orchestrate",
        "workflow-authoring",
        "artifact-design",
        "artifact-capabilities",
    ],
    "process_skill_globs": ["orchestrate-*", "process-*", "*-process", "workflow-*"],
    # Who may spawn whom, by the type of the SPAWNING agent. "*" means every
    # type the gate otherwise allows; "default" covers types not listed. A
    # {"allow": ..., "deny": {type: reason}} entry refuses the listed types
    # with their own reason.
    "spawn_rights": {
        "main": ["builder", "reviewer", "Explore", "prod-probe", "watcher"],
        "builder": ["Explore"],
        "reviewer": ["Explore"],
        "default": [],
    },
    # The model of an agent comes from its definition. Listed exceptions only,
    # keyed spawning type -> requested type -> the models that spawner may ask
    # for. The main session is never restricted.
    "model_override_rights": {},
    # Read by the orchestrate skill: fix rounds per task, independent reviewers.
    "build": {"fix_rounds_max": 2, "reviewers": 2},
    # Offline experiment (offline_decision). Programs that are a control plane
    # or a remote shell are refused whatever they are asked to do; clients that
    # are equally useful locally are refused only for a remote target, and for
    # HTTP only when the call writes.
    "offline": {
        "denied_heads": [
            "kubectl", "helm", "flux", "argocd", "ssh", "scp", "sftp",
            "terraform", "aws", "gcloud", "az", "vercel", "supabase", "doctl",
        ],
        "denied_subcommands": {
            "git": ["push"],
            "docker": ["push", "login"],
            "gh": {
                "allow_only": [
                    "pr view", "pr list", "pr checks", "pr diff",
                    "run view", "run list", "issue view", "issue list",
                    "repo view", "api",
                ]
            },
        },
        "gh_api_write_flags": ["-X", "--method", "-f", "-F"],
        "db_clients": [
            "clickhouse-client", "clickhouse", "psql", "redis-cli", "kcat",
            "mysql", "mongosh",
        ],
        "db_host_flags": ["--host", "-h", "-b", "--broker-list"],
        "http_clients": ["curl", "wget", "http", "https", "httpie"],
        "local_hosts": ["localhost", "127.0.0.1", "::1", "host.docker.internal"],
    },
}

# Agent types whose stop text may offer the checkpoint-commit path: they own a
# task branch. Every other type is told to write files only.
_COMMIT_TYPES = frozenset({"builder"})

_COMMIT_EXIT = (
    "Write your handoff to a *.md file with the Write tool, and separately run: "
    "bash ~/.claude/hooks/handoff.sh [optional-slug] to commit whatever is on disk. "
    "Do both, in either order. Nothing else runs until you do."
)
_FILE_EXIT = (
    "Write your handoff/report to a *.md file with the Write tool - that is the only "
    "thing that runs. Do not run git add, git commit or handoff.sh: this agent type "
    "does not commit. The *.md Write stays available for as many attempts as it takes."
)

_CONTEXT_REMIND = (
    "Context budget: this {agent} is at {ctx}k tokens of context (soft threshold "
    "{soft}k) and ${cost} spent. Tool calls are not limited - context is. If more than "
    "~15 turns of work remain, write the handoff now and let a fresh agent take the "
    "rest; otherwise finish the deliverable here. Read by pointer, do not re-read "
    "files you have already seen, and keep command output bounded."
)
_CONTEXT_STOP = (
    "Context budget spent for this {agent}: {ctx}k tokens (hard threshold {hard}k), "
    "${cost} spent. Everything from here is charged at the most expensive context this "
    "agent will ever have, so the phase ends here and continues in a fresh agent. "
)
_COST_STOP = (
    "Cost ceiling reached for this {agent}: ${cost} spent (ceiling ${ceiling}), {ctx}k "
    "context. The phase ends here and continues in a fresh agent. "
)
_CALLS_REMIND = (
    "Turn budget for this {agent} agent: {calls} tool calls used, soft cap {soft}, hard "
    "cap {hard}, {left} calls left before every tool but the handoff is denied. If the "
    "deliverable is reachable within the calls left, finish it now. Otherwise stop "
    "exploring and write the handoff with the exact next step."
)
_CALLS_STOP = "Hard turn cap reached for {agent} ({calls}/{hard} tool calls). "


def _merged_params(params):
    """Overlay caller-supplied params on the built-in defaults (one level deep
    for the nested maps), so a partial or missing file never loses a key."""
    merged = dict(DEFAULT_PARAMS)
    for key, value in (params or {}).items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update({k: v for k, v in value.items() if not k.startswith("_")})
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def _cost_ceiling(agent_type, params):
    ceilings = params.get("cost_ceiling_usd") or {}
    if agent_type in ceilings:
        return ceilings[agent_type]
    return ceilings.get("default")


def _exit_text(agent_type):
    return _COMMIT_EXIT if agent_type in _COMMIT_TYPES else _FILE_EXIT


def agent_budget_decision(context_tokens, cost_usd, calls, agent_type, params=None):
    """Decide whether an agent may keep working.

    Returns ``{"decision": "allow"}``, ``{"decision": "remind", "message": ...}``
    or ``{"decision": "stop", "reason": ...}``. Work types are judged on
    context size and cumulative cost; call-capped types on tool calls; any
    other type is allowed.
    """
    p = _merged_params(params)
    agent_type = agent_type_name(agent_type)
    ctx = int(context_tokens or 0)
    cost = float(cost_usd or 0.0)
    calls = int(calls or 0)
    ctx_k = int(round(ctx / 1000.0))
    cost_s = "%.2f" % cost

    if agent_type in (p.get("work_agent_types") or []):
        ceiling = _cost_ceiling(agent_type, p)
        if ceiling is not None and cost >= ceiling:
            return {
                "decision": "stop",
                "reason": _COST_STOP.format(
                    agent=agent_type, cost=cost_s, ceiling=ceiling, ctx=ctx_k
                )
                + _exit_text(agent_type),
            }
        if ctx >= p["context_hard"]:
            return {
                "decision": "stop",
                "reason": _CONTEXT_STOP.format(
                    agent=agent_type,
                    ctx=ctx_k,
                    hard=int(round(p["context_hard"] / 1000.0)),
                    cost=cost_s,
                )
                + _exit_text(agent_type),
            }
        if ctx >= p["context_soft"]:
            return {
                "decision": "remind",
                "message": _CONTEXT_REMIND.format(
                    agent=agent_type,
                    ctx=ctx_k,
                    soft=int(round(p["context_soft"] / 1000.0)),
                    cost=cost_s,
                ),
            }
        return {"decision": "allow"}

    caps = (p.get("call_caps") or {}).get(agent_type)
    if caps:
        if calls >= caps["hard"]:
            return {
                "decision": "stop",
                "reason": _CALLS_STOP.format(
                    agent=agent_type, calls=calls, hard=caps["hard"]
                )
                + _exit_text(agent_type),
            }
        if calls >= caps["soft"]:
            return {
                "decision": "remind",
                "message": _CALLS_REMIND.format(
                    agent=agent_type,
                    calls=calls,
                    soft=caps["soft"],
                    hard=caps["hard"],
                    left=caps["hard"] - calls,
                ),
            }
    return {"decision": "allow"}


_SKILL_DENY = (
    "Expert skills are loaded by the agent that does the work, not by the main "
    "session: `{skill}` is {kb}k of instructions that would sit in this context for "
    "the rest of the session. Hand the task to a `builder` agent; it chooses and loads "
    "the expert skills itself. If the main session really must read it, put "
    "`SKILL_LOAD_OVERRIDE: <reason>` in your message and request the skill again."
)


def _is_process_skill(skill_name, params):
    name = (skill_name or "").strip()
    if name in (params.get("process_skills") or []):
        return True
    return any(
        fnmatch.fnmatch(name, pat) for pat in (params.get("process_skill_globs") or [])
    )


def skill_load_decision(is_main, skill_bytes, skill_name, params=None, override_reason=None):
    """Keep large expert skills out of the main session's context.

    Denies only for the main session, only above ``skill_bytes_threshold``, and
    only for skills that are not process skills. An override reason allows and
    is reported back so the hook can log it.
    """
    p = _merged_params(params)
    if not is_main:
        return {"decision": "allow"}
    if int(skill_bytes or 0) <= p["skill_bytes_threshold"]:
        return {"decision": "allow"}
    if _is_process_skill(skill_name, p):
        return {"decision": "allow"}
    if override_reason:
        return {"decision": "allow", "override": override_reason}
    return {
        "decision": "deny",
        "reason": _SKILL_DENY.format(
            skill=skill_name, kb=int(round(int(skill_bytes) / 1000.0))
        ),
    }


_CHECK_RETURN = (
    "You edited files and are finishing without running the declared check. Run it "
    "now: {check}. If it cannot run here, say so explicitly in your report and name "
    "what you verified instead."
)


# The builder's own notes under <repo>/orch-notes/: writing them is not a change
# the check proves, and a builder stops after writing them when a design review
# must run before code.
_NOTE_FILES = frozenset({"SPEC.md", "DESIGN.md", "HANDOFF.md"})


def _is_note_edit(event):
    path = event.get("path") or ""
    return (
        os.path.basename(path) in _NOTE_FILES
        and os.path.basename(os.path.dirname(path)) == "orch-notes"
    )


def checks_decision(declared_check, events):
    """Return an agent once if its declared check never ran after its last edit.

    ``events`` is the ordered record of what the agent did: dicts with
    ``kind`` in ``edit``/``command``/``reminder``, for commands the
    ``command`` text, and for file-tool edits the ``path``. An edit of one of
    the builder's notes (``_NOTE_FILES``) is not an edit here. No declared
    check means allow, flagged ``undeclared``.
    """
    check = (declared_check or "").strip()
    if not check:
        return {"decision": "allow", "undeclared": True}

    last_edit = -1
    for i, event in enumerate(events or []):
        if event.get("kind") == "edit" and not _is_note_edit(event):
            last_edit = i
    if last_edit < 0:
        return {"decision": "allow"}

    for event in (events or [])[last_edit + 1 :]:
        kind = event.get("kind")
        if kind == "command" and check in (event.get("command") or ""):
            return {"decision": "allow"}
        if kind == "reminder":
            return {"decision": "allow"}
    return {"decision": "return", "message": _CHECK_RETURN.format(check=check)}


_READ_TOOLS = frozenset(
    {"Read", "Grep", "Glob", "LS", "NotebookRead", "WebFetch", "WebSearch"}
)
_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit", "TodoWrite"})
_CD_PREFIX = re.compile(r"^cd\s+[^\s;|&]+\s*&&\s*")
_FD_DUP = re.compile(r"\d*>&\d*")
_DEVNULL = re.compile(r">>?\s*/dev/null")
_SED_INPLACE = re.compile(r"\bsed\b[^|;&]*\s-[A-Za-z]*i\b")


def is_read_call(tool, command=None):
    """True when a tool call only observes. Writing Bash forms - a heredoc, a
    redirect into a file, ``tee``, ``sed -i`` - are writes, not reads."""
    if tool in _WRITE_TOOLS:
        return False
    if tool != "Bash":
        return tool in _READ_TOOLS
    text = (command or "").strip()
    if not text:
        return False
    stripped = _strip_quotes(_CD_PREFIX.sub("", text))
    if "<<" in stripped:
        return False
    if _SED_INPLACE.search(stripped):
        return False
    scan = _DEVNULL.sub(" ", _FD_DUP.sub(" ", stripped))
    if ">" in scan:
        return False
    for segment in _segments(stripped):
        if _program(_tokens_after_keywords(segment)) == "tee":
            return False
    return True


# ---------------------------------------------------------------------------
# Spawn rights (v7): which agent type may spawn which.
# ---------------------------------------------------------------------------

# Requesting one of these is requesting no type at all: a ~45k-token base
# prompt per turn against ~16k for a typed agent (measured 2026-09-15).
_UNTYPED = frozenset({"", "general-purpose", "claude"})

_UNTYPED_REASON = (
    "Untyped subagent refused (45k-token base per turn). Name a type from "
    "~/.claude/agents whose description matches the work. If none fits, add the line "
    "AGENT_TYPE_OVERRIDE to the prompt with the reason."
)

_SPAWN_REASON = (
    "A `{spawner}` agent may not spawn `{requested}`: {allowed}. Spawn rights are set "
    "per spawning type in ~/.claude/tools/orchestrate-params.json (`spawn_rights`) so "
    "that work stays on the agent that was given it. Do the part you were assigned and "
    "return what you cannot do as a question, or add the line AGENT_TYPE_OVERRIDE to "
    "the prompt with the reason."
)

_MAIN_SPAWNER = "main"


def spawn_decision(spawner_type, requested_type, params=None):
    """Decide whether ``spawner_type`` may spawn ``requested_type``.

    ``spawner_type`` is the agent type of the caller and is empty for the main
    session. Rights come from the ``spawn_rights`` map: a list of allowed
    types, or ``"*"`` for every type the gate otherwise allows, or a dict
    ``{"allow": <list or "*">, "deny": {type: reason}}``; types absent from
    the map fall back to its ``default`` entry.
    """
    p = _merged_params(params)
    requested = agent_type_name(requested_type)
    if requested in _UNTYPED:
        return {"decision": "deny", "reason": _UNTYPED_REASON}

    rights = p.get("spawn_rights") or {}
    spawner = agent_type_name(spawner_type) or _MAIN_SPAWNER
    allowed = rights.get(spawner)
    if allowed is None:
        allowed = rights.get("default", [])
    if isinstance(allowed, dict):
        denied = allowed.get("deny") or {}
        if requested in denied:
            return {"decision": "deny", "reason": denied[requested]}
        allowed = allowed.get("allow")
    if allowed == "*" or requested in (allowed or []):
        return {"decision": "allow"}

    listed = ", ".join("`%s`" % a for a in (allowed or []))
    phrase = ("it may spawn only " + listed) if listed else "it may not spawn agents"
    return {
        "decision": "deny",
        "reason": _SPAWN_REASON.format(
            spawner=spawner, requested=requested, allowed=phrase
        ),
    }


# ---------------------------------------------------------------------------
# Model overrides: an agent's model comes from its definition.
# ---------------------------------------------------------------------------

_MODEL_REASON = (
    "Repeat the Agent call without the `model` parameter. The model of a `{requested}` "
    "is set by its definition in ~/.claude/agents/{requested}.md; a `{spawner}` agent "
    "does not choose it, so passing `model` here only makes the work dearer than the "
    "type was designed to be. Exceptions are listed per (spawner, requested) pair in "
    "~/.claude/tools/orchestrate-params.json (`model_override_rights`)."
)


def model_override_decision(
    spawner_type, requested_type, requested_model, params=None
):
    """Decide whether a spawning agent may override the spawned agent's model.

    The main session (empty ``spawner_type``) decides models freely, and a call
    that passes no ``model`` at all takes the model from the agent definition
    and is always allowed. Any other override needs the exact
    ``(spawner, requested, model)`` triple in ``model_override_rights``.
    """
    model = (requested_model or "").strip()
    if not model:
        return {"decision": "allow"}
    spawner = agent_type_name(spawner_type)
    if not spawner:
        return {"decision": "allow"}

    p = _merged_params(params)
    requested = agent_type_name(requested_type)
    allowed = ((p.get("model_override_rights") or {}).get(spawner) or {}).get(
        requested
    ) or []
    if model.lower() in [str(a).strip().lower() for a in allowed]:
        return {"decision": "allow"}
    return {
        "decision": "deny",
        "reason": _MODEL_REASON.format(spawner=spawner, requested=requested),
    }


# ---------------------------------------------------------------------------
# Offline experiment: a session may be declared offline, and then no command of
# that session may write beyond this machine or touch a deployment control
# plane at all.
# ---------------------------------------------------------------------------

_OFFLINE_REASON = (
    "offline experiment session: this command can reach production or a remote "
    "control plane; run the local equivalent or report the item as not provable "
    "offline."
)

# Flags that make an HTTP call a write rather than a read.
_HTTP_BODY_FLAGS = frozenset(
    {"-d", "-F", "--form", "-T", "--upload-file", "--post-file", "--post-data",
     "--body-data", "--body-file"}
)
_HTTP_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_HTTP_METHOD_FLAGS = ("-X", "--request", "--method")
_SUDO = frozenset({"sudo", "doas"})
_INNER_SHELLS = frozenset({"bash", "sh", "zsh", "ksh"})


def _offline_tokens(segment):
    """Tokens of one segment with wrappers (`rtk`, `rtk proxy`, `env`),
    environment assignments and `sudo` removed, so ``tokens[0]`` is the program
    that would actually run."""
    tokens = _tokens_after_keywords(segment)
    for _ in range(4):
        if not tokens:
            return tokens
        first = tokens[0]
        if _ENV_ASSIGN.match(first) or os.path.basename(first) in _SUDO:
            tokens = tokens[1:]
            if os.path.basename(first) in _SUDO:
                while tokens and tokens[0].startswith("-"):
                    tokens = tokens[1:]
            tokens = _unwrap(tokens)
            continue
        return tokens
    return tokens


def _positional(args):
    """Arguments that are neither flags nor the value of a ``--flag value``."""
    return [a for a in args if not a.startswith("-")]


def _host_of(text):
    """Host part of a URL, ``user@host:port`` or bare ``host:port``."""
    value = text.strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    value = value.split("/", 1)[0].split("?", 1)[0]
    if "@" in value:
        value = value.rsplit("@", 1)[1]
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    if value.count(":") == 1:
        value = value.split(":", 1)[0]
    return value


def _is_local_host(host, local_hosts):
    host = (host or "").strip().lower()
    if not host:
        return True
    if host in local_hosts:
        return True
    # An unqualified name cannot be a public endpoint: a k8s service, a compose
    # service or a /etc/hosts alias all resolve inside this machine's world.
    return "." not in host and ":" not in host


def _flag_values(args, flags):
    """Values given to ``flags`` in either ``-h value`` or ``--host=value`` form."""
    out = []
    i = 0
    while i < len(args):
        arg = args[i]
        name, _, inline = arg.partition("=")
        if name in flags:
            if inline:
                out.append(inline)
            elif i + 1 < len(args):
                out.append(args[i + 1])
                i += 1
        i += 1
    return out


def _remote_endpoint(args, host_flags, local_hosts):
    """True when an argument names a host that is not on this machine."""
    candidates = list(_flag_values(args, host_flags))
    candidates += [a for a in args if "://" in a]
    for candidate in candidates:
        if not _is_local_host(_host_of(candidate), local_hosts):
            return True
    return False


def _http_is_write(args):
    for i, arg in enumerate(args):
        name, _, inline = arg.partition("=")
        if name in _HTTP_METHOD_FLAGS:
            value = inline or (args[i + 1] if i + 1 < len(args) else "")
            if value.strip().upper() in _HTTP_WRITE_METHODS:
                return True
            continue
        if arg.startswith("-X") and len(arg) > 2:
            if arg[2:].strip().upper() in _HTTP_WRITE_METHODS:
                return True
            continue
        if name in _HTTP_BODY_FLAGS or name.startswith("--data"):
            return True
        # httpie takes the method as a positional word.
        if not arg.startswith("-") and arg.upper() in _HTTP_WRITE_METHODS:
            return True
    return False


def _http_remote(args, local_hosts):
    for arg in args:
        if arg.startswith("-"):
            continue
        if "://" not in arg and "." not in arg and ":" not in arg:
            continue
        host = _host_of(arg)
        if host and not _is_local_host(host, local_hosts):
            return True
    return False


def _gh_denied(args, allowed, write_flags):
    positional = _positional(args)
    if not positional:
        return False
    if positional[0] == "api":
        return any(a.partition("=")[0] in write_flags for a in args)
    return " ".join(positional[:2]) not in allowed


def _offline_segment_denied(tokens, off):
    prog = _program(tokens)
    if not prog:
        return False
    args = tokens[1:]
    local_hosts = frozenset(
        str(h).strip().lower() for h in (off.get("local_hosts") or [])
    )

    if prog in set(off.get("denied_heads") or []):
        return True

    rule = (off.get("denied_subcommands") or {}).get(prog)
    if rule is not None:
        if prog == "gh":
            allowed = set((rule or {}).get("allow_only") or [])
            if _gh_denied(args, allowed, set(off.get("gh_api_write_flags") or [])):
                return True
        else:
            positional = _positional(args)
            if positional and positional[0] in set(rule or []):
                return True

    if prog in set(off.get("db_clients") or []):
        if _remote_endpoint(args, set(off.get("db_host_flags") or []), local_hosts):
            return True

    if prog in set(off.get("http_clients") or []):
        if _http_is_write(args) and _http_remote(args, local_hosts):
            return True

    return False


def _inner_shell_commands(command):
    """Command strings passed to ``bash -c '...'`` and friends, so that a gate
    on command heads is not defeated by one level of quoting."""
    import shlex

    try:
        tokens = shlex.split(command)
    except ValueError:
        return []
    out = []
    for i, token in enumerate(tokens):
        if os.path.basename(token) not in _INNER_SHELLS:
            continue
        for j in range(i + 1, len(tokens)):
            if tokens[j] in ("-c", "-lc", "-cl"):
                if j + 1 < len(tokens):
                    out.append(tokens[j + 1])
                break
            if not tokens[j].startswith("-"):
                break
    return out


def offline_decision(command, params=None, _depth=0):
    """Refuse a Bash command that can write beyond this machine or reach a
    deployment control plane at all.

    The property, not a repository's list: control-plane and remote-shell
    programs are refused outright; a version-control/registry/API client is
    refused for its writing subcommands; a database, broker or HTTP client is
    refused only when it is pointed at a host that is not this machine, and for
    HTTP only when the call carries a method or body that writes. Everything
    else - including any purely local command - is allowed.

    ``params`` supplies the parameters under its ``offline`` key; anything not
    given falls back to ``DEFAULT_PARAMS``.
    """
    text = (command or "").strip()
    if not text:
        return {"decision": "allow"}

    off = _merged_params(params).get("offline") or {}
    stripped = _strip_quotes(text)
    for segment in _segments(stripped):
        tokens = _offline_tokens(segment)
        if _offline_segment_denied(tokens, off):
            return {
                "decision": "deny",
                "reason": _OFFLINE_REASON,
                "head": _program(tokens),
            }

    if _depth == 0:
        for inner in _inner_shell_commands(text):
            nested = offline_decision(inner, params, _depth + 1)
            if nested["decision"] == "deny":
                return nested

    return {"decision": "allow"}


# --- Bash output clamp -------------------------------------------------------

_CLAMP_BYPASS = re.compile(r"(^|[\s/;&|(])(handoff\.sh|wait-for)(?=$|[\s;&|)])")
_ALREADY_CLAMPED = re.compile(r"\bclamp\.py\b")


def bash_clamp_decision(agent_type, command, params, out_path, tools_dir):
    """None to leave the command alone, else the command wrapped so its output
    lands in ``out_path`` and only a clamped view is printed. A ``{ }`` group
    keeps the persistent shell's state; the exit status is returned last."""
    cfg = (params or {}).get("bash_clamp") or {}
    if agent_type_name(agent_type) not in (cfg.get("types") or []):
        return None
    if _CLAMP_BYPASS.search(command) or _ALREADY_CLAMPED.search(command):
        return None
    out = shlex.quote(out_path)
    clamp = shlex.quote(os.path.join(tools_dir, "clamp.py"))
    return (
        "{ %s\n} > %s 2>&1; __orch_rc=$?; python3 %s %s --head %d --tail %d --max-bytes %d; ( exit $__orch_rc )"
        % (command, out, clamp, out, cfg["head_lines"], cfg["tail_lines"], cfg["max_bytes"])
    )


_OWN_TRANSCRIPT_RE = re.compile(r"/subagents/agent-[^/]+\.jsonl$")


def own_transcript_path(transcript, agent_id):
    """A subagent hook gets either its own transcript, or the parent's plus an
    ``agent_id``. Return the subagent's own transcript path, or "" when neither
    shape applies (the parent session itself)."""
    if not transcript:
        return ""
    if _OWN_TRANSCRIPT_RE.search(transcript):
        return transcript
    if not agent_id:
        return ""
    base = transcript[: -len(".jsonl")] if transcript.endswith(".jsonl") else transcript
    return os.path.join(base, "subagents", "agent-%s.jsonl" % agent_id)


# ---------------------------------------------------------------------------
# Retro 2026-10-12, round 2: four decisions at the point where a loaded rule
# was not applied. Evidence: ~/.claude/retro/findings/ (833 mistakes, 25
# sessions); the class table is in ~/.claude/retro/CLASSES.md.
# ---------------------------------------------------------------------------

# -- merge gate: a change reaches a shared path only with a real-path check --

CHECK_RAN_TOKEN = "CHECK_RAN:"
_MERGE_RE = re.compile(
    r"\bgh\s+pr\s+merge\b"
    r"|\bgit\s+push\b[^|;&\n]*\b(?:master|main)\b"
)
_MERGE_REASON = (
    "Refused by merge-gate: this command lands a change on a shared branch. Run "
    "the declared check on the real path first (service start plus one unit "
    "through; on a numeric path the target before and after), then repeat the "
    "command with `CHECK_RAN: <what ran, result>` in it. Unit tests or a review "
    "alone do not count (retro 2026-10-12: 36 merges without a real-path check "
    "cost 57 h and one irreversible mass send)."
)


def merge_gate_decision(command):
    """Deny ``gh pr merge`` and pushes to master/main unless the command carries
    ``CHECK_RAN:`` with the real-path check that ran."""
    cmd = command or ""
    if CHECK_RAN_TOKEN in cmd or not _MERGE_RE.search(cmd):
        return {"decision": "allow"}
    return {"decision": "deny", "reason": _MERGE_REASON}


# -- reply lint: a final reply carries its observation and stays in authority --

_DONE_RE = re.compile(
    r"(?<![\w-])(?:готов[оаы]?|сделан[оаы]?|исправлен[оаы]?|починен[оаы]?|починил[аи]?"
    r"|работает|закрыт[оаы]?|влит[оаы]?|done|fixed|resolved|works)(?![\w-])",
    re.IGNORECASE,
)
_NEGATED_DONE_RE = re.compile(
    r"(?:\bне\b|\bещё не\b|\beще не\b|\bnot\b|\bisn'?t\b|\bno longer\b)\s+\S*\s*$",
    re.IGNORECASE,
)
_OBSERVATION_RE = re.compile(
    r"```"
    r"|measured|observed|assumed|замер|наблюда|измер|не провер|not verified|unverified"
    r"|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}(?::\d{2})?Z?\b"
    r"|\b\d+(?:[.,]\d+)?\s?(?:ms|мс|s|с|sec|сек|min|мин|h|ч|%|/s|/с|[KMG]i?B|[КМГ]Б|rows|строк|LOC|cores?)\b",
    re.IGNORECASE,
)
_OWNER_QUESTION_RE = re.compile(
    r"решени[яе]\s+за\s+(?:вами|тобой)|если\s+скаж|жду\s+(?:вашего|твоего)"
    r"|по\s+вашему\s+слову|if you say|your call|open the PR yourself|PR\s+открывать\s+тебе",
    re.IGNORECASE,
)
_OWNER_DOMAIN_RE = re.compile(
    r"необратим|деньг|приорит|irreversible|money|priorit|client data|данн\w+ клиент",
    re.IGNORECASE,
)
REPLY_LINT_MARKER = "reply-lint:"
_CLAIM_REASON = (
    REPLY_LINT_MARKER + " the reply claims completion without its observation. Attach "
    "the observation that proves it (command or query, value, time) labelled "
    "measured / observed / assumed, on one sampled real artifact of the original "
    "scenario; or write 'not verified'. A number from the owner, a client, memory or "
    "a delegate is a claim until reproduced from the primary source. Then give the "
    "complete final reply again."
)
_AUTHORITY_REASON = (
    REPLY_LINT_MARKER + " the reply ends on a question or hands a decision to the owner. "
    "Authority test: reversible, bounded and ours -> do it now; a tunable value -> set "
    "a default and report 'set to X - keep or change?'; only money, business priorities "
    "and irreversible effects on client data go to the owner, as one plain question with "
    "a recommendation. Do the work, then give the complete final reply again."
)


def _has_done_claim(text):
    for match in _DONE_RE.finditer(text):
        if not _NEGATED_DONE_RE.search(text[: match.start()][-24:]):
            return True
    return False


def reply_lint_decision(last_assistant_text, stop_hook_active):
    """Return the main session once when its final reply claims completion
    without an observation, or ends on a question the agent could decide.
    The second stop (``stop_hook_active``) always passes."""
    if stop_hook_active:
        return {"decision": "allow"}
    text = (last_assistant_text or "").strip()
    if not text:
        return {"decision": "allow"}
    reasons = []
    if _has_done_claim(text) and not _OBSERVATION_RE.search(text):
        reasons.append(_CLAIM_REASON)
    tail = text.rstrip("*_` \n")
    asks_owner = tail.endswith("?") or _OWNER_QUESTION_RE.search(_blank_quoted(text))
    if asks_owner and not _OWNER_DOMAIN_RE.search(text):
        reasons.append(_AUTHORITY_REASON)
    if not reasons:
        return {"decision": "allow"}
    return {"decision": "block", "reason": " ".join(reasons)}


# -- brief gate: a delegate's budget goes to the deliverable, not to discovery --

_BRIEF_LINES = {
    "builder": ("files:", "check:"),
    "prod-probe": ("access:",),
}
_BRIEF_REASON = (
    "Refused by brief-gate: a `{requested}` prompt needs the line(s) {missing} "
    "(each at the start of a line). `files:` = file:line pointers from one "
    "`Explore` run; `check:` = the run on the real path that proves the work; "
    "`access:` = the route to every system the probe reads (infra-access). A "
    "delegate that must discover these burns its context before the deliverable "
    "(retro 2026-10-12: 30+ builders and 4 probes died that way). Add the lines, "
    "or `AGENT_TYPE_OVERRIDE` with the reason."
)


def brief_decision(requested_type, prompt):
    """Deny a builder spawn without ``files:`` and ``check:`` lines, and a
    prod-probe spawn without an ``access:`` line."""
    requested = agent_type_name(requested_type)
    needed = _BRIEF_LINES.get(requested)
    if not needed:
        return {"decision": "allow"}
    present = {m.group(1).lower() for m in re.finditer(r"^\s*([a-z]+:)", prompt or "", re.M)}
    missing = [line for line in needed if line not in present]
    if not missing:
        return {"decision": "allow"}
    return {
        "decision": "deny",
        "reason": _BRIEF_REASON.format(
            requested=requested, missing=", ".join("`%s`" % m for m in missing)
        ),
    }


# -- secrets: masked at the Bash output boundary, never in a transcript --

_SECRET_NAME = r"(?:password|passwd|secret|token|api[_-]?key|apikey|authorization|private[_-]?key|credential)"
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)([\w.-]*" + _SECRET_NAME + r"[\w.-]*\s*[:=]\s*[\"']?)([^\s\"',;&<>]{6,})"
)
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9._+/=-]{8,})")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")
_K8S_SECRET_RE = re.compile(r"(?m)^(\s+[\w.-]+:\s+)([A-Za-z0-9+/=]{16,})$")
_K8S_SECRET_JSON_RE = re.compile(r"(\"[\w.-]+\":\s*\")([A-Za-z0-9+/=]{16,})(\")")


def _mask(value):
    import hashlib

    return "<redacted sha256:%s>" % hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def redact_secrets(text):
    """Return ``text`` with secret-looking values replaced by a masked digest.
    Pure numbers and values shorter than six characters are left alone."""
    if not text:
        return text
    out = _PEM_RE.sub(lambda m: _mask(m.group(0)), text)
    out = _JWT_RE.sub(lambda m: _mask(m.group(0)), out)
    out = _BEARER_RE.sub(lambda m: m.group(1) + _mask(m.group(2)), out)
    out = _SECRET_ASSIGN_RE.sub(
        lambda m: m.group(0)
        if m.group(2).isdigit() or m.group(2).lower() == "bearer" or m.group(2).startswith("<redacted")
        else m.group(1) + _mask(m.group(2)),
        out,
    )
    if re.search(r"kind:\s*Secret\b|\"kind\":\s*\"Secret\"", out):
        out = _K8S_SECRET_RE.sub(lambda m: m.group(1) + _mask(m.group(2)), out)
        out = _K8S_SECRET_JSON_RE.sub(
            lambda m: m.group(1) + _mask(m.group(2)) + m.group(3), out
        )
    return out
