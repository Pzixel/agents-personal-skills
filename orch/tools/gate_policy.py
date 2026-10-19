"""Pure decision policy for the no-poll and main-budget PreToolUse gates.

Convention: ``cmd_or_path`` is the Bash ``command`` for Bash calls, and the bare
target path (``file_path``/``path``/``pattern``) for Read/Grep/Glob calls; a
bare path is recognised by having no whitespace and starting with ``/``, ``~``
or ``./``.

No I/O of any kind lives here: every function takes facts as values and returns
a complete decision dict.
"""

import fnmatch
import json
import os
import re
import shlex
import tempfile

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

# Delivery commands the orchestrate skill keeps in the main session: program ->
# argument prefixes that are free there. An empty prefix matches any arguments.
# Waiting and polling (gh pr checks --watch, gh run watch) are not delivery.
_DELIVERY_COMMANDS = {
    "gh": (
        ("pr", "create"), ("pr", "merge"), ("pr", "view"), ("pr", "edit"), ("pr", "close"),
    ),
    "git": (
        ("worktree", "add"), ("worktree", "remove"), ("worktree", "prune"),
        ("worktree", "list"), ("branch", "-d"), ("branch", "-D"),
        ("push",), ("fetch",), ("pull",),
    ),
}

# Tools that write files by hand: always a hands-on call, the file is the subject.
_EDIT_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})

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


# git global options that take a separate value (`git -C <dir> push`).
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})


def _subcommand_args(prog, args):
    """Arguments from the subcommand on, past git's global options."""
    if prog != "git":
        return args
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in _GIT_VALUE_OPTIONS else 1
    return args[i:]


def _segment_class(tokens):
    prog = _program(tokens)
    args = tokens[1:]
    if prog in _EXEMPT_TOOLS:
        return EXEMPT
    sub = _subcommand_args(prog, args)
    if any(tuple(sub[: len(pre)]) == pre for pre in _DELIVERY_COMMANDS.get(prog, ())):
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
        if args[:2] == ["pr", "checks"] and "--watch" not in args:
            return EXEMPT
        if args[:2] == ["run", "view"]:
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
    "and a killed task reports nothing). Escape hatch: a line "
    "`NO_POLL_OVERRIDE: <reason>` in the command (a reason is required); it overrides the polling heuristic "
    "only, and every use is logged."
)

_SUBAGENT_BACKGROUND_REASON = (
    "run_in_background is refused for subagents: the harness kills background Bash "
    "tasks with exit 144 at its own events, and a subagent that ends its turn to wait "
    "for the notification is never resumed. " + _SUBAGENT_PROTOCOL + " Escape hatch: "
    "a line `NO_POLL_OVERRIDE: <reason>` in the command (a reason is required); it is logged, "
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

    ``timeout_ms`` is the Bash call's timeout; None means the harness default.
    A refusal becomes {"decision": "override", "reason": ...} when the command
    carries a NO_POLL_OVERRIDE marker (override_marker)."""
    decision = _poll_rule(cmd, run_in_background, agent_type, params, timeout_ms)
    reason = override_marker(cmd, "NO_POLL_OVERRIDE", params)
    if decision["decision"] == "deny" and reason:
        return {"decision": "override", "reason": reason}
    return decision


def _poll_rule(cmd, run_in_background, agent_type, params, timeout_ms):
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
_QUOTE_PAIRS = {'"': '"', "`": "`", "'": "'", "\u201c": "\u201d", "\u2018": "\u2019",
    "\u00ab": "\u00bb", "\u201e": "\u201c",
}


def _blank_quoted(text):
    """Replace the contents of closed "...", `...`, '...', curly, «...» and „...“ spans
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
    "reports back a summary. Validation and delivery commands (git status, git diff "
    "--stat, gh pr checks without --watch, gh pr create/merge/view, git push/fetch/pull, "
    "git worktree, scout, wait-for, usage-audit, reading handoff/report markdown) "
    "are free and never counted. If this specific call really must happen here, re-run "
    "it with `MAIN_BUDGET_OVERRIDE: <reason>` in the command."
)

_BUDGET_REMIND = (
    "Budget: you are at {calls} hands-on calls in this window at {ctx}k context -- "
    "batch the rest into one message or delegate them to a subagent."
)


def tool_call_class(tool_name, cmd_or_path, params=None):
    """Budget class of one main-session tool call: a hand edit is always
    hands-on; every other tool is classified by its command or path."""
    if tool_name in _EDIT_TOOLS:
        return HANDS_ON
    return classify_command(cmd_or_path, params)


def override_marker(text, name, params=None):
    """Return the reason of an override marker ``name`` in ``text``, else None.

    A marker is a line that, after whitespace and an optional leading ``#``,
    is ``NAME: <reason>`` (the rest of the line) or ``NAME="<reason>"`` (the
    quoted part; the ``=`` form requires quotes). The reason, stripped of
    whitespace and quotes, must be at least ``override_reason_min_chars``
    long. A marker elsewhere in a line (a grep argument, a string) is not an
    override."""
    min_chars = int(_merged_params(params)["override_reason_min_chars"])
    pattern = re.compile(
        r"^#?\s*" + re.escape(name) + r"\s*(?::(.*)|=\s*(\"[^\"]*\"|'[^']*'))"
    )
    for line in (text or "").splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        reason = (match.group(1) or match.group(2) or "").strip().strip("\"'").strip()
        if len(reason) >= min_chars:
            return reason[:200]
    return None


def budget_decision(
    agent_type, ctx_tokens, window_calls, cmd_or_path,
    tool_name="Bash", loaded_skills=(), params=None,
):
    """Throttle hands-on main-session tool calls once the context is expensive.

    ``loaded_skills`` are the skills this session loaded; a session running a
    ``main_only_skills`` skill works in main by design and is never throttled.
    """
    if agent_type:
        return {"decision": "allow"}
    reason = override_marker(cmd_or_path, "MAIN_BUDGET_OVERRIDE", params)
    if reason:
        return {"decision": "override", "reason": reason}
    p = _merged_params(params)
    main_only = [s for s in loaded_skills or () if s in p["main_only_skills"]]
    if main_only:
        return {"decision": "allow", "reason": "main-only skill %s loaded" % main_only[0]}
    if ctx_tokens <= 80000:
        return {"decision": "allow"}
    if tool_call_class(tool_name, cmd_or_path, p) in (EXEMPT, WAIT_FOR):
        return {"decision": "allow"}
    ctx_k = int(round(ctx_tokens / 1000.0))
    if window_calls >= 16:
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
    "skill_bytes_threshold": 12000,
    # An override marker needs a reason of at least this many characters.
    "override_reason_min_chars": 8,
    "process_skills": [
        "orchestrate",
        "workflow-authoring",
        "artifact-design",
        "artifact-capabilities",
        "dialogue-to-knowledge",
    ],
    # Skills whose whole procedure runs in the main session by design; the
    # budget gate does not count calls in a session that loaded one.
    "main_only_skills": ["dialogue-to-knowledge", "retro"],
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
    "destructive": {
        "ours_paths": ["/tmp/", "tmp/", ".worktrees/", "~/.claude/tmp/", "~/Library/Caches/"],
        "ours_labels": ["orch.session"],
        "sql_clients": ["psql", "mysql", "sqlite3", "clickhouse-client", "clickhouse", "mongosh"],
        "plan_max_lines": 40,
        "plan_max_bytes": 4000,
        "command_max_bytes": 65536,
        "ask_in_bypass": False,
        "approvals_dir": "~/.claude/approvals",
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


def load_params():
    """orchestrate-params.json next to this module, or the built-in defaults
    when it is missing or unreadable."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "orchestrate-params.json")) as fh:
            return json.load(fh)
    except Exception:
        return dict(DEFAULT_PARAMS)


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
    "session: `{skill}` puts {on_load_bytes} bytes of instructions into this context on "
    "load (the threshold is {threshold} bytes), and its documents add up to {docs_kb}k if read in full, for the rest of the "
    "session. Hand the task to a `builder` or `prod-probe` agent; it loads the skill "
    "itself. If the main session really must read it, put "
    "`SKILL_LOAD_OVERRIDE: <reason>` in your message and request the skill again."
)

_SKILL_DOCS_CONTEXT = (
    "Skill `{skill}` loaded: {on_load_kb}k on load; its documents add up to "
    "{docs_kb}k if read in full."
)
_SKILL_DOCS_ADVICE = " Read one reference section at a time; delegate work that needs several."


def _is_process_skill(skill_name, params):
    name = (skill_name or "").strip()
    if name in (params.get("process_skills") or []):
        return True
    return any(
        fnmatch.fnmatch(name, pat) for pat in (params.get("process_skill_globs") or [])
    )


def skill_load_decision(
    is_main, skill_bytes, skill_name, params=None, override_reason=None, docs_bytes=0
):
    """Keep large expert skills out of the main session's context.

    ``skill_bytes`` is what the harness injects on load (SKILL.md);
    ``docs_bytes`` is every document the skill can send the agent to read.
    Denies only for the main session, only when the on-load size exceeds
    ``skill_bytes_threshold``, and only for skills that are not process skills.
    An override reason allows and is reported back so the hook can log it. An
    allowed main-session load whose documents exceed the threshold carries a
    ``context`` line with both sizes, plus reading advice unless the skill is a
    process or main-only skill, which run in the main session by design.
    """
    p = _merged_params(params)
    if not is_main:
        return {"decision": "allow"}
    sizes = {
        "skill": skill_name,
        "on_load_bytes": int(skill_bytes or 0),
        "threshold": p["skill_bytes_threshold"],
        "on_load_kb": int(round(int(skill_bytes or 0) / 1000.0)),
        "docs_kb": int(round(int(docs_bytes or 0) / 1000.0)),
    }
    threshold = p["skill_bytes_threshold"]
    decision = {"decision": "allow"}
    works_in_main = _is_process_skill(skill_name, p) or skill_name in p["main_only_skills"]
    if int(skill_bytes or 0) > threshold and not works_in_main:
        if not override_reason:
            return {"decision": "deny", "reason": _SKILL_DENY.format(**sizes)}
        decision["override"] = override_reason
    if int(docs_bytes or 0) > threshold:
        decision["context"] = _SKILL_DOCS_CONTEXT.format(**sizes) + (
            "" if works_in_main else _SKILL_DOCS_ADVICE
        )
    return decision


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
    "~/.claude/agents whose description matches the work. If none fits, the main session "
    "adds a line `AGENT_TYPE_OVERRIDE: <reason>` to the prompt (a reason is required)."
)

_SPAWN_REASON = (
    "A `{spawner}` agent may not spawn `{requested}`: {allowed}. Spawn rights are set "
    "per spawning type in ~/.claude/tools/orchestrate-params.json (`spawn_rights`) so "
    "that work stays on the agent that was given it. Do the part you were assigned and "
    "return what you cannot do as a question; only the main session may add a line "
    "`AGENT_TYPE_OVERRIDE: <reason>` to the prompt."
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


_SUBAGENT_OVERRIDE_REASON = (
    "AGENT_TYPE_OVERRIDE is honoured only in the main session: the main session "
    "spawns reviewers; return the request to main."
)


def spawn_gate_decision(spawner_type, requested_type, model, prompt, params=None):
    """The Agent-tool gate: spawn rights, model choice and brief, in that order.

    A prompt line ``AGENT_TYPE_OVERRIDE: <reason>`` turns a refusal into
    ``{"decision": "override", "reason": ...}`` for the main session only; a
    subagent carrying it is refused. A spawn the gate already allows stays a
    plain allow, so override records count only real bypasses."""
    reason = override_marker(prompt, "AGENT_TYPE_OVERRIDE", params)
    if reason and agent_type_name(spawner_type):
        return {"decision": "deny", "reason": _SUBAGENT_OVERRIDE_REASON}
    decision = spawn_decision(spawner_type, requested_type, params)
    if decision["decision"] != "deny":
        decision = model_override_decision(spawner_type, requested_type, model, params)
    if decision["decision"] != "deny":
        decision = brief_decision(requested_type, prompt)
    if decision["decision"] == "deny" and reason:
        return {"decision": "override", "reason": reason}
    return decision


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
PROD_POSITION_TOKEN = "PROD_POSITION:"
WAY_BACK_TOKEN = "WAY_BACK:"
_MERGE_RE = re.compile(
    r"\bgh\s+pr\s+merge\b"
    # git global options may stand between `git` and `push` (`git -C <dir> push`).
    r"|\bgit(?:\s+(?:-C|-c|--git-dir|--work-tree|--namespace)(?:=|\s+)\S+|\s+-\S+)*"
    r"\s+push\b[^|;&\n]*\b(?:master|main)\b"
)
_MERGE_REASON = (
    "Refused by merge-gate: this command lands a change on a shared branch. Run "
    "the declared check on the real path first (service start plus one unit "
    "through; on a numeric path the target before and after), then repeat the "
    "command with `CHECK_RAN: <what ran, result>` in it. Unit tests or a review "
    "alone do not count (retro 2026-10-12: 36 merges without a real-path check "
    "cost 57 h and one irreversible mass send)."
)
_MERGE_FIELD_DOCS = (
    (CHECK_RAN_TOKEN, "`CHECK_RAN: <what ran, result>`"),
    (PROD_POSITION_TOKEN, "`PROD_POSITION: <dimension along which blast radius grows, production's number on it, source>`"),
    (WAY_BACK_TOKEN, "`WAY_BACK: <means of undo, minutes>`"),
)


# Rules files loaded into every session; growing them costs every session.
ALWAYS_ON_PATHS = frozenset(("rules/always-on.md", "rules/always-on-claude.md"))
_ALWAYS_ON_RE = re.compile(r"ALWAYS_ON:\s*(\d+)\s*->\s*(\d+)\s*chars")
_REMOVED_RE = re.compile(r"\bremoved:\s*(?:'([^']*)'|\"([^\"]*)\"|([^;\n'\"]*))")
_GROW_REASON_RE = re.compile(r"\breason:\s*[^\s;'\"]")
_ALWAYS_ON_LEAD = (
    "Refused by merge-gate: this push changes an always-on rules file, whose "
    "size every session pays for."
)
_ALWAYS_ON_DOC = (
    "`ALWAYS_ON: <before> -> <after> chars` (wc -c of the file) and, when it "
    "grows, `removed: <text cut elsewhere>` or `reason: <why the always-on "
    "layer must grow>` (retro CASE-19: an on-demand placement of the measured "
    "budget rule was read by no fresh agent, live 0/3, against 0/3 -> 3/3 when "
    "loaded)"
)


def _always_on_satisfied(cmd, changed_paths):
    """True unless ``changed_paths`` touch an always-on rules file and the
    command lacks the size change, or what was removed / why it grows.
    ``changed_paths`` None means unknown: no requirement."""
    if not changed_paths or not ALWAYS_ON_PATHS.intersection(changed_paths):
        return True
    size = _ALWAYS_ON_RE.search(cmd)
    if not size:
        return False
    if int(size.group(2)) <= int(size.group(1)) or _GROW_REASON_RE.search(cmd):
        return True
    removed = _REMOVED_RE.search(cmd)
    text = removed and "".join(g or "" for g in removed.groups()).strip()
    return bool(text) and text.lower() != "none"


def merge_gate_decision(command, changed_paths=None):
    """Deny ``gh pr merge`` and pushes to master/main unless the command carries
    ``CHECK_RAN:``, ``PROD_POSITION:`` and ``WAY_BACK:``, and ``ALWAYS_ON:``
    when ``changed_paths`` touch an always-on rules file."""
    cmd = command or ""
    if not _MERGE_RE.search(cmd):
        return {"decision": "allow"}
    missing = [doc for token, doc in _MERGE_FIELD_DOCS if token not in cmd]
    lead = _MERGE_REASON if missing else _ALWAYS_ON_LEAD
    if not _always_on_satisfied(cmd, changed_paths):
        missing.append(_ALWAYS_ON_DOC)
    if not missing:
        return {"decision": "allow"}
    return {"decision": "deny", "reason": lead + " Missing: " + "; ".join(missing) + "."}


# git push options that take a separate value.
_PUSH_VALUE_OPTIONS = frozenset({"--repo", "-o", "--push-option", "--receive-pack", "--exec"})


def push_range(command, cwd):
    """``{"dir", "remote", "src", "target"}`` of the first ``git push`` in
    ``command`` that updates master or main, or None when the repository
    directory, remote or refspec is not literal. ``cwd`` is the directory the
    command starts in; a literal ``cd`` and ``git -C`` move it."""
    directory = cwd or None
    for tokens in _shell_segments(command or ""):
        tokens = _tokens_after_keywords(" ".join(tokens))
        prog = _program(tokens)
        if prog == "cd":
            directory = _cd_target(prog, tokens[1:], {"dir": directory}) if directory else None
            continue
        if prog != "git":
            continue
        args, repo, i = tokens[1:], directory, 0
        while i < len(args) and args[i].startswith("-"):
            if args[i] == "-C" and i + 1 < len(args):
                repo = _resolve(args[i + 1], {"dir": repo}) if repo or args[i + 1].startswith("/") else None
            i += 2 if args[i] in _GIT_VALUE_OPTIONS else 1
        if i >= len(args) or args[i] != "push":
            continue
        operands, rest = [], args[i + 1:]
        j = 0
        while j < len(rest):
            if rest[j].startswith("-"):
                j += 2 if rest[j] in _PUSH_VALUE_OPTIONS else 1
                continue
            operands.append(rest[j])
            j += 1
        if len(operands) < 2 or not repo:
            continue
        for spec in operands[1:]:
            src, _, dst = spec.lstrip("+").rpartition(":")
            dst = dst[len("refs/heads/"):] if dst.startswith("refs/heads/") else dst
            if dst in ("master", "main"):
                return {"dir": repo, "remote": operands[0], "src": src or "HEAD", "target": dst}
    return None


# -- reply lint: a final reply carries its observation and stays in authority --

_DONE_RE = re.compile(
    r"(?<![\w-])(?:готов[оаы]?|сделан[оаы]?|исправлен[оаы]?|починен[оаы]?|починил[аи]?"
    r"|закрыт[оаы]?|влит[оаы]?|done|fixed|resolved)(?![\w-])",
    re.IGNORECASE,
)
_NEGATED_DONE_RE = re.compile(
    r"(?:\bне\b|\bещё не\b|\beще не\b|\bnot\b|\bisn'?t\b|\bno longer\b)\s+\S*\s*$",
    re.IGNORECASE,
)
_OBSERVATION_RE = re.compile(
    r"```"
    r"|measured|observed|assumed|замер|наблюда|измер|не\s+(?:пере)?провер|не\s+повторял|не\s+воспроизводил[аи]?\b|по\s+словам|со\s+слов\b|not verified|unverified"
    r"|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}(?::\d{2})?Z?\b"
    r"|\b\d+(?:[.,]\d+)?\s?(?:ms|мс|s|с|sec|сек|min|мин|h|ч|%|/s|/с|[KMG]i?B|[КМГ]Б|rows|строк|LOC|cores?)\b"
    r"|(?<![\w-])(?=[0-9a-f]*\d)[0-9a-f]{7,40}(?![\w-])|(?<![\w-])[A-Za-z][\w-]*\s+#\d+\b"
    r"|\bexit\s+\d+|\b\d+\s*(?:/|из|of)\s*\d+\s+(?:тест\w*|tests?)\b"
    r"|\b\d+\s+(?:тест\w*|tests?)\s+(?:прош\w*|pass\w*)"
    r"|\b\d+/\d+\s*(?:→|->)\s*\d+/\d+",
    re.IGNORECASE,
)
# A first-person commitment to do work later: a perfective 1st-person future
# verb of work, a deferral phrase, or "I'll <work verb>".
_PROMISE_RE = re.compile(
    r"\b(?:поправлю|исправлю|сделаю|добавлю|заведу|открою|починю|допишу|проверю"
    r"|вернусь\s+к|займусь|доделаю|перепишу)\b"
    r"|(?:отдельн\w+|следующ\w+)\s+(?:PR|пулл?-?реквест\w*|коммит\w*)"
    r"|\bI(?:'ll|\s+will)\s+(?:fix|open|add|do|follow\s+up|look|update|write|check)\b"
    r"|\b(?:in\s+a\s+)?follow-?up\s+PR\b|\bseparately\s+later\b",
    re.IGNORECASE,
)
# The promise is backed when the reply names a durable record of the remaining
# work (an issue or PR number, a handoff/todo file).
_PROMISE_BACKED_RE = re.compile(
    r"#\d+|handoff|хендофф|\S*(?:todo|TODO|HANDOFF)\S*\.md",
    re.IGNORECASE,
)
# Work offered on the owner's consent or command ("если подходит", "по твоей
# команде", "if that works for you"). A condition on the agent's own result
# ("если нормально отработает", "if it works") names no owner and is not one.
# Shared by the promise check (such an offer is no promise) and the hand-off
# prefilter (such an offer goes to the judge).
_OWNER_CONSENT = (
    r"\bесли\s+(?:\w+\s+)?(?:подход\w*|соглас\w*|хоти\w*|хочешь|устраива\w*|одобр\w*)\b"
    r"|\bесли\s+(?:тебе|вам)\s+(?:ок|окей|норм\w*)\b"
    r"|\bпо\s+(?:твоей|вашей)\s+команде|скажи(?:те)?\s*[\u2014\u2013-]+\s*сделаю|\bon\s+your\s+go\b"
    r"|\bif\s+(?:that|this|it)\s+(?:works|is\s+(?:ok|fine))\s+(?:for|with|to)\s+you\b"
    r"|\bif\s+(?:that|this)\s+sounds\s+(?:good|right|fine|ok)\b"
    r"|\bif\s+you\s+(?:agree|want|approve|prefer)\b"
)
REPLY_LINT_MARKER = "reply-lint:"
_CLAIM_REASON = (
    REPLY_LINT_MARKER + " the reply claims completion without its observation. Attach "
    "the observation that proves it (command or query, value, time) labelled "
    "measured / observed / assumed, on one sampled real artifact of the original "
    "scenario; or write 'not verified'. A number from the owner, a client, memory or "
    "a delegate is a claim until reproduced from the primary source. Append only the missing part; do not "
    "repeat the reply."
)
_PROMISE_REASON = (
    REPLY_LINT_MARKER + " the reply promises future work that this turn did not do. "
    "Do it now if it is inside your authority, or state plainly that it is not done "
    "and name where it is tracked (issue, PR, handoff file). Append only the missing part; do not repeat the reply."
)


# The one word before a done word that makes it a question, a relative clause, a condition or a
# future, not an assertion ("как работает", "когда будут готовы", "once done").
_DONE_LABEL_RE = re.compile(
    r"^\W*(?:что\s+сделано|what(?:\s+(?:was|is))?\s+done)\s*:", re.IGNORECASE
)
_NON_ASSERTION_WORD_RE = re.compile(
    r"\b(?:как|how|когда|when|once|будет|будут|станет|will|если|if)\s+$", re.IGNORECASE
)
# A condition right after the done word qualifies the status, it does not claim it.
_QUALIFIED_AFTER_RE = re.compile(
    r"(?:готов\w*,?\s+когда|done\s+when)\b"
    r"|(?:сделан\w*|done)\s+(?:через|в\s+виде|via|as\s+an?\b|\S+['`](?:ом|ем)\b)",
    re.IGNORECASE,
)
_NUMBER_WORDS = {
    "один": 1, "одна": 1, "одного": 1, "первый": 1, "первая": 1, "первое": 1, "first": 1,
    "one": 1, "два": 2, "две": 2, "двух": 2, "второй": 2, "two": 2, "три": 3, "трёх": 3,
    "трех": 3, "третий": 3, "three": 3, "четыре": 4, "четырёх": 4, "четырех": 4,
    "four": 4, "пять": 5, "пяти": 5, "five": 5,
}
_NUMBER = r"(\d+|" + "|".join(_NUMBER_WORDS) + r")"
_PROGRESS_RE = re.compile(_NUMBER + r"\s+(?:из|of)\s+" + _NUMBER + r"(?!\w)", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s)|\n")
# An offer names, before its verb, an action of the owner or a third party
# ("пришлите URL, и я проверю", "once you allow writes, I'll write it").
_OFFER_RE = re.compile(
    r"(?:пришли(?:те)?|разреши(?:те)?|напиши(?:те)?|скажи(?:те)?|дадите|дашь|ответит)(?![\w-])"
    r"|\bвы\b|\bты\b|\byou\b|\ballow\b",
    re.IGNORECASE,
)


# A condition on an event the agent does not drive this turn: a delegate or tool
# of the agent finishing or arriving, named as a noun or by a third-person verb
# ("когда агент закончит", "после ревью", "once the reviewer finishes", "по
# уведомлению"), or the owner's consent. The words between the trigger and the
# event stay inside one sentence and hold no first person, so the agent's own
# act ("когда закончу", "after I run the tests") is not such an event.
_EVENT_GAP = r"(?:[^\w.!?\n]+(?!(?:я|мы|мо[йеиёя]\w*|наш\w*|I|we|my|our)\b)\w+){0,3}?[^\w.!?\n]+"
_EVENT_CONDITION_RE = re.compile(
    r"(?:\b(?:когда|после|как(?:\s+только)?|о?жд\w*|ожида\w*|дожд\w*|once|when|after|until|wait\w*)\b"
    + _EVENT_GAP
    + r"(?:"
    r"(?:суб)?агент\w*|ревью\w*|сборщик\w*|билдер\w*|проб[аыеу]\b|монитор\w*|CI\b|джоб\w*|jobs?\b"
    r"|прогон(?:а|у|ом|е|ы|ов|ам)?\b|уведомлени\w*|notifications?\b|(?:sub)?agents?\b|reviews?\b"
    r"|reviewers?\b|builders?\b|probes?\b|monitors?\b|runs?\b"
    r"|законч(?:ит|ат|ится|атся)\b|заверш(?:ит|ат|ится|атся)\b|придёт|придет|придут"
    r"|вернёт\w*|вернет\w*|вернут\b|пройд(?:[её]т|ут)\b|отработа(?:ет|ют)\b"
    r"|finish(?:es|ed)\b|returns\b|arriv(?:es|ed)\b|completes\b|lands\b))"
    r"|\bпо\s+уведомлени\w*|\bon\s+(?:the\s+)?notification\b|" + _OWNER_CONSENT,
    re.IGNORECASE,
)


def _paragraph_previous_sentence_start(text, start):
    """Start of the sentence before the one at ``start``, when both sit in
    one paragraph (no blank line between); else ``start``."""
    if start == 0 or re.search(r"\n\s*\n\s*$", text[:start]):
        return start
    return _sentence_bounds(text, max(start - 2, 0))[0]


def _number(word):
    word = word.lower()
    return _NUMBER_WORDS[word] if word in _NUMBER_WORDS else int(word)


def _sentence_bounds(text, pos):
    start = 0
    for match in _SENTENCE_END_RE.finditer(text, 0, pos):
        start = match.end()
    end = _SENTENCE_END_RE.search(text, pos)
    return start, end.end() if end else len(text)


def _is_partial_progress(sentence):
    return any(_number(m.group(1)) < _number(m.group(2)) for m in _PROGRESS_RE.finditer(sentence))


def _label_covers(text, start, pos):
    """The done word ending at ``pos`` sits inside a "what was done:" label that
    opens the sentence starting at ``start``."""
    label = _DONE_LABEL_RE.match(text[start:])
    return bool(label) and pos - start <= label.end()


def _has_done_claim(original):
    text = _blank_quoted(original)
    for match in _DONE_RE.finditer(text):
        before = text[: match.start()]
        start, end = _sentence_bounds(text, match.start())
        if (
            _NEGATED_DONE_RE.search(before[-24:])
            or _NON_ASSERTION_WORD_RE.search(before[-16:])
            or _QUALIFIED_AFTER_RE.match(original, match.start())
            or _label_covers(text, start, match.end())
            or _is_partial_progress(text[start:end])
        ):
            continue
        return True
    return False


def _has_unbacked_promise(text):
    """A promise counts when the reply names no durable record of the remaining
    work, no action of another party precedes the promise in its sentence, and
    neither its sentence nor the sentence before it in the same paragraph
    conditions it on a background event or the owner's consent."""
    if _PROMISE_BACKED_RE.search(text):
        return False
    blanked = _blank_quoted(text)
    for match in _PROMISE_RE.finditer(blanked):
        start, end = _sentence_bounds(blanked, match.start())
        if _OFFER_RE.search(blanked, start, match.start()):
            continue
        window = _paragraph_previous_sentence_start(blanked, start)
        if not _EVENT_CONDITION_RE.search(blanked, window, end):
            return True
    return False


HANDOFF_JUDGE_PROMPT = (
    "You check the final reply of a coding agent before its turn ends. The reply, as a JSON string:\n"
    "{reply}\n\n"
    "Decide whether it is an OWNER HAND-OFF (any language). The agent's tool to ask the owner, "
    "AskUserQuestion, pauses the turn and is not visible in this text, so a reply that ends the turn "
    "while still needing the owner has not asked through it.\n\n"
    "It is an owner hand-off when either holds:\n"
    "1. Work stays waiting on the owner's choice, answer, approval, confirmation, permission or input. "
    "Any shape counts: a direct question offering options (\"A or B?\", \"which do we do?\"), \"your call\", "
    "\"waiting for your decision\", work conditioned on the owner's act (\"after your approval I will\", "
    "\"I do it on your word\", \"not starting without your go\", \"if you allow it, I will\"), the owner's "
    "choice named as pending (\"your choice\", \"three decisions still wait for you\"), an indirect question "
    "(\"waiting to hear whether you approve B\"), or a reminder that an earlier question is still open. "
    "This holds even while background jobs or agents run.\n"
    "2. The reply points to content of an earlier message instead of stating it (\"see the options above\", "
    "\"as in my previous message\") while asking the owner something about it.\n\n"
    "NOT a hand-off:\n"
    "- an answer to the owner's own question, even if it ends with a short optional follow-up offer;\n"
    "- a tunable value already set and reported, e.g. \"set to 7 days, keep or change?\";\n"
    "- a wait that is negated or resolved (\"nothing waits on you\", \"your answers were applied\", "
    "\"no decision needed\");\n"
    "- plain status, results, or a report of work that continues without the owner (background job, CI, "
    "while the owner is away);\n"
    "- a wait on the agent's own subagents, builders, reviewers, probes or background tasks (\"waiting for E and F\");\n"
    "- waiting on a third party or an external event, not the owner;\n"
    "- a statement of a limitation or of what blocks something, when nothing is asked of the owner;\n"
    "- an idle tick of an unattended goal or loop that names the owner's own stated action as its resume "
    "condition (\"waiting until you lift the goal\");\n"
    "- a request for an action only the owner can perform, when the reply states the exact action and why: a "
    "permission the hooks forbid to agents, a command the owner must type (\"! kill 1234\", an "
    "`APPROVE <hash>` line for a named plan whose path, hash and scope the reply states);\n"
    "- quoting or describing such phrases as examples (in code, quotes, or rule text).\n\n"
    "Decide in two steps. First find the one sentence in the reply, written by the agent and addressed to the "
    "owner, that asks the owner for a choice, answer, approval, action or input, or says work waits on the "
    "owner. If no such sentence exists, the reply is not a hand-off: a long status, a plan the agent carries "
    "out itself, next steps it will take, a future review gate, or work waiting on CI, a deploy, a probe, a "
    "reviewer, a builder or another agent all pass. If that sentence exists and none of the NOT cases covers "
    "it, it is a hand-off. When unsure, pass.\n\n"
    "Respond with one JSON object only, no other text, with \"sentence\" written before \"handoff\":\n"
    "{{\"sentence\": \"<that sentence quoted, at most 15 words, or none>\", \"handoff\": <true or false>}}"
)
_HANDOFF_REASON = (
    REPLY_LINT_MARKER + " the reply leaves work waiting on the owner in plain text "
    "(\"{sentence}\"). If the decision is inside your authority (a technical question by the "
    "authority rule, or a tunable), decide it, do it now and report it. Otherwise ask now with the "
    "AskUserQuestion tool: each option self-contained (what the owner sees change, the "
    "deciding numbers, your recommendation first), never a pointer to an earlier message."
)
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


# A reply that hands work to the owner addresses the owner or conditions work on the
# owner's act: a second-person pronoun, possessive, finite or imperative verb (RU/EN),
# "owner", an idiom like "по команде" / "жду подтверждения" / "пока не скажете" /
# "let me know", a consent offer ("если подходит, сделаю"), or a question mark. A wide
# net: the model judge filters. Anything else skips the judge.
_ADDRESSES_OWNER_RE = re.compile(
    r"\?"
    r"|\b(?:ты|тебя|тебе|тобой|тобою|тво[йяёеию]\w*|вы|вас|вам|вами|ваш\w*|владел\w*"
    r"|you|your|yours|yourself|yourselves|owner\w*)\b"
    r"|\b(?:скажи|скажите|подтверди|подтвердите|дай|дайте|напиши|напишите|одобри|одобрите"
    r"|разреши|разрешите|реши|решите|выбери|выберите|ответь|ответьте|пришли|пришлите|глянь"
    r"|гляньте|посмотри|посмотрите|сними|снимите|запусти|запустите|останови|остановите"
    r"|верни|верните|согласуй|согласуйте)\b"
    r"|\b\w{2,}(?:ешь|ёшь|ишь|ете|ёте|ите|йте|ьте)\b"
    r"|\bпо\s+(?:\w+\s+)?(?:команде|слову|сигналу|отмашке)"
    r"|\bжду\s+(?:\w+\s+)?(?:подтвержд|одобр|соглас|команд|решени|ответ|отмашк|разрешени|слов|\"|«)"
    r"|\bпока\s+не\s+(?:скаж|дад|подтверд|разреш|одобр|ответ|соглас|реш)"
    r"|\b(?:let me know|approve|confirm|tell me|go ahead|green light|sign[- ]off)\b"
    r"|" + _OWNER_CONSENT,
    re.I,
)


def addresses_owner(reply):
    """The reply can be a hand-off at all, so the model judge is worth a call."""
    return bool(_ADDRESSES_OWNER_RE.search(reply))


def handoff_judge_prompt(reply):
    """The judge prompt for one final reply; the reply goes in as a JSON string."""
    return HANDOFF_JUDGE_PROMPT.format(reply=json.dumps(reply, ensure_ascii=False))


def parse_handoff_verdict(cli_stdout):
    """Verdict from `claude -p --output-format json` output: {"handoff", "sentence"},
    or None when the run failed or the result is not exactly one verdict object
    (the hook then fails open)."""
    try:
        envelope = json.loads(cli_stdout)
    except ValueError:
        return None
    if not isinstance(envelope, dict) or envelope.get("is_error") or not isinstance(envelope.get("result"), str):
        return None
    result = envelope["result"].strip()
    fenced = _JSON_FENCE_RE.match(result)
    if fenced:
        result = fenced.group(1)
    try:
        verdict = json.loads(result)
    except ValueError:
        return None
    if not isinstance(verdict, dict) or not isinstance(verdict.get("handoff"), bool):
        return None
    return {"handoff": verdict["handoff"], "sentence": str(verdict.get("sentence") or "")}


def reply_lint_decision(last_assistant_text, stop_hook_active, handoff=None):
    """Return the main session once when its final reply claims completion
    without an observation, promises future work with no durable record
    of it (issue, PR, handoff file), or hands work to the owner in plain
    text. ``handoff`` is the judge verdict (``parse_handoff_verdict``), None
    when no verdict exists.
    Not claims (2026-10-15): a Russian not-verified disclosure, a done word
    as a section label or with a manner complement.
    The second stop (``stop_hook_active``) always passes."""
    if stop_hook_active:
        return {"decision": "allow"}
    text = (last_assistant_text or "").strip()
    if not text:
        return {"decision": "allow"}
    reasons = []
    if _has_done_claim(text) and not _OBSERVATION_RE.search(text):
        reasons.append(_CLAIM_REASON)
    if _has_unbacked_promise(text):
        reasons.append(_PROMISE_REASON)
    if handoff and handoff["handoff"]:
        reasons.append(_HANDOFF_REASON.format(sentence=handoff["sentence"].replace('"', "'")))
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
    "`access:` = the route to every system the probe reads. A "
    "delegate that must discover these burns its context before the deliverable "
    "(retro 2026-10-12: 30+ builders and 4 probes died that way). Add the lines, "
    "or, from the main session, a line `AGENT_TYPE_OVERRIDE: <reason>`."
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
    r"(?i)([\w.-]*" + _SECRET_NAME + r"[\w.-]*[ \t]*[:=][ \t]*[\"']?)"
    r"([^\s\"',;&<>()\[\]]{6,})(?=[\s\"',;&<>]|$)"
)
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9._+/=-]{8,})")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")
_K8S_SECRET_RE = re.compile(r"(?m)^(\s+[\w.-]+:\s+)([A-Za-z0-9+/=]{16,})$")
_K8S_SECRET_JSON_RE = re.compile(r"(\"[\w.-]+\":\s*\")([A-Za-z0-9+/=]{16,})(\")")


def _mask(value):
    import hashlib

    return "<redacted sha256:%s>" % hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def redact_secrets(text, known_tokens=()):
    """Return ``text`` with secret-looking values replaced by a masked digest:
    every known vault value and every standalone API-key-shaped token
    (vault_policy), then the named-field, Bearer, JWT, PEM and Kubernetes Secret
    rules. Pure numbers and values shorter than six characters are left alone."""
    if not text:
        return text
    import vault_policy

    out = vault_policy.redact_known(text, known_tokens) if known_tokens else text
    out = vault_policy.redact_shaped(out)
    out = _PEM_RE.sub(lambda m: _mask(m.group(0)), out)
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


# --- destructive policy (hooks/deny-destructive.py) -------------------------

_PLAN_MARKER_RE = re.compile(
    r"^\s*(?:bash|sh|zsh)\s+(\S+)\s+#\s*DESTRUCTIVE_PLAN\s+sha256=([0-9a-fA-F]{64})\s*$"
)
_PLAN_HEADERS = (
    "# scope:", "# undo:", "# count:", "# contradiction:", "# prod_position:", "# way_back:"
)
_PLAN_INDIRECT_RE = re.compile(r"^\s*(?:(?:source|\.|exec|eval)\s|(?:bash|sh|zsh)\s+\S)")
_SEGMENT_PUNCT = frozenset(";&|()\n`{}")
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_SHELLS = frozenset({"bash", "sh", "zsh"})
_INNER_SUBST_RE = re.compile(r"`([^`]*)`|\$\(([^()]*)\)")
_KUBECTL_VALUE_FLAGS = frozenset({
    "-n", "-s", "-v", "--v", "--as", "--as-group", "--token", "--request-timeout",
    "--namespace", "--context", "--kubeconfig", "--cluster", "--user", "--server",
})
_KUBECTL_READS = frozenset({
    "get", "describe", "logs", "top", "explain", "api-resources", "api-versions",
    "version", "cluster-info", "diff", "cp", "debug", "port-forward",
    "attach", "wait", "events", "kustomize",
})
_KUBECTL_READ_PAIRS = {
    "config": frozenset({"view", "get-contexts", "current-context"}),
    "auth": frozenset({"can-i"}),
    "rollout": frozenset({"status", "history"}),
}
_KUBECTL_NO_UNDO_KINDS = frozenset({
    "namespace", "namespaces", "ns", "secret", "secrets", "pvc", "pvcs",
    "persistentvolumeclaim", "persistentvolumeclaims", "crd", "crds",
    "customresourcedefinition", "customresourcedefinitions",
})
_SECRET_WORD_RE = re.compile(r"^secrets?(?:$|[/.,])", re.I)
_SECRET_MANIFEST_RE = re.compile(r"\bkind:\s*Secret\b")
_SQL_NO_UNDO_RE = re.compile(
    r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA|INDEX|VIEW|MATERIALIZED\s+VIEW|SEQUENCE|TYPE"
    r"|FUNCTION|EXTENSION|TRIGGER|POLICY|ROLE|USER)\b|\bTRUNCATE\b|\bALTER\s+TABLE\b[^;]*\b(?:DROP|DELETE)\b"
    r"|\.drop(?:Database)?\(",
    re.I,
)
_SQL_DELETE_RE = re.compile(r"\bDELETE\s+FROM\b([^;]*)", re.I)
_SQL_WRITE_RE = re.compile(
    r"\bUPDATE\s+\S+\s+SET\b|\.(?:delete|update)(?:One|Many)\(|\.remove\(", re.I
)
_SQL_FILE_FLAGS = frozenset({"-f", "--file", "--queries-file", "<"})
# SET changes only the session it runs in.
_SQL_READ_WORDS = frozenset({"SELECT", "WITH", "SHOW", "EXPLAIN", "DESCRIBE", "DESC", "SET", "(SELECT"})
_INLINE_INTERPRETER_RE = re.compile(r"^(?:python[0-9.]*|perl|ruby|node)$")
_INLINE_WRITE_RE = re.compile(
    r"rmtree|os\.remove|unlink|rmdir|shutil\.move|rmSync|rm_rf|FileUtils\.rm", re.I
)
# SQL in interpreter code counts only next to a DB client; without one the
# code edits or generates SQL text.
_INLINE_DB_CLIENT_RE = re.compile(
    r"psycopg|asyncpg|sqlalchemy|clickhouse|pymysql|mysql|sqlite3|psql|pg8000|\bdatabases\b"
    r"|\bDBI\b|\bpg\b|Sequel"
)


def _inline_code_hits(program, code):
    hits = []
    if _INLINE_WRITE_RE.search(code):
        hits.append(("approval", "inline " + program + " code deletes or moves files"))
    if _INLINE_DB_CLIENT_RE.search(code):
        hits.extend(_sql_hits([code]))
    return hits
_XARGS_VALUE_FLAGS = frozenset({"-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a"})
_SSH_VALUE_FLAGS = frozenset({"-p", "-i", "-l", "-o", "-F", "-J", "-b", "-c", "-D", "-L", "-R", "-W"})
_DOCKER_VALUE_FLAGS = frozenset({"-u", "--user", "-e", "--env", "-w", "--workdir", "--env-file"})


def plan_marker(command):
    """(path, sha256 hex) when the whole command runs a destructive plan, else None."""
    m = _PLAN_MARKER_RE.match(command or "")
    return (m.group(1), m.group(2).lower()) if m else None


def _segments_meta(command):
    """[tokens, separators before, separators after] per shell segment; quoted
    text stays one token, so a quoted word never becomes a command head.
    Backticks split like ``;``."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n`{}")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        tokens = command.split()
    # A comment ends at the next separator, not only at a newline: over-reading
    # a comment as a command can only raise a class, never hide one.
    segments, current, comment, punct = [], None, False, ""
    for tok in tokens:
        if tok and tok != "{}" and set(tok) <= _SEGMENT_PUNCT:
            current, comment = None, False
            punct += " " + tok
            continue
        if comment or tok != "{}" and tok.startswith("#"):
            comment = True
            continue
        if current is None:
            if segments:
                segments[-1][2] = punct
            current = [[], punct, ""]
            segments.append(current)
            punct = ""
        current[0].append(tok)
    if segments:
        segments[-1][2] = punct
    return segments


def _shell_segments(command):
    """Token lists per shell segment (see ``_segments_meta``)."""
    return [seg[0] for seg in _segments_meta(command)]


def _command_head(tokens):
    """(program, args, via_xargs) after env assignments, wrappers and their options."""
    i, via_xargs = 0, False
    n = len(tokens)
    while i < n:
        word = os.path.basename(tokens[i])
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i]):
            i += 1
        elif word == "function":
            i += 2
        elif tokens[i] in ("do", "then", "else", "elif", "if", "while", "until", "!"):
            i += 1
        elif word == "timeout":
            i += 1
            while i < n and tokens[i].startswith("-"):
                i += 2 if tokens[i] in ("-s", "-k", "--signal", "--kill-after") else 1
            i += 1
        elif word == "rtk":
            i += 2 if i + 1 < n and tokens[i + 1] == "proxy" else 1
        elif word == "xargs":
            via_xargs = True
            i += 1
            while i < n and tokens[i].startswith("-"):
                i += 2 if tokens[i] in _XARGS_VALUE_FLAGS else 1
        elif word == "nice":
            i += 1
            while i < n and tokens[i].startswith("-"):
                i += 2 if tokens[i] == "-n" else 1
        elif word in ("env", "nohup", "time", "command", "exec"):
            i += 1
            while i < n and tokens[i].startswith("-"):
                i += 2 if (word, tokens[i]) in (
                    ("env", "-u"), ("env", "-C"), ("env", "-S"), ("exec", "-a")
                ) else 1
        else:
            return word, tokens[i + 1:], via_xargs
    return "", [], via_xargs


_REDIRECT = re.compile(r"^(\d*|&)(>>?|<)(&?\d+|&-)?$|^(\d*|&)(>>?|<)(.+)$")


def _path_args(args):
    """Path operands; a redirect and its target file are not operands."""
    paths, after_dashdash, skip = [], False, False
    for arg in args:
        if skip:
            skip = False
        elif m := _REDIRECT.match(arg):
            skip = m.group(2) is not None and m.group(3) is None
        elif not after_dashdash and arg == "--":
            after_dashdash = True
        elif after_dashdash or not arg.startswith("-"):
            paths.append(arg)
    return paths


def _is_ours_path(path, cfg, relative_ok):
    if any(ch in path for ch in "$`{}") or ".." in path.split("/"):
        return False
    norm = os.path.normpath(os.path.expanduser(path))
    parts = norm.split("/")
    if (relative_ok or norm.startswith("/")) and ".worktrees" in parts[:-1]:
        return True
    if norm.startswith("/") and "tmp" in parts[1:]:
        i = parts.index("tmp", 1)
        if os.path.exists(os.path.join("/".join(parts[:i]) or "/", ".git")):
            return True
    for prefix in cfg["ours_paths"]:
        if not prefix.startswith(("/", "~")):
            if not relative_ok:
                continue
        else:
            prefix = os.path.expanduser(prefix)
        root = prefix.rstrip("/")
        if norm.startswith(prefix) or norm == root and not root.startswith("/"):
            return True
    return False


def _temp_prefixes():
    """The system temp dir (``$TMPDIR`` on macOS) as given and resolved; only
    entries below it are ours, not the directory itself."""
    base = tempfile.gettempdir()
    return sorted({base.rstrip("/") + "/", os.path.realpath(base).rstrip("/") + "/"})


def _scratch_roots():
    """Directories whose entries are scratch: /tmp and the system temp dir,
    as written and resolved."""
    return sorted({"/tmp/", os.path.realpath("/tmp") + "/"} | set(_temp_prefixes()))


def _real_operand(path, keep_final=False):
    """Real path of an absolute ``path``, symlinks resolved before each
    ``..`` as the kernel walks it. ``keep_final``: the final component is the
    object itself (``rm`` of a symlink removes the link) unless it is ``.``,
    ``..`` or ends in ``/``."""
    name = os.path.basename(path)
    if keep_final and name not in ("", ".", ".."):
        return os.path.join(os.path.realpath(os.path.dirname(path)), name)
    return os.path.realpath(path)


def _resolve(path, ctx, keep_final=False):
    """Real path of an operand (see ``_real_operand``), None when the
    directory a relative operand is taken from is not known."""
    if path.startswith("/"):
        return _real_operand(path, keep_final)
    if path == "~" or path.startswith("~/"):
        return _real_operand(os.path.expanduser(path), keep_final)
    if ctx["dir"]:
        return _real_operand(os.path.join(ctx["dir"], path), keep_final)
    return None


def _ours(path, cfg, ctx, keep_final=False):
    """An operand is ours by its real path; only when no working directory
    was given and no cd ran is a relative operand judged by its text."""
    if any(ch in path for ch in "$`{}") or (
        any(ch in path for ch in "*?[") and ".." in path.split("/")
    ):
        return False  # a glob before ".." may match a symlink
    resolved = _resolve(path, ctx, keep_final)
    if resolved is None:
        return ctx["dir"] == "" and not ctx["cd_seen"] and _is_ours_path(path, cfg, True)
    return _is_ours_path(resolved, cfg, False)


def _path_args_keep_flags(args):
    """``args`` without redirects and their target files."""
    out, skip = [], False
    for arg in args:
        redirect = _REDIRECT.match(arg)
        if skip:
            skip = False
        elif redirect:
            skip = redirect.group(2) is not None and redirect.group(3) is None
        else:
            out.append(arg)
    return out


def _is_plain_relative(path):
    """A relative path a rename can reverse: no climbing, variables or globs."""
    return not (
        path.startswith(("/", "~", "-")) or ".." in path.split("/")
        or any(ch in path for ch in "$`{}*?[")
    )


_RM_OWN_HINT = (
    "; write each path absolute (a relative path counts only after an unconditional"
    " top-level cd to an existing directory), or run ~/.claude/tools/rm-own -r <path>..."
    " for paths inside a worktree, <repo>/tmp or /tmp (it checks the real path)"
)


def own_removal(path, params=None):
    """(real path rm-own removes, None) or (None, why it refuses ``path``).
    The path is taken physically: the parent is resolved through symlinks
    before any ``..`` is applied, the final component is the object itself."""
    cfg = _decision_cfg(params, None)
    expanded = os.path.expanduser(path).rstrip("/") or "/"
    name = os.path.basename(expanded)
    if name in ("", ".", "..") or name == ".worktrees":
        return None, path + ": the .worktrees directory, a root, . or .. is never removed"
    real = _real_operand(os.path.join(os.getcwd(), expanded), True)
    if os.path.exists(os.path.join(real, ".git")) and not os.path.islink(real):
        return None, path + ": a repository root is never removed (use git worktree remove)"
    if not _is_ours_path(real, cfg, False):
        return None, path + ": resolves to " + real + ", outside a worktree, <repo>/tmp, /tmp/<x>, the temp dir, ~/.claude/tmp or ~/Library/Caches"
    return real, None


def _rm_rule(paths, cfg, ctx, what, keep_final=False):
    if paths and all(_ours(p, cfg, ctx, keep_final) for p in paths):
        return ("ours", what + " of project-owned scratch paths")
    return ("impossible", what + " outside the project-owned paths has no undo" + _RM_OWN_HINT)


def _selector_keys(args):
    keys = []
    for i, arg in enumerate(args):
        value = None
        if arg in ("-l", "--selector") and i + 1 < len(args):
            value = args[i + 1]
        elif arg.startswith(("-l=", "--selector=")):
            value = arg.split("=", 1)[1]
        elif arg.startswith("-l") and len(arg) > 2:
            value = arg[2:]
        if value:
            keys.extend(re.split(r"!=|==|=|\s", part.strip())[0] for part in value.split(","))
    return keys


def _flag_value(args, flag):
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return None


_KUBECTL_BOOL_FLAGS = frozenset({
    "--insecure-skip-tls-verify", "--match-server-version", "--warnings-as-errors",
    "--disable-compression", "-h", "--help",
})


def _kubectl_class(args, cfg, ctx):
    """Options, redirects and the verb are read before ``--``; what follows
    ``--`` is the command an exec runs, its redirects included."""
    end = args.index("--") if "--" in args else len(args)
    rest, i = [], 0
    while i < end:
        redirect = _REDIRECT.match(args[i])
        if args[i] in _KUBECTL_VALUE_FLAGS:
            i += 2
            continue
        if redirect:
            i += 2 if redirect.group(2) is not None and redirect.group(3) is None else 1
            continue
        if args[i] != "\\":
            rest.append(args[i])
        i += 1
    words = [a.rstrip("\\") for a in rest if not a.startswith("-")]
    if not words:
        return []
    verb = words[0]
    for a in rest[:next(i for i, a in enumerate(rest) if not a.startswith("-"))]:
        if "=" not in a and a not in _KUBECTL_BOOL_FLAGS:
            return [("approval", "kubectl option " + a + " before the verb is not known; its value may be read as the verb")]
    if "$" not in verb and not re.match(r"^[a-z][a-z-]*$", verb):
        return []  # not a kubectl command (a comment or prose read as one); kubectl rejects it
    if any(a.startswith("--dry-run") and a != "--dry-run=none" for a in rest):
        return []
    if verb == "exec":
        return _classify_inner(args[end + 1:], cfg, ctx) if end < len(args) else []
    if verb in _KUBECTL_READS:
        return []
    if verb in _KUBECTL_READ_PAIRS and len(words) > 1 and words[1] in _KUBECTL_READ_PAIRS[verb]:
        return []
    source = _flag_value(rest, "-f") or _flag_value(rest, "--filename") or ""
    names_secret = any(_SECRET_WORD_RE.match(w) for w in words[1:]) or "secret" in source.lower()
    if verb == "delete":
        kinds = re.split(r"[,/]", words[1].lower()) if len(words) > 1 else []
        if names_secret or any(kind.split(".")[0] in _KUBECTL_NO_UNDO_KINDS for kind in kinds):
            return [("impossible", "kubectl delete of a namespace, secret, PVC or CRD has no undo")]
        keys = _selector_keys(rest)
        if keys and "--all" not in rest and all(key in cfg["ours_labels"] for key in keys):
            return [("ours", "kubectl delete by a session-owned label")]
        return [("approval", "kubectl delete of controller-managed objects")]
    if names_secret or (
        verb in ("apply", "create", "replace") and _SECRET_MANIFEST_RE.search(ctx["text"])
    ):
        return [("impossible", "kubectl writes a Secret: the previous value cannot be restored")]
    phrase = " ".join(words[:2]) if " ".join(words[:2]) in _KUBECTL_CHEAP_UNDO else verb
    undo = _KUBECTL_CHEAP_UNDO.get(phrase)
    if undo and (
        any(a.split("=", 1)[0] == "--all" for a in rest) or _selector_keys(rest)
        or _kubectl_named_objects(rest, len(phrase.split())) > 1
    ):
        return [("approval", "kubectl " + phrase + " with --all, a selector or several objects reaches beyond one object; the owner decides that blast radius")]
    if undo:
        return [("ours", "kubectl " + phrase + " of one object has a cheap mechanical undo: " + undo)]
    return [("approval", "kubectl " + verb + " changes live cluster state")]


_GIT_VALUE_OPTIONS = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env",
})
_GIT_REDIRECT_RE = re.compile(r"--work-tree|--git-dir|\bGIT_WORK_TREE\b|\bGIT_DIR\b|\bcore\.(?:worktree|bare)\b")


# Options of the cheap-undo verbs whose value is the next word, not an object.
_KUBECTL_WRITE_VALUE_FLAGS = frozenset({
    "-l", "--selector", "--replicas", "--current-replicas", "--resource-version", "--timeout",
    "-o", "--output", "-f", "--filename", "--field-manager", "--template", "--to-revision",
})


def _kubectl_named_objects(rest, phrase_len):
    """Number of objects a resource verb names: each TYPE/NAME word, or the
    names after a bare TYPE (times the types in a TYPE,TYPE list)."""
    targets, i = [], 0
    while i < len(rest):
        if rest[i] in _KUBECTL_WRITE_VALUE_FLAGS:
            i += 2
            continue
        if not rest[i].startswith("-"):
            targets.append(rest[i].rstrip("\\"))
        i += 1
    targets = targets[phrase_len:]
    if not targets:
        return 0
    if "/" in targets[0]:
        return len(targets)
    return (len(targets) - 1) * len(targets[0].split(","))


# Live writes with a cheap, known, mechanical undo (owner decision K27): run at once.
_KUBECTL_CHEAP_UNDO = {
    "scale": "read the previous replica count first, then scale back to it",
    "rollout restart": "none needed, pods are recreated from the unchanged spec",
    "rollout undo": "kubectl rollout undo to the revision before",
    "rollout pause": "kubectl rollout resume",
    "rollout resume": "kubectl rollout pause",
}


def _git_own_repository(directory):
    """True when the repository holding ``directory`` (the nearest ``.git``
    entry up the tree) is an agent's own checkout: a linked worktree below a
    ``.worktrees`` directory, or a repository under /tmp or the temp dir."""
    root = directory
    while root and not os.path.lexists(os.path.join(root, ".git")):
        parent = os.path.dirname(root)
        root = None if parent == root else parent
    if not root:
        return False
    if ".worktrees" in root.split("/")[:-1] and os.path.isfile(os.path.join(root, ".git")):
        return True
    return any(root.startswith(prefix) for prefix in _scratch_roots())


def _git_class(args, cfg, ctx):
    i, repo = 0, ctx["dir"] or None
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C" and i + 1 < len(args):
            target = args[i + 1]
            literal = not any(ch in target for ch in "$`{}*?[")
            repo = _resolve(target, dict(ctx, dir=repo)) if literal else None
        i += 2 if args[i] in _GIT_VALUE_OPTIONS else 1
    if i >= len(args):
        return []
    sub, rest = args[i], _path_args_keep_flags(args[i + 1:])
    discard = None
    if sub == "clean":
        discard = "git clean deletes untracked files"
    elif sub == "reset" and "--hard" in rest:
        discard = "git reset --hard discards uncommitted work"
    elif sub == "checkout" and "--" in rest and rest.index("--") < len(rest) - 1:
        discard = "git checkout -- <path> discards uncommitted work"
    elif sub == "restore" and not (
        any(a in ("--staged", "-S") for a in rest) and not any(a in ("--worktree", "-W") for a in rest)
    ):
        discard = "git restore discards uncommitted work"
    if discard:
        if not _GIT_REDIRECT_RE.search(ctx["text"]) and _git_own_repository(repo):
            return [("ours", discard + " in an agent worktree")]
        return [("impossible", discard + " outside an agent worktree (.worktrees/<task>); commit or stash first, or work in a worktree")]
    if sub == "push" and any(
        a in ("-f", "--force") or a.startswith("+") for a in rest
    ):
        return [("impossible", "a force push overwrites remote history")]
    return []


def _sql_hits(args, file_texts=()):
    """``file_texts``: text of each file the client reads, None when unknown."""
    text = " ".join(args)
    if _SQL_NO_UNDO_RE.search(text):
        return [("impossible", "SQL DROP/TRUNCATE/ALTER ... DROP has no undo")]
    for m in _SQL_DELETE_RE.finditer(text):
        if not re.search(r"\bWHERE\b", m.group(1), re.I):
            return [("impossible", "SQL DELETE without WHERE has no undo")]
    if _SQL_DELETE_RE.search(text) or _SQL_WRITE_RE.search(text):
        return [("approval", "SQL write to a database")]
    hits = []
    for body in file_texts:
        if body is None:
            hits.append(("approval", "SQL from a file: its statements are not visible"))
        else:
            hits.extend(_sql_hits([body]) or _sql_read_only_hits(body))
    return hits


def _sql_read_only_hits(text):
    """A SQL file passes unasked only when every statement is a read."""
    text = re.sub(r"--[^\n]*|/\*.*?\*/", " ", text, flags=re.S)
    for stmt in text.split(";"):
        word = stmt.split(None, 1)[0].upper() if stmt.strip() else ""
        if word and word not in _SQL_READ_WORDS:
            return [("approval", "SQL file runs " + word + ", not only reads")]
    return []


def _after_options(args, value_flags):
    """Index of the first positional argument, skipping options and their values."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in value_flags else 1
    return i


def _classify_inner(tokens, cfg, ctx):
    """A command run elsewhere (exec, ssh), ``tokens`` the tail of the current
    segment: one quoted string is re-parsed. Its working directory and
    variables are not ours to know, so the raw (unsubstituted) words count. A
    ``<`` redirect is opened by the local shell: its file resolves here."""
    raw = ctx.get("raw", tokens)
    raw = raw[len(raw) - len(tokens):] if len(raw) >= len(tokens) else tokens
    if len(tokens) == 1:
        return _classify_text(raw[0], cfg, ctx["depth"] + 1, None, None, True)
    local = list(tokens)
    for i, tok in enumerate(local):
        if tok == "<" and i + 1 < len(local):
            local[i + 1] = _resolve(local[i + 1], ctx) or local[i + 1]
        elif tok.startswith("<") and not tok.startswith("<<") and len(tok) > 1:
            local[i] = "<" + (_resolve(tok[1:], ctx) or tok[1:])
    inner = _new_ctx(None, ctx["depth"], ctx["text"])
    inner.update(cd_seen=True, raw=raw)
    return _classify_tokens(local, cfg, inner)


def _cd_target(program, args, ctx):
    """The directory a literal ``cd``/``pushd`` moves to, None when unknown."""
    operands = [a for a in _path_args_keep_flags(args) if not a.startswith("-") or a == "-"]
    if len(operands) > 1:
        return None
    if not operands:
        return os.path.realpath(os.path.expanduser("~")) if program == "cd" else None
    target = operands[0]
    if target == "-" or any(ch in target for ch in "$`{}*?[") or target.startswith("~") and not (
        target == "~" or target.startswith("~/")
    ):
        return None
    return _resolve(target, ctx)


def _lone(char, punct):
    """True when ``punct`` holds ``char`` alone (``|`` not ``||``)."""
    return bool(re.search(r"(?<!\%s)\%s(?!\%s)" % (char, char, char), punct))


def _top_level(ctx, before, after):
    """Outside ``{ }``, blocks, loops, function bodies, pipelines and
    background jobs. A subshell ``( )`` counts as its own top level: its
    state is restored when it ends (``_scope``)."""
    return not (
        ctx["brace"] or ctx["block"]
        or _lone("|", before + after) or _lone("&", after)
    )


def _unconditional(ctx, before, after):
    """A segment the command's own shell surely runs: top level and the
    first of its ``&&``/``||`` chain."""
    return _top_level(ctx, before, after) and "&&" not in before and "||" not in before


def _chain_step(ctx, before):
    """Track the ``&&``/``||`` chain a segment belongs to. A cd reached only
    through ``&&`` holds for the rest of its ``&&`` chain; once the chain
    ends or meets ``||`` the directory is unknown."""
    continues = "&&" in before or "||" in before or _lone("|", before)
    if not continues:
        ctx["chain_or"] = False
    if "||" in before:
        ctx["chain_or"] = True
    if ctx.get("cd_pending") and ("||" in before or not continues):
        ctx["cd_pending"] = False
        ctx["dir"] = None


def _change_dir(program, args, ctx, before, after, negated):
    """Track the working directory through a cd. A top-level cd (see
    ``_top_level``) to a literal directory, not negated by ``!``, reached
    only through ``&&`` and not followed by ``||``, moves it: for the rest of
    the command when it is first of its chain and the directory exists, else
    for the rest of its ``&&`` chain (``_chain_step``; a missing directory
    may be created earlier in the chain). Any other cd makes it unknown for
    the rest of the command or of the enclosing subshell."""
    ctx["cd_seen"] = True
    target = None
    if program != "popd" and not negated and _top_level(ctx, before, after) \
            and "||" not in after and not ctx.get("chain_or"):
        target = _cd_target(program, args, ctx)
    if target is not None and ("&&" in before or not os.path.isdir(target)):
        if "&&" in before or "&&" in after:
            ctx["cd_pending"] = True
        else:
            target = None
    ctx["dir"] = target


def _is_own_tool(head, ctx, name):
    """True when ``head`` is the installed tool ``name`` or this repository's."""
    real = _resolve(os.path.expanduser(head), ctx) if "/" in head else None
    tools = (os.path.expanduser("~/.claude/tools/" + name),
             os.path.join(os.path.dirname(os.path.realpath(__file__)), name))
    return real is not None and real in {os.path.realpath(t) for t in tools}


def forward_refusal(pid, row, uid):
    """None when ``pid`` may be stopped by kill-forward, else the reason.
    ``row`` is the ``(uid, ppid, elapsed, args)`` ps shows for it, or None."""
    if not pid.isdigit() or row is None:
        return pid + ": no such process"
    if row[0] != uid:
        return pid + ": belongs to another user"
    argv = row[3].split()
    if os.path.basename(argv[0]) != "kubectl" or "port-forward" not in argv[1:]:
        return pid + ": not a kubectl port-forward: " + row[3]
    return None


def _runs_computed_command(text):
    """True when a command head in shell ``text`` is a variable or the output
    of a substitution (what ``eval`` does), so the command is not visible."""
    segments = _segments_meta(text)
    for n, (tokens, before, _) in enumerate(segments):
        if _command_head(tokens)[0].startswith("$"):
            return True
        opens = before.replace(" ", "")
        if opens.endswith("`") and (opens == "`" and n == 0 or opens[-2:-1] in tuple(";&|(\n")):
            return True
    return False


def _classify_tokens(tokens, cfg, ctx):
    """Hits for one segment; ``ctx`` carries the raw text, the working
    directory, variables and the raw tokens of the segment (``raw``)."""
    program, args, via_xargs = _command_head(tokens)
    raw_args = ctx.get("raw", tokens)[len(tokens) - len(args):]
    if not program:
        return []
    if program == "rm-own":
        if _is_own_tool(tokens[len(tokens) - len(args) - 1], ctx, "rm-own"):
            return []  # the tool checks each real path itself
        program = "rm"  # another script by that name is judged as rm
    if program == "kill-forward":
        if _is_own_tool(tokens[len(tokens) - len(args) - 1], ctx, "kill-forward"):
            return []  # the tool checks each pid itself
        program = "kill"  # another script by that name is judged as kill
    if program in ("cd", "pushd", "popd"):
        negated = "!" in tokens[:len(tokens) - len(args) - 1]
        _change_dir(program, args, ctx, ctx.get("before", ""), ctx.get("after", ""), negated)
        return []
    if program == "alias" and not any("=" in a for a in args):
        return []  # prints aliases
    if program in ("sudo", "doas", "alias", "apt", "apt-get") or program == "brew" and args[:1] == ["uninstall"]:
        return [("privileged", "privileged package or system command")]
    if program in ("kill", "pkill", "killall"):
        return [("privileged", "kill: stop a job with its own tool or let it finish; stop your own leftover kubectl port-forwards with ~/.claude/tools/kill-forward <pid>...")]
    if program == "chmod":
        operands = _path_args(args)[1:]
        if operands and all(_ours(p, cfg, ctx) for p in operands):
            return [("ours", "chmod of project-owned scratch paths")]
        return [("privileged", "chmod outside the project-owned paths: file modes are not changed by agents; chmod works on absolute paths inside a worktree, <repo>/tmp or /tmp; to delete such paths run ~/.claude/tools/rm-own -r <path>...")]
    if program == "chown":
        return [("privileged", "chown: file owners are not changed by agents")]
    if program == "eval":
        return [("privileged", "eval wrapper: run the command directly")]
    shell_flag = next((a for a in args if re.match(r"^-[a-z]*c[a-z]*$", a)), None)
    if program in _SHELLS and shell_flag:
        i = args.index(shell_flag) + 1
        if i >= len(raw_args):
            return []
        if _runs_computed_command(raw_args[i]):
            return [("privileged", program + " " + shell_flag + " runs a command named by a variable or substitution: run the command directly")]
        return _classify_text(raw_args[i], cfg, ctx["depth"] + 1, ctx["dir"], None, ctx["cd_seen"])
    if program == "rm":
        return [_rm_rule([] if via_xargs else _path_args(args), cfg, ctx, "rm", True)]
    if program == "mv":
        paths = _path_args(args)
        if paths and all(_ours(p, cfg, ctx) for p in paths):
            return [("ours", "mv within project-owned paths")]
        if ctx["dir"] is not None and paths and all(_is_plain_relative(p) for p in paths):
            return []
        return [("approval", "mv outside the project-owned paths; move within a worktree, <repo>/tmp or /tmp by absolute paths, or rename with relative paths; to delete instead run ~/.claude/tools/rm-own -r <path>...")]
    if program == "find":
        exec_rm = any(
            a in ("-exec", "-execdir") and args[i + 1:i + 2] == ["rm"] for i, a in enumerate(args)
        )
        if "-delete" in args or exec_rm:
            start = []
            for a in args:
                if a.startswith(("-", "(", "!")):
                    break
                start.append(a)
            return [_rm_rule(start, cfg, ctx, "find -delete")]
        return []
    if program == "git":
        return _git_class(args, cfg, ctx)
    if program == "kubectl":
        return _kubectl_class(args, cfg, ctx)
    if program == "docker":
        rest = args[1:] if args[:1] == ["compose"] else args
        if rest[:1] == ["exec"]:
            i = _after_options(rest[1:], _DOCKER_VALUE_FLAGS)
            return _classify_inner(rest[1:][i + 1:], cfg, ctx)
        return []
    if program == "ssh":
        i = _after_options(args, _SSH_VALUE_FLAGS)
        return _classify_inner(args[i + 1:], cfg, ctx)
    if program in ("helm",) and args[:1] in (["uninstall"], ["delete"]) or (
        program == "flux" and args[:1] == ["delete"]
    ) or program == "rsync" and any(a.startswith("--delete") for a in args):
        return [("approval", program + " removes live state")]
    if _INLINE_INTERPRETER_RE.match(program):
        code = " ".join(
            args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-c", "-e", "-E")
        )
        return _inline_code_hits(program, code)
    if program in cfg["sql_clients"]:
        keys = [_sql_key(p, ctx) for p in _sql_file_args(args)]
        cfg["sql_paths"].extend(k for k in keys if k is not None)
        return _sql_hits(args, [None if k is None else cfg["sql_files"].get(k) for k in keys])
    if program in ("echo", "printf"):
        target = _redirect_target(args)
        key = target and _sql_key(target, ctx)
        if key:
            cfg["sql_files"][key] = " ".join(args[:args.index(target) - 1])
    return []


def _redirect_target(args):
    """The file a ``> path`` / ``>> path`` redirect writes, else None."""
    for i, a in enumerate(args[:-1]):
        if a in (">", ">>"):
            return args[i + 1]
    return None


def _sql_file_args(args):
    paths = []
    for i, a in enumerate(args):
        if a in _SQL_FILE_FLAGS and i + 1 < len(args):
            paths.append(args[i + 1])
        elif a.startswith("<") and not a.startswith("<<") and len(a) > 1:
            paths.append(a[1:])
        elif a.startswith(("--file=", "--queries-file=")):
            paths.append(a.split("=", 1)[1])
    return paths


def _sql_key(path, ctx):
    """The path a SQL file is known by: as written while no cd ran and no
    working directory is known, else resolved; None for a variable or glob
    path or a relative path whose directory is unknown."""
    if re.search(r"[$*?`{]", path):
        return None
    if not ctx["dir"] and not ctx["cd_seen"] and not path.startswith(("/", "~")):
        return path
    return _resolve(path, ctx)


def sql_file_paths(command, params=None, cwd=None):
    """Paths SQL clients in ``command`` read from, resolved through the cd's
    the command runs (relative ones against ``cwd``); the hook reads them and
    passes their text to ``destructive_decision`` as ``sql_file_texts``."""
    cfg = _decision_cfg(params, None)
    _classify_text(command, cfg, 0, os.path.realpath(cwd) if cwd else "")
    return list(dict.fromkeys(cfg["sql_paths"]))


_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\+?)=(.*)$", re.S)
_NAME_WORD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\+?=|\[|$)")
_VAR_WRITERS = frozenset({
    "declare", "local", "typeset", "export", "readonly", "unset", "read", "getopts",
    "let", "mapfile", "readarray",
})
_KEYWORDS = frozenset({"if", "then", "else", "elif", "while", "until", "do", "!"})
_VAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\x01?")
_BRACED_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_MKTEMP_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)=\"?(?:\$\(\s*mktemp\b[^)`]*\)|`\s*mktemp\b[^)`]*`)\"?")
# A comma brace inside a word: ``a/{b,c}d`` -> ``a/bd a/cd`` (the lexer would
# split it at ``{``, hiding the alternatives from the operand checks).
_BRACE_LIST_RE = re.compile(r"([^\s;&|()<>{}]*)\{([^{}\s]*,[^{}\s]*)\}([^\s;&|()<>]*)")
_BLOCK_OPEN = frozenset({"if", "for", "while", "until", "case", "select"})
_BLOCK_CLOSE = frozenset({"fi", "done", "esac"})


def _new_ctx(dir_, depth, text, variables=None):
    """``dir``: an absolute working directory; "" when none is known and no cd
    ran (relative operands are judged by their text); None when unknown."""
    base = {"HOME": os.path.expanduser("~")}
    base.update(variables or {})
    return {"text": text, "depth": depth, "dir": dir_, "cd_seen": False, "vars": base,
            "parens": [], "brace": 0, "block": 0, "backtick": False}


def _substitute(token, variables):
    out = _VAR_RE.sub(lambda m: variables.get(m.group(1), m.group(0)), token)
    return out.replace("\x01", "")


def _scope(punct, ctx):
    """Apply the grouping characters between two segments: a subshell ``( )``
    or backtick restores the directory, the bindings and the chain state on
    exit, a ``{ }`` group or function body does not."""
    saved = ("dir", "vars", "cd_pending", "chain_or")
    for ch in punct:
        if ch == "(" or ch == "`" and not ctx["backtick"]:
            ctx["parens"].append({k: ctx.get(k) for k in saved})
            ctx["vars"] = dict(ctx["vars"])
            ctx["backtick"] = ctx["backtick"] or ch == "`"
        elif ch == ")" or ch == "`":
            if ctx["parens"]:
                ctx.update(ctx["parens"].pop())
            else:
                ctx["dir"] = None
            if ch == "`":
                ctx["backtick"] = False
        elif ch == "{":
            ctx["brace"] += 1
        elif ch == "}":
            ctx["brace"] = max(0, ctx["brace"] - 1)


def _bind(tokens, ctx, before, after):
    """Bind the ``NAME=<literal>`` words of an unconditional segment that is
    only assignments; every other write to a name (a nested or conditional
    assignment, ``+=``, an assignment prefix of a command, declare/local/
    export/unset/read/getopts...) unbinds it. True when the segment runs no
    command."""
    i = 0
    while i < len(tokens) and tokens[i] in _KEYWORDS:
        i += 1
    words, assigns = tokens[i:], []
    while len(assigns) < len(words) and _ASSIGN_RE.match(words[len(assigns)]):
        assigns.append(_ASSIGN_RE.match(words[len(assigns)]).groups())
    standalone = bool(assigns) and len(assigns) == len(words)
    bind = standalone and _unconditional(ctx, before, after)
    for name, plus, value in assigns:
        value = _substitute(value, ctx["vars"])
        if bind and not plus and not any(ch in value for ch in "$`"):
            ctx["vars"][name] = value
        else:
            ctx["vars"].pop(name, None)
    program, args, _ = _command_head(words[len(assigns):])
    if program in _VAR_WRITERS or program == "printf" and "-v" in args:
        for word in args:
            match = _NAME_WORD_RE.match(word)
            if match:
                ctx["vars"].pop(match.group(1), None)
    return standalone


def _walk(script, cfg, ctx):
    hits, function = [], None
    for tokens, before, after in _segments_meta(script):
        _scope(before, ctx)
        _chain_step(ctx, before)
        for i, word in enumerate(tokens):
            if word in _BLOCK_OPEN:
                ctx["block"] += 1
            if word in ("for", "select"):
                ctx["vars"].pop(tokens[i + 1] if i + 1 < len(tokens) else "", None)
            if word not in _KEYWORDS:
                break
        if tokens[0] in _BLOCK_CLOSE:
            ctx["block"] = max(0, ctx["block"] - 1)
            continue
        if function and "{" in before:
            name, function = function, None
            if _command_head(tokens)[0] == "kubectl" and "$@" in tokens:
                # A kubectl wrapper ``k(){ kubectl --context X "$@"; }``: the
                # definition runs nothing; each call is classed as kubectl.
                cfg["functions"][name] = tokens[:tokens.index("$@")]
                continue
        if len(tokens) == 1 and after.replace(" ", "").startswith("()"):
            function = tokens[0]
            continue
        if tokens[:1] == ["function"] and len(tokens) == 2:
            function = tokens[1]
            continue
        if tokens[0] in cfg["functions"]:
            tokens = cfg["functions"][tokens[0]] + tokens[1:]
        # A quoted substitution runs here, with the directory and variables
        # of this point (an unquoted one is split into segments).
        for m in _INNER_SUBST_RE.finditer(" ".join(tokens)):
            hits.extend(_classify_text(
                m.group(1) or m.group(2) or "", cfg, ctx["depth"] + 1, ctx["dir"],
                dict(ctx["vars"]), ctx["cd_seen"],
            ))
        if _bind(tokens, ctx, before, after):
            continue
        if any(t.startswith("<<") and not t.startswith("<<<") for t in tokens):
            # The next heredoc body in order is what a `> file` redirect here writes.
            body = ctx["heredoc_bodies"].pop(0) if ctx.get("heredoc_bodies") else None
            target = _redirect_target(tokens)
            key = target and body is not None and _sql_key(_substitute(target, ctx["vars"]), ctx)
            if key:
                cfg["sql_files"][key] = body
        # A bound value with whitespace splits into words, as an unquoted
        # expansion does (for a quoted one each word is then judged alone).
        words, raw = [], []
        for t in tokens:
            value = _substitute(t, ctx["vars"])
            split = value.split() if value != t and not any(c.isspace() for c in t) else [value]
            words.extend(split)
            raw.extend([t] * len(split))
        ctx.update(raw=raw, before=before, after=after)
        hits.extend(_classify_tokens(words, cfg, ctx))
    return hits


def _quoted_positions(text):
    """Indexes of characters inside single or double quotes."""
    quoted, quote, escape = set(), None, False
    for i, ch in enumerate(text):
        if escape:
            escape = False
        elif ch == "\\" and quote != "'":
            escape = True
        elif quote and ch == quote:
            quote = None
        elif not quote and ch in "'\"":
            quote = ch
        elif quote:
            quoted.add(i)
    return quoted


def _expand_brace_lists(script):
    """Expand comma braces the shell expands (outside quotes)."""
    for _ in range(8):
        quoted = _quoted_positions(script)
        expanded = _BRACE_LIST_RE.sub(
            lambda m: m.group(0) if m.start(2) - 1 in quoted
            else " ".join(m.group(1) + alt + m.group(3) for alt in m.group(2).split(",")),
            script,
        )
        if expanded == script:
            return script
        script = expanded
    return script


def _classify_text(command, cfg, depth=0, dir_="", variables=None, cd_seen=False):
    """``dir_`` is the working directory the text starts in (see ``_new_ctx``);
    ``cd_seen``: a cd already ran, so relative prefixes no longer count."""
    if depth > 3:
        return [("impossible", "command nesting too deep to classify")]
    ctx = _new_ctx(dir_, depth, command, variables)
    ctx["cd_seen"] = cd_seen
    script, heredocs = _split_heredocs(command)
    ctx["heredoc_bodies"] = [body for _, body, _ in heredocs]
    script = script.replace("\\\n", " ")
    script = _MKTEMP_RE.sub(lambda m: m.group(1) + "=" + _temp_prefixes()[0] + "mktemp", script)
    script = _BRACED_VAR_RE.sub(lambda m: "$" + m.group(1) + "\x01", script)
    script = _expand_brace_lists(script)
    hits = _walk(script, cfg, ctx)
    # Heredoc shells start where the command started unless a cd ran
    # somewhere in it: which cd applies to them is not tracked.
    inner_dir = dir_ if not ctx["cd_seen"] else None
    for line, body, quoted in heredocs:
        if not quoted:
            for m in _INNER_SUBST_RE.finditer(body):
                hits.extend(_classify_text(m.group(1) or m.group(2) or "", cfg, depth + 1, inner_dir))
        hits.extend(_heredoc_hits(line, body, cfg, depth, inner_dir))
    return hits


def _split_heredocs(command):
    """(script without heredoc bodies, [(opening line, body, delimiter quoted)])."""
    lines = command.split("\n")
    script, heredocs, i = [], [], 0
    while i < len(lines):
        line = lines[i]
        script.append(line)
        i += 1
        for m in _HEREDOC_RE.finditer(line):
            body = []
            while i < len(lines) and lines[i].strip() != m.group(2):
                body.append(lines[i])
                i += 1
            i += 1
            heredocs.append((line, "\n".join(body), bool(m.group(1))))
    return "\n".join(script), heredocs


def _heredoc_hits(line, body, cfg, depth, dir_):
    """A heredoc body is data unless it feeds a shell, an interpreter, a remote
    shell or a SQL client; then it is classed as what that head runs. Every
    segment of the opening line counts: ``cat <<EOF | bash`` feeds bash."""
    for tokens in _shell_segments(line):
        program, args, _ = _command_head(tokens)
        if program == "kubectl" and "exec" in args or program == "docker" and "exec" in args:
            program = "ssh"
        if program in _SHELLS:
            return _classify_text(body, cfg, depth + 1, dir_)
        if program == "ssh":
            return _classify_text(body, cfg, depth + 1, None, None, True)
        if _INLINE_INTERPRETER_RE.match(program) and _inline_code_hits(program, body):
            return _inline_code_hits(program, body)
        if program in cfg["sql_clients"]:
            return _sql_hits([body])
    return []


_DECISION = {"ours": "allow", "approval": "ask", "impossible": "deny", "privileged": "deny"}
_SEVERITY = {"ours": 1, "approval": 2, "impossible": 3, "privileged": 3}


def _plan_decision(path, marker_hash, agent_type, plan_text, plan_hash, cfg):
    def deny(reason):
        return {"decision": "deny", "class": "approval", "reason": reason}

    if agent_type:
        return deny("only the main session runs a destructive plan: return the plan path to main")
    if not path.startswith(("/", "~/")):
        return deny("plan path must be absolute")
    if plan_text is None:
        return deny("plan file " + path + " is missing or unreadable")
    if (plan_hash or "").lower() != marker_hash:
        return deny("plan hash mismatch: rewrite the marker after editing the plan")
    lines = plan_text.splitlines()
    headers = {}
    for header in _PLAN_HEADERS:
        found = [l for l in lines if l.strip().lower().startswith(header)]
        if not found:
            return deny(
                "plan header line `" + header + " ...` is missing; required: "
                + " ".join(_PLAN_HEADERS)
            )
        headers[header] = found[0].strip()
    max_lines, max_bytes = int(cfg["plan_max_lines"]), int(cfg["plan_max_bytes"])
    if len(lines) > max_lines or len(plan_text.encode("utf-8")) > max_bytes:
        return deny(
            "plan is over %d lines or %d bytes: split the plan" % (max_lines, max_bytes)
        )
    for number, line in enumerate(lines, 1):
        if _PLAN_INDIRECT_RE.match(line):
            return deny(
                "plan line %d runs another file: inline it, the owner approves only what is shown" % number
            )
    summary = "Destructive plan " + path + " | " + " | ".join(headers[h] for h in _PLAN_HEADERS)
    return {"decision": "ask", "class": "approval", "reason": summary + "\n\n" + plan_text}


_APPROVALS_READERS = frozenset({"cat", "ls", "head", "tail", "stat", "test", "wc", "file", "grep", "rg"})


_APPROVE_LINE_RE = re.compile(r"^[ \t]*APPROVE[ \t]+([0-9a-fA-F]{64})[ \t]*$", re.IGNORECASE | re.MULTILINE)
_APPROVE_TOKEN_RE = re.compile(r"APPROVE\s+[0-9a-f]{64}(?![0-9a-f])", re.IGNORECASE)
_APPROVALS_DENY = {"decision": "deny", "class": "privileged",
                   "reason": "the approvals directory is written only by the owner's prompt"}


def approved_hashes(prompt):
    """Lowercase plan hashes the owner approves: each line that is exactly
    `APPROVE <64 hex>` (APPROVE case-insensitive, surrounding blanks allowed)."""
    return [h.lower() for h in _APPROVE_LINE_RE.findall(prompt or "")]


def _expand_home(path, home):
    return os.path.join(home, path[2:]) if path == "~" or path.startswith("~/") else path


def approvals_root(params, home):
    """Expanded, resolved approvals directory for ``home``."""
    return os.path.realpath(_expand_home(_merged_params(params)["destructive"]["approvals_dir"], home))


def _writes_approvals(command, cfg):
    """True when a token names the approvals dir (case-folded, normalized)
    outside a plain read, or the command carries an APPROVE line."""
    if _APPROVE_TOKEN_RE.search(command):
        return True
    name = os.path.basename(os.path.normpath(cfg["approvals_dir"])).casefold()
    hit = False
    for tokens in _shell_segments(command):
        if any(name in os.path.normpath(t).casefold().split("/") for t in tokens):
            if re.search(r"[<>]", command) or _command_head(tokens)[0] not in _APPROVALS_READERS:
                return True
            hit = True
    return hit and bool(re.search(r"[<>]", command))


def approval_path_decision(file_path, home, params=None):
    """deny when a file-tool path resolves under the approvals dir, else None.
    Compared case-folded: the volume may be case-insensitive."""
    root = approvals_root(params, home).casefold()
    target = os.path.realpath(_expand_home(file_path, home)).casefold()
    if target == root or target.startswith(root + os.sep):
        return dict(_APPROVALS_DENY)
    return None


def destructive_decision(
    command, agent_type, plan_text, plan_hash, params=None, permission_mode="",
    sql_file_texts=None, approved=False, cwd=None,
):
    """Class and permission decision for one Bash command.

    ``plan_text``/``plan_hash`` are the content and sha256 hex of the file the
    DESTRUCTIVE_PLAN marker names (None when there is no readable file). The
    plan's inner commands are not re-inspected: the owner reads them in the
    prompt. ``permission_mode`` is the hook input field; whether a hook ``ask``
    prompts in bypassPermissions mode is undocumented, so there it becomes a
    deny unless ``destructive.ask_in_bypass``. ``sql_file_texts`` maps each
    path of ``sql_file_paths(command)`` to its text, None when unreadable.
    ``approved`` says whether the owner's prompt approved this plan hash in
    this session (plan-approval.py); an approved valid plan is allowed once.
    ``cwd`` is the hook input's working directory; relative operands resolve
    against it and the cd's the command runs. A malformed params value raises.
    Returns {"decision": allow|ask|deny, "class", "reason"}.
    """
    cfg = _decision_cfg(params, sql_file_texts)
    if len((command or "").encode("utf-8", "surrogatepass")) > int(cfg["command_max_bytes"]):
        return {"decision": "deny", "class": "impossible",
                "reason": "command too long for the destructive gate"}
    if _writes_approvals(command or "", cfg):
        return dict(_APPROVALS_DENY)
    marker = plan_marker(command)
    if marker:
        result = _plan_decision(marker[0], marker[1], agent_type, plan_text, plan_hash, cfg)
        if result["decision"] == "ask" and approved:
            return {"decision": "allow", "class": "approval",
                    "reason": "plan " + marker[1][:12] + " approved by owner"}
        if result["decision"] == "ask" and permission_mode == "bypassPermissions" and not cfg["ask_in_bypass"]:
            return {"decision": "deny", "class": "approval", "reason": (
                "this session cannot prompt; give the owner the plan path " + marker[0] + " to read and ask them to reply "
                "with the line: APPROVE " + marker[1] + "\nthen run the same command again"
            )}
    else:
        result = _command_decision(command or "", cfg, os.path.realpath(cwd) if cwd else "")
    if (
        result["decision"] == "ask"
        and permission_mode == "bypassPermissions"
        and not cfg["ask_in_bypass"]
    ):
        return {"decision": "deny", "class": result["class"], "reason": (
            result["reason"] + "; this session runs in bypass mode and cannot prompt "
            "(destructive.ask_in_bypass=false): give the owner the plan path and ask for the line APPROVE <sha256>"
        )}
    return result


def _decision_cfg(params, sql_file_texts):
    cfg = dict(_merged_params(params)["destructive"])
    cfg.update(
        sql_files=dict(sql_file_texts or {}), sql_paths=[], functions={},
        ours_paths=list(cfg["ours_paths"]) + _temp_prefixes() + [
            os.path.realpath(os.path.expanduser(p)) + "/"
            for p in cfg["ours_paths"] if p.startswith(("/", "~"))
        ],
    )
    return cfg


def _command_decision(command, cfg, cwd):
    found = _classify_text(command, cfg, 0, cwd)
    if not found:
        return {"decision": "allow", "class": "none", "reason": ""}
    cls, reason = max(found, key=lambda hit: _SEVERITY[hit[0]])
    if cls == "approval":
        reason += ": batch it into a plan script (# scope / # undo / # count / # contradiction / # prod_position / # way_back) run from the main session"
    elif cls == "impossible":
        reason += ("; if the step is needed, write a plan script stating exactly what it does"
                   " (# scope / # undo: none / # count / # contradiction / # prod_position / # way_back),"
                   " give the owner its path and ask for the line APPROVE <sha256>, then run it from the main"
                   " session as bash <plan>  # DESTRUCTIVE_PLAN sha256=<hex>")
    return {"decision": _DECISION[cls], "class": cls, "reason": reason}
