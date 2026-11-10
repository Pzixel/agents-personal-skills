"""Pure decision policy for the no-poll and main-budget PreToolUse gates.

Convention: ``cmd_or_path`` is the Bash ``command`` for Bash calls, and the bare
target path (``file_path``/``path``/``pattern``) for Read/Grep/Glob calls; a
bare path is recognised by having no whitespace and starting with ``/``, ``~``
or ``./``.

No I/O of any kind lives here: every function takes facts as values and returns
a complete decision dict.
"""

import fnmatch
import datetime
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


# The orch plugin's agent types, read from its agents/ directory next to this
# file; the model spawns them only as `orch:<type>`, so every text it reads
# names them that way. Built-in types (`claude-code-guide`) stay bare.
_ORCH_AGENTS_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "agents")
ORCH_AGENTS = frozenset(n[:-3] for n in os.listdir(_ORCH_AGENTS_DIR) if n.endswith(".md"))


def agent_ref(type_name):
    """The name the model spawns ``type_name`` by: ``orch:<type>`` for an orch type."""
    return "orch:" + type_name if type_name in ORCH_AGENTS else type_name


_PLAIN_WORD = re.compile(r"^[^\s;&|()<>`\\'\"]*$")


def _strip_quotes(cmd, keep_words=False):
    """Blank out single/double-quoted spans so their contents cannot be read as
    shell structure (``grep -r "sleep" .`` must not look like a sleep call).
    With ``keep_words``, a span that is one plain word (`"$PID"`, `'s2_chain'`)
    keeps its text, since it cannot change the structure."""
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
                quote, opened = ch, i + 1
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
            if keep_words and _PLAIN_WORD.match(cmd[opened:i]):
                out[-1] = cmd[opened:i]
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
    "state is terminal, or an `orch:watcher` agent, or background wait-for only for a few "
    "minutes (the harness kills background Bash tasks with exit 144 at its own events, "
    "and a killed task reports nothing). A wait until a clock time, or longer than one "
    "Monitor (a deploy window hours away), is one `CronCreate` one-shot at that time, "
    "named in the reply with its id. Escape hatch: a line "
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


# An experiment left running past the call: a script or binary of the session
# whose result is read and judged later. It is only worth starting if its
# outcomes lead to different steps (CASE-58: a fine-tune relaunched after it
# had already failed the gate). Servers, tunnels and watchers are
# infrastructure, not runs.
_DETACH_HEADS = frozenset({"nohup", "setsid", "disown"})
# Programs that run the command given after their options, with the options
# that take a separate value (`timeout` also takes its duration). `rtk`,
# `poetry` and `uv` count only before their subcommand from _WRAPPERS.
_LAUNCH_WRAPPERS = {
    "nohup": (), "setsid": (), "disown": (), "caffeinate": ("-t", "-w"),
    "timeout": ("-s", "--signal", "-k", "--kill-after"), "nice": ("-n", "--adjustment"),
    "ionice": ("-c", "-n", "-p"), "sudo": ("-u", "-g", "-C", "-D", "-h", "-p", "-U", "-r", "-t"),
    "stdbuf": ("-i", "-o", "-e"), "env": ("-u", "-C", "--unset", "--chdir"), "time": ("-f", "-o"),
    "exec": ("-a",), "command": (), "builtin": (), "rtk": (),
    "poetry": ("-C", "--directory", "-P", "--project"),
    "uv": ("-p", "--python", "--with", "--with-requirements", "--with-editable", "--project",
           "--directory", "--group", "--extra", "--env-file", "--index", "--package", "--from"),
}
_INTERPRETER_VALUE_OPTIONS = frozenset(
    {"-W", "-X", "-o", "-O", "+o", "+O", "-r", "--require", "--import", "--loader"})
_INLINE_CODE_OPTIONS = frozenset({"-m", "-c", "-e", "--eval"})
_JOB_ENDERS = frozenset({"kill", "pkill", "killall", "wait"})
_WAIT_PROGRAMS = frozenset(
    {"sleep", "wait-for", "tail", "grep", "test", "[", "[[", "true", "false", ":",
     "break", "echo", "pgrep", "date"}
)
_SCRIPT_INTERPRETERS = re.compile(r"^(python[0-9.]*|bash|sh|zsh|node)$")
_SCRIPT_SUFFIX = re.compile(r"\.(py|sh|js|mjs|ts)$")
_SERVICE_PROGRAMS = frozenset(
    {"vite", "next", "npm", "npx", "pnpm", "yarn", "kubectl", "ssh", "docker", "docker-compose",
     "tail", "caddy", "nginx", "uvicorn", "gunicorn", "serve"}
)
_NOT_SCRIPTS = frozenset({"wait-for", "check-run"})
_SYSTEM_DIRS = ("/usr/", "/bin/", "/sbin/", "/opt/homebrew/")
_OUTCOMES_RE = re.compile(r"^#\s*OUTCOMES:(.*)$")
# `2>&1`, `&>f`, `<&3`: an ampersand inside a redirection is not a separator.
_REDIRECT_AMP = re.compile(r"(?<=[<>])&|&(?=>)")
_REDIRECTION = re.compile(r"^\d*(>>|>\||>|<<<|<<|<)(.*)$")
_COMMAND_OPS = ("&&", "||", "|&", ";;", "&", "|", ";", "\n", "(", ")")
_OPENERS = frozenset({"while", "until", "for", "select", "if", "{"})
_CLOSERS = frozenset({"done", "fi", "}"})
_JOB_REF = re.compile(r"^\$(!|\{!\})$")
_OUTCOMES_LINE = "# OUTCOMES: <outcome> -> <step>; <outcome> -> <step>"


def _shell_tokens(script):
    """(text, position, is_operator) of a quote-stripped script; `$(...)`,
    `${...}`, `<(...)` and `>(...)` stay inside their word."""
    out, i, n = [], 0, len(script)
    while i < n:
        if script[i] in " \t":
            i += 1
            continue
        op = next((o for o in _COMMAND_OPS if script.startswith(o, i)), None)
        if op:
            out.append((op, i, True))
            i += len(op)
            continue
        start, depth = i, 0
        while i < n:
            if script[i] in "$<>" and script[i + 1:i + 2] in ("(", "{"):
                depth, i = depth + 1, i + 2
            elif depth and script[i] in "({":
                depth, i = depth + 1, i + 1
            elif depth and script[i] in ")}":
                depth, i = depth - 1, i + 1
            elif not depth and script[i] in " \t\n;&|()":
                break
            else:
                i += 1
        out.append((script[start:i], start, False))
    return out


def _commands(script):
    """Simple commands of a script: words, end position, ``bg`` (position of
    the `&` that backgrounds the statement holding it, else None) and ``cond``
    (the condition of a while/until loop). A compound command (loop, if,
    subshell, brace group) belongs whole to the statement it stands in."""
    commands, frames, words, cond = [], [([], [])], [], [False]

    def close(position):
        if words:
            frames[-1][0].append(len(commands))
            frames[-1][1].append(len(commands))
            commands.append({"words": words[:], "end": position, "bg": None, "cond": cond[0]})
            del words[:]
        cond[0] = False

    def pop():
        if len(frames) > 1:
            members = frames.pop()[1]
            frames[-1][0].extend(members)
            frames[-1][1].extend(members)

    for text, pos, is_op in _shell_tokens(script):
        if not is_op:
            if words or text not in _OPENERS | _CLOSERS | {"do", "then", "else", "elif", "!"}:
                words.append(text)
            elif text in _CLOSERS:
                pop()
            elif text in _OPENERS:
                frames.append(([], []))
                cond[0] = text in ("while", "until")
            continue
        close(pos)
        if text == "(":
            frames.append(([], []))
        elif text == ")":
            pop()
        elif text in (";", "\n", ";;", "&"):
            if text == "&":
                for idx in frames[-1][0]:
                    if commands[idx]["bg"] is None:
                        commands[idx]["bg"] = pos
            del frames[-1][0][:]
    close(len(script))
    return commands


def _launch_head(words):
    """(tokens from the launched program on, detached): env assignments,
    redirections, shell keywords and wrappers that run the command after them
    (nohup, setsid, caffeinate, timeout, sudo, `rtk proxy`, `uv run` ...) removed."""
    tokens, detached, i = list(words), False, 0
    while i < len(tokens):
        tok = tokens[i]
        prog = os.path.basename(tok)
        redirection = _REDIRECTION.match(tok)
        if redirection:
            i += 1 if redirection.group(2) else 2
            continue
        if _WRAPPERS.get(prog) and tokens[i + 1:i + 2] and tokens[i + 1] in _WRAPPERS[prog]:
            i += 2
        elif prog in _LAUNCH_WRAPPERS and not _WRAPPERS.get(prog):
            detached = detached or prog in _DETACH_HEADS
            i += 1
        elif _ENV_ASSIGN.match(tok) or tok.lower() in _SHELL_KEYWORDS:
            i += 1
            continue
        else:
            break
        values = _LAUNCH_WRAPPERS.get(prog, ())
        while i < len(tokens) and (tokens[i].startswith("-") or _ENV_ASSIGN.match(tokens[i])):
            i += 2 if tokens[i] in values else 1
        if prog == "timeout":
            i += 1
    head = tokens[i:]
    return head, detached or _program(head) in ("tmux", "screen")


def _is_script_path(token):
    return ("/" in token or bool(_SCRIPT_SUFFIX.search(token))) and not token.startswith(_SYSTEM_DIRS)


def _runs_script(tokens):
    """True when the launched command runs a script or binary of the session:
    an interpreter given a file path, or a path to the file itself."""
    prog = _program(tokens)
    if not tokens or prog in _SERVICE_PROGRAMS or prog in _NOT_SCRIPTS or "node_modules/" in tokens[0]:
        return False
    if prog in ("tmux", "screen"):
        return any(_runs_script(_launch_head(tokens[i:])[0])
                   for i in range(1, len(tokens)) if not tokens[i].startswith("-"))
    if not _SCRIPT_INTERPRETERS.match(prog):
        return _is_script_path(tokens[0])
    i = 1
    while i < len(tokens) and tokens[i].startswith(("-", "+")):
        if tokens[i] in _INLINE_CODE_OPTIONS:
            return False
        i += 2 if tokens[i] in _INTERPRETER_VALUE_OPTIONS else 1
    return i < len(tokens) and not _REDIRECTION.match(tokens[i]) and _is_script_path(tokens[i])


def _is_pure_wait(commands):
    """Only waiting: wait programs, at least one of them a sleep, wait-for or
    `tail -f`; a loop condition may be a script of the session (`until ./check.sh`)."""
    programs = []
    for command in commands:
        head = _launch_head(command["words"])[0]
        if not head or (command["cond"] and _runs_script(head)):
            continue
        prog = _program(head)
        if prog not in _WAIT_PROGRAMS:
            return False
        if prog == "tail" and not any(t in ("-f", "-F") for t in head[1:]):
            prog = "tail-static"
        programs.append(prog)
    return any(p in ("sleep", "wait-for", "tail") for p in programs)


def _detached_launches(commands):
    """[(launch position, command indices)] of the script or binary runs the
    call leaves going: a statement backgrounded with `&` (every command of its
    pipeline, subshell, brace group or loop), or one under nohup/setsid/tmux."""
    statements = {}
    for idx, command in enumerate(commands):
        if command["bg"] is not None:
            statements.setdefault(command["bg"], []).append(idx)
        elif _launch_head(command["words"])[1]:
            statements.setdefault(command["end"], []).append(idx)
    return [
        (pos, idxs) for pos, idxs in sorted(statements.items())
        if not _is_pure_wait([commands[i] for i in idxs])
        and any(_runs_script(_launch_head(commands[i]["words"])[0]) for i in idxs)
    ]


def _ends_launch(commands, pos, idxs):
    """True when a foreground kill, pkill, killall or wait after the launch at
    ``pos`` targets that job: `$!` or a variable assigned from it while it is
    the latest background job, a job spec, a bare wait, or a pkill pattern
    found in the launched command."""
    jobs = sorted({c["bg"] for c in commands if c["bg"] is not None})

    def latest_job(at):
        return max((j for j in jobs if j < at), default=None)

    names = {}
    text = " ".join(" ".join(commands[i]["words"]) for i in idxs)
    for command in commands:
        for word in command["words"][:1]:
            name, eq, value = word.partition("=")
            if eq and _JOB_REF.match(value):
                names[name] = latest_job(command["end"])
        head = _launch_head(command["words"])[0]
        prog = _program(head)
        if command["bg"] is not None or command["end"] <= pos or prog not in _JOB_ENDERS:
            continue
        options = [a for a in head[1:] if a.startswith("-")]
        args = [a for a in head[1:] if not a.startswith("-")]
        if prog == "wait" and not args:
            return True
        if prog in ("pkill", "killall"):
            if args and _pattern_in(args[-1], text):
                return True
            continue
        if prog == "kill" and ("-0" in options or "0" in args[:1]):
            continue
        for arg in args:
            ref = arg.lstrip("$").strip("{}")
            if arg.startswith("%") or "jobs" in arg:
                return True
            if (_JOB_REF.match(arg) and latest_job(command["end"]) == pos) or (
                    arg.startswith("$") and names.get(ref) == pos):
                return True
    return False


def _pattern_in(pattern, text):
    try:
        return re.search(pattern, text) is not None
    except re.error:
        return pattern in text


def _outcome_steps(command):
    """Steps of the `# OUTCOMES: o -> s; o -> s` line; pairs missing a side do not count."""
    for line in (command or "").splitlines():
        match = _OUTCOMES_RE.match(line.strip())
        if not match:
            continue
        steps = []
        for pair in match.group(1).split(";"):
            outcome, arrow, step = pair.partition("->")
            if arrow and outcome.strip() and step.strip():
                steps.append(" ".join(step.lower().split()))
        return steps
    return []


def _outcomes_commands(command):
    script, _ = _split_heredocs(command or "")
    script = "\n".join(l for l in script.split("\n") if not l.lstrip().startswith("#"))
    return _commands(_REDIRECT_AMP.sub(" ", _strip_quotes(script, keep_words=True)))


def is_pure_wait(command):
    """True when the whole command only waits (wait-for, a bare sleep, tail -f,
    or a sleep loop whose condition runs a script of the session): its own exit
    is the event a caller waits for."""
    return _is_pure_wait(_outcomes_commands(command))


def outcomes_decision(command, run_in_background, params=None):
    """A script or binary of the session left running past the call (detached,
    or the whole call in the background) must name, in a `# OUTCOMES:` line, at
    least ``outcomes_min_pairs`` outcomes leading to at least two different
    steps. Exempt: a background call that is only a wait, and a launch that a
    later kill or wait of the same call stops or awaits. No override marker
    exempts."""
    commands = _outcomes_commands(command)
    launches = _detached_launches(commands)
    open_launches = [(pos, idxs) for pos, idxs in launches if not _ends_launch(commands, pos, idxs)]
    ended = {i for _, idxs in launches for i in idxs} - {i for _, idxs in open_launches for i in idxs}
    runs = [" ".join(_launch_head(c["words"])[0]) for i, c in enumerate(commands)
            if (open_launches and i in open_launches[0][1]) or (not open_launches and i not in ended)
            if _runs_script(_launch_head(c["words"])[0])]
    if open_launches:
        trigger = "detach"
    elif run_in_background and runs and not _is_pure_wait(commands):
        trigger = "background"
    else:
        return {"decision": "allow", "reason": "", "trigger": "", "launch": ""}
    steps = _outcome_steps(command)
    if len(steps) < int(_merged_params(params)["outcomes_min_pairs"]):
        reason = (
            "This command starts a script or binary of the session whose result arrives after "
            "the call ({}: `{}`). Name each outcome of the run and the step it leads to in a "
            "line `{}` (at least two outcomes). A run whose every outcome leads to the same "
            "step is not run: report the fact already in hand instead.".format(
                trigger, runs[0], _OUTCOMES_LINE)
        )
    elif len(set(steps)) < 2:
        reason = (
            "Every outcome in the `# OUTCOMES:` line leads to the same step ({}). A run whose "
            "every outcome leads to the same step is not run: take that step now, or report the "
            "fact already in hand instead.".format(steps[0])
        )
    else:
        return {"decision": "allow", "reason": "", "trigger": trigger, "launch": runs[0]}
    return {"decision": "deny", "reason": reason, "trigger": trigger, "launch": runs[0]}


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
    "reading to an `orch:scout` or `orch:prod-probe` subagent that carries a cheap context and "
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


_WAIT_SHOWS_LINE = re.compile(r"^[ \t]*#?[ \t]*WAIT_SHOWS:[ \t]*(.*)$", re.MULTILINE)
_WAIT_SHOWS_NULL = {"nothing", "none", "-", "n/a"}
_WAIT_SHOWS_WHERE = {
    "CronCreate": "to the CronCreate prompt",
    "Monitor": "as a `# WAIT_SHOWS:` line in the Monitor command or description",
    "Bash": "as a `# WAIT_SHOWS:` line in the Bash command",
}
_WAIT_SHOWS_MISSING = (
    'Refused by wait-shows-gate: this wait is armed {hours:.1f} h ahead as a check. Behaviour is proven by a pure function on real input; a wait for an event is the rare exception, proven at the call: add a line `WAIT_SHOWS: <the fact only this event produces; why one pure function on stored data cannot answer it; why the refactor that would make it answerable is impossible or beyond the budget>`'
    " {where}. "
    "If a pure function can answer it, do not arm the wait: run it on today's rows now, read-only, and report that as the check; the event may still be observed in the background. (Retro 2026-11-05: a 16 h one-shot on the next digest send was armed while the digest was a function of stored rows and took 3 minutes to compute.)"
)
_WAIT_SHOWS_NOTHING = (
    "Refused by wait-shows-gate: WAIT_SHOWS is nothing, so a pure function on stored data answers it: do not arm the wait; run that function on today's rows now, read-only, and report the result as the check. Arm a background observation only with a fact it produces that no pure function can (a delivery path, a first run of new wiring), with the refactor cost that rules out making it pure."
)


def _cron_horizon_minutes(cron, now):
    """Minutes from ``now`` to the next fire of a one-shot ``m h dom mon dow``
    cron in local time; ``*`` in dom or mon is today's value; a time not after
    ``now`` rolls to the next day. None when the string is not of that form."""
    fields = (cron or "").split()
    if len(fields) != 5:
        return None
    try:
        minute, hour = int(fields[0]), int(fields[1])
        day = now.day if fields[2] == "*" else int(fields[2])
        month = now.month if fields[3] == "*" else int(fields[3])
        fire = now.replace(month=month, day=day, hour=hour, minute=minute,
                           second=0, microsecond=0)
    except ValueError:
        return None
    if fire <= now:
        fire += datetime.timedelta(days=1)
    return (fire - now).total_seconds() / 60.0


def wait_shows_decision(tool_name, tool_input, now, params=None):
    """A deferred wait armed ``wait_shows_min_minutes`` or more ahead (a
    one-shot CronCreate, a Monitor timeout, a pure Bash wait-for) must carry a
    ``WAIT_SHOWS:`` line with the exception's proof (the fact only that event
    produces, why no pure function on stored data answers it, why the refactor
    is impossible or over budget); a null answer is refused too. Unparseable horizons are allowed (fail open)."""
    tool_input = tool_input or {}
    if tool_name == "CronCreate":
        if tool_input.get("recurring"):
            return {"decision": "allow"}
        minutes = _cron_horizon_minutes(tool_input.get("cron"), now)
        text = tool_input.get("prompt") or ""
    elif tool_name == "Monitor":
        try:
            minutes = float(tool_input.get("timeout_ms")) / 60000.0
        except (TypeError, ValueError):
            minutes = None
        text = (tool_input.get("command") or "") + "\n" + (tool_input.get("description") or "")
    elif tool_name == "Bash":
        text = tool_input.get("command") or ""
        seconds = _wait_for_max(text) if is_pure_wait(text) else None
        minutes = None if seconds is None else seconds / 60.0
    else:
        return {"decision": "allow"}
    if minutes is None or minutes < float(_merged_params(params)["wait_shows_min_minutes"]):
        return {"decision": "allow"}
    match = _WAIT_SHOWS_LINE.search(text)
    if not match or not match.group(1).strip():
        return {"decision": "deny", "reason": _WAIT_SHOWS_MISSING.format(
            hours=minutes / 60.0, where=_WAIT_SHOWS_WHERE[tool_name])}
    if match.group(1).strip().lower() in _WAIT_SHOWS_NULL:
        return {"decision": "deny", "reason": _WAIT_SHOWS_NOTHING}
    return {"decision": "allow"}


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
    "plan_simplify": {"fan_out_min_words": 1200, "fan_out_min_agents": 2},
    "work_agent_types": ["builder", "reviewer"],
    "context_soft": 150000,
    "context_hard": 220000,
    "cost_ceiling_usd": {"default": 6, "builder": 25},
    # Types absent from both work_agent_types and call_caps are uncapped.
    "call_caps": {
        "scout": {"soft": 12, "hard": 18},
        "prod-probe": {"soft": 25, "hard": 40},
    },
    # A `sleep` shorter than this paces requests; this long or longer is a poll.
    "poll_sleep_min_seconds": 5,
    # A detached or background script run names at least this many outcomes in `# OUTCOMES:`.
    "outcomes_min_pairs": 2,
    "wait_shows_min_minutes": 60,
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
        "main": {
            "allow": [
                "builder",
                "reviewer",
                "scout",
                "prod-probe",
                "watcher",
                "claude-code-guide",
            ],
            "deny": {
                "Plan": (
                    "`Plan` is the harness's generic architect and skips the plan"
                    " contract (spec note, premises, lenses). Write the plan in the"
                    " main session with the `superpowers-writing-plans-psilon`"
                    " skill; spawn `orch:scout` for code facts and `orch:reviewer`"
                    " for the /simplify lenses."
                ),
                "Explore": (
                    "`Explore` is the harness's generic explorer without the"
                    " bounded 8-question brief; spawn `orch:scout` for code facts."
                ),
            },
        },
        "builder": ["scout"],
        "reviewer": ["scout"],
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
    # Loop-cost gate (loop_cost_decision): minutes waited on long Bash
    # commands per repository. Set from no prior default; the backtest
    # numbers are in ORIGINS.md.
    "loop_cost": {
        "long_command_min_seconds": 120,
        "window_seconds": 21600,
        "cumulative_budget_seconds": 1800,
        "cumulative_own_runs": 2,
        "repeat_count": 3,
        "compact_stale_rows": 200,
    },
    # Headroom gate (headroom_decision): memory a process started by kubectl
    # exec adds against the container's free memory. Assumed values; see the
    # _doc in orchestrate-params.json and ORIGINS.md.
    "headroom": {
        "jvm_overhead_bytes": 201326592,
        "process_floor_bytes": 33554432,
        "near_miss_factor": 2,
        "read_timeout_seconds": 3.5,
        "jvm_launchers": ["java", "kafka-*.sh", "zookeeper-*.sh"],
    },
    "destructive": {
        "ours_paths": ["/tmp/", "tmp/", ".worktrees/", "~/.claude/tmp/", "~/Library/Caches/"],
        "ours_labels": ["orch.session"],
        "sql_clients": ["psql", "mysql", "sqlite3", "clickhouse-client", "clickhouse", "clickhouse-local", "mongosh"],
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


_PLAN_REVIEW_MISSING = (
    "run /simplify on the plan (reuse, simplification, efficiency, altitude, "
    "premises), write the findings to %s after the last plan rewrite, rework the "
    "plan from them"
)
_LENS_PROMPT = (
    "through a lens agent whose first message carries the full path `%s` and the "
    "literal `/simplify`; it writes %s, an empty file when it finds nothing"
)
_DESIGN_REVIEW_MISSING = (
    "run /simplify on the design (reuse, simplification, efficiency, altitude, "
    "premises) " + _LENS_PROMPT + ", after the last DESIGN.md change"
)
_HANDOFF_REVIEW_MISSING = (
    "%s is missing: a handoff builder at the design stage is spawned only after "
    "step 2 ran on the design; run /simplify on it " + _LENS_PROMPT + ", and put "
    "both findings sets (review-design.md, DESIGN.simplify.md) into the handoff prompt"
)
_PLAN_FAN_OUT_MISSING = (
    "the %s has %d words, at or above plan_simplify.fan_out_min_words = %d, so "
    "/simplify needs at least %d lens agents spawned by the main session, each "
    "lens prompt naming the path `%s`; found %d agents at spawn depth 1 "
    "naming it. Spawn the lens reviewers from the main session (`orch:reviewer`, "
    "one lens or lens pair each, in one message); a subagent may not fan out"
)
_GO_AHEAD = re.compile(r"\Adesign reviewed\b.*proceed to code", re.I | re.S)
_BUILDER_TYPE = re.compile(r"^(orch:)?builder$")
_WORKSPACE_LINE = re.compile(r"^workspace:\s*(\S+)", re.M)


def plan_review_path(plan):
    return plan[: -len(".md")] + ".simplify.md"


def design_gate_target(tool, tool_input, recipient_type=None, recipient_prompt=""):
    """Which builder design a tool call moves into code: (DESIGN.md path, kind) or None.

    - SendMessage to a builder whose trimmed message starts with `design
      reviewed` and contains `proceed to code` (any case; the orchestrate
      template's go-ahead, so a message that only mentions the phrase passes);
      the builder is recipient_type (`builder` or `orch:builder`), recipient_prompt its
      first user message: kind "design", the simplify pass must be fresh.
    - Agent spawning a builder whose prompt carries `handoff:`: kind "handoff",
      the simplify file must exist (the builder edits DESIGN.md after the pass).
    The design is `<workspace>/orch-notes/DESIGN.md`, `<workspace>` the first
    token of the builder prompt's `workspace:` line; no such line: None."""
    if tool == "SendMessage":
        if not _GO_AHEAD.search((tool_input.get("message") or "").strip()):
            return None
        builder, prompt, kind = recipient_type, recipient_prompt, "design"
    elif tool == "Agent":
        prompt = tool_input.get("prompt") or ""
        if "handoff:" not in prompt:
            return None
        builder, kind = tool_input.get("subagent_type"), "handoff"
    else:
        return None
    match = _WORKSPACE_LINE.search(prompt or "")
    if not _BUILDER_TYPE.match(builder or "") or not match:
        return None
    return os.path.join(match.group(1), "orch-notes", "DESIGN.md"), kind


# The three opening lines of a plan the owner approves. The labels are the contract between the plan skill
# and this gate; the values are in whatever language the owner reads.
PLAN_HEAD_LINES = 6
_PLAN_MARKS = "*_`>#- \t"
_PLACEHOLDER_RE = re.compile(r"^(?:\[[^\]]*\]|<[^>]*>)$")
_CONTINUATION_RE = re.compile(r"^(?:\s+\S|[-*+] |\d+[.)] )")
_PLAN_LINES_REASON = (
    "Refused by plan-simplify-gate: the plan must open, within its first %d head lines (non-blank lines that "
    "are not the list or indented lines under a label), with three plain lines the owner reads before "
    "approving. Missing or empty: {missing} Keep the labels exactly as written, in English; write the content "
    "in the language of your replies to the owner. A value that is only markdown marks or wholly a bracketed "
    "placeholder counts as empty. Then call ExitPlanMode again."
) % PLAN_HEAD_LINES
# key -> (label, property the refusal names); the plan skill carries the same property text.
_PLAN_LINE_PROPERTY = {
    "task": ("task", "a line `Task:` followed by what is made and what the requester will do with the result;"),
    "done_when": ("done when", "a line `Done when:` followed by the one observation, at whoever uses the "
                               "result, that shows the result does that job;"),
    "existing": ("existing", "a line `Existing:` under `Done when:`. `Existing:` says what the project already "
                 "has for each thing this plan defines, counts or builds (a term, a metric, a mechanism, a "
                 "screen). Start from the word as the owner and the screens use it, not from a name coined in "
                 "this plan. Find where the project fixes it: the decision or semantics record that the "
                 "repository's AGENTS.md names, searched by the owner's word and by the data source, then the "
                 "code that shows it; cite path:line. Then one of: `as is`; `changed for all: <every place that "
                 "changes, in this plan>`; `new and distinct: <a name that does not contain the old one>, "
                 "differs by <the fact, with a number>`. A meaning changed only inside the new work is none of "
                 "the three. `Existing: none` only with the words searched and the files read. The value may be "
                 "a list under the label."),
}
_PLAN_LABELS = {label: key for key, (label, _) in _PLAN_LINE_PROPERTY.items()}


def _plan_label(line):
    label, sep, value = line.strip().lstrip(_PLAN_MARKS).partition(":")
    key = _PLAN_LABELS.get(label.strip(_PLAN_MARKS).strip().lower()) if sep else None
    return key, value


def plan_head_lines(text):
    """{label key: value} from the opening head lines of a plan. A label's value is the rest of its line plus
    its continuation: list items or indented lines directly under it (after at most one blank line), up to a
    blank line, a heading or another label; continuation lines are not head lines. A value is "" when absent,
    when only markdown marks remain, or when the line is wholly a placeholder (`[...]`, `<...>`) with no
    continuation. Marks around the label are ignored; the value loses only the label's closing emphasis."""
    found = {key: "" for key in _PLAN_LINE_PROPERTY}
    head, current, blanks, cont = 0, None, 0, False
    for line in (text or "").splitlines():
        if not line.strip():
            blanks += 1
            if cont or blanks > 1:
                current = None
            continue
        key, value = _plan_label(line)
        if current and not key and _CONTINUATION_RE.match(line):
            found[current] += "\n" + line.strip()
            cont, blanks = True, 0
            continue
        head += 1
        if head > PLAN_HEAD_LINES:
            break
        current, cont, blanks = None, False, 0
        if key and not found[key]:
            bare = value.strip(_PLAN_MARKS).strip()
            placeholder = bare and _PLACEHOLDER_RE.match(value.strip(" *_`"))
            found[key] = "" if placeholder else (value.lstrip("*_").strip() if bare else "")
            current = key
    return {key: v.strip() if v.strip(_PLAN_MARKS + "\n").strip() else "" for key, v in found.items()}


def plan_lines_decision(text):
    """Refusal naming each missing opening line with its property, or None."""
    found = plan_head_lines(text)
    missing = [prop for key, (_, prop) in _PLAN_LINE_PROPERTY.items() if not found[key]]
    return _PLAN_LINES_REASON.format(missing=" ".join(missing)) if missing else None


# Measured refusal (retro-evals runs/case76-askq/refusal.txt, CASE-76): replays 0/3 -> 2/3; keep it verbatim.
_ASK_EXISTING_REASON = "Refused by ask-existing-gate: a question to the owner that offers how to define, count or label something, or which mechanism to build or use, must say in its stem what the project already has for it: a line `Existing: <path:line> <the meaning or mechanism in force, in the owner's words>`, found in the decision or semantics record the repository's AGENTS.md names and in the code that shows it. The option that keeps it unchanged comes first. An option that departs from it is one of: `changed for all` (it names every place that changes) or `new and distinct` (a name that does not contain the old one, and the fact, with a number, by which it differs); a meaning changed only inside the new work is not offered. A question that offers nothing of that kind (a confirmation, a permission, a priority) carries `metadata.source: \"existing: n/a\"` instead. Then call AskUserQuestion again."


def _existing_value(text):
    """Non-empty `Existing:` value of text that starts with the label, by the plan gate's value rule."""
    return _plan_label(text)[0] == "existing" and bool(plan_head_lines(text)["existing"])


def ask_existing_decision(tool_input):
    """AskUserQuestion gate: None when `metadata.source` starts with `existing: n/a` or a line of some
    question stem starts with a non-empty `Existing:`; else the measured refusal. A citation only in
    metadata does not pass: the owner never sees metadata. Fails open (None) on an input with no
    non-empty string stem: a harness shape change must not block every question to the owner."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    metadata = tool_input.get("metadata")
    source = metadata.get("source") if isinstance(metadata, dict) else None
    if isinstance(source, str) and re.match(r"existing:\s*n/a\b", source.strip(), re.I):
        return None
    questions = tool_input.get("questions")
    stems = [q.get("question") for q in questions if isinstance(q, dict)] if isinstance(questions, list) else []
    stems = [s for s in stems if isinstance(s, str) and s.strip()]
    if not stems:
        return None
    for stem in stems:
        lines = stem.splitlines()
        if any(_existing_value("\n".join(lines[i:])) for i in range(len(lines))):
            return None
    return _ASK_EXISTING_REASON


def plan_simplify_decision(plan, words, plan_changed, review_mtime, agents_seen, params=None, kind="plan"):
    """Simplify gate over observed facts: a refusal message, or None.

    kind "plan" (ExitPlanMode), "design" (`proceed to code` to a builder) or
    "handoff" (a builder spawned from a handoff). "plan" and "design" need a
    `.simplify.md` written at or after plan_changed (the caller's moment of the
    last relevant change); "handoff" needs it to exist only. A plan of at least
    fan_out_min_words words also needs fan_out_min_agents lens agents spawned by
    the main session and naming the plan. One refusal names every unmet
    condition."""
    unmet = []
    review = plan_review_path(plan)
    if kind == "handoff":
        if review_mtime is None:
            unmet.append(_HANDOFF_REVIEW_MISSING % (review, plan, review))
    elif review_mtime is None or review_mtime < plan_changed:
        unmet.append(_PLAN_REVIEW_MISSING % review if kind == "plan"
                     else _DESIGN_REVIEW_MISSING % (plan, review))
    rule = _merged_params(params)["plan_simplify"]
    min_words, min_agents = rule["fan_out_min_words"], rule["fan_out_min_agents"]
    if words >= min_words and agents_seen < min_agents:
        noun = "plan" if kind == "plan" else "design"
        unmet.append(_PLAN_FAN_OUT_MISSING % (noun, words, min_words, min_agents, plan, agents_seen))
    if not unmet:
        return None
    tail = "then call ExitPlanMode again" if kind == "plan" else "then retry"
    return "plan-simplify-gate: %s: %s; %s." % (plan, "; ".join(unmet), tail)


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
    "session. Hand the task to an `orch:builder` or `orch:prod-probe` agent; it loads the skill "
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
    "Untyped subagent refused (45k-token base per turn). Name a type from the Agent "
    "tool's list whose description matches the work (orch types: "
    + ", ".join("`orch:%s`" % a for a in sorted(ORCH_AGENTS))
    + "). If none fits, the main session "
    "adds a line `AGENT_TYPE_OVERRIDE: <reason>` to the prompt (a reason is required)."
)

_SPAWN_REASON = (
    "Agent `{spawner}` may not spawn `{requested}`: {allowed}. Spawn rights are set "
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

    listed = ", ".join("`%s`" % agent_ref(a) for a in (allowed or []))
    phrase = ("it may spawn only " + listed) if listed else "it may not spawn agents"
    return {
        "decision": "deny",
        "reason": _SPAWN_REASON.format(
            spawner=agent_ref(spawner), requested=agent_ref(requested), allowed=phrase
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
    "Repeat the Agent call without the `model` parameter. The model of `{requested}` "
    "is set by its agent definition; agent `{spawner}` "
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
        "reason": _MODEL_REASON.format(spawner=agent_ref(spawner), requested=agent_ref(requested)),
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
WORSE_FOR_TOKEN = "WORSE_FOR:"
# The phrase anywhere in the command: the field check for every caller.
_MERGE_RE = re.compile(
    r"\bgh\s+pr\s+merge\b"
    # git global options may stand between `git` and `push` (`git -C <dir> push`).
    r"|\bgit(?:\s+(?:-C|-c|--git-dir|--work-tree|--namespace)(?:=|\s+)\S+|\s+-\S+)*"
    r"\s+push\b[^|;&\n]*\b(?:master|main)\b"
)
# The stricter test for refusing a delegate as a delegate (CASE-62): the landing
# at command position (line start, a separator, `then`/`do`/`else`, a case arm,
# a brace group) behind env assignments and the rtk, time, env, exec and nohup
# wrappers. Only heredoc bodies are stripped, so a grep pattern or a heredoc that
# holds the phrase is not a landing; a delegate whose command only holds the
# phrase falls through to the field check above.
_LANDING_RE = re.compile(
    r"(?:^|[;&|(){\n]|\b(?:then|do|else)\b)\s*"
    r"(?:(?:\w+=(?:\"(?:[^\"\\]|\\.)*\"|'[^']*'|\S*)|rtk(?:\s+proxy)?|time(?:\s+-p)?|env(?:\s+-\S+)*"
    r"|exec|nohup|command|sudo(?:\s+-\S+)*|nice(?:\s+-n\s+\S+|\s+-\S+)*|timeout(?:\s+-\S+)*\s+\S+)\s+)*"
    # An absolute or relative program path counts (`/usr/bin/git`, `/opt/homebrew/bin/gh`).
    r"(?:[^\s;&|(){}]*/)?(?:gh\b[^;&|\n]*\bpr\s+merge\b"
    r"|git(?:\s+(?:-C|-c|--git-dir|--work-tree|--namespace)(?:=|\s+)\S+|\s+-\S+)*"
    r"\s+push\b[^|;&\n]*(?:(?<![\w/.-])|(?<=refs/heads/))(?:master|main)(?![\w-]))"
)
_LANDING_HEREDOC_RE = re.compile(r"(<<-?[ \t]*(['\"]?)(\w+)\2[^\n]*\n).*?^[ \t]*\3[ \t]*$", re.S | re.M)
_SHELL_C_RE = re.compile(r"\b(?:ba|z)?sh\s+(?:-\w+\s+)*-\w*c\w*\s+(\"(?:[^\"\\]|\\.)*\"|'[^']*')")


def is_landing(command):
    """True when ``command`` runs ``gh pr merge`` or a push to master/main at
    command position, inside a ``bash -c``/``sh -c`` body included."""
    text = _LANDING_HEREDOC_RE.sub(r"\1", command or "")
    if _LANDING_RE.search(text):
        return True
    for m in _SHELL_C_RE.finditer(text):
        body = m.group(1)[1:-1]
        if m.group(1)[0] == '"':
            body = re.sub(r"\\(.)", r"\1", body, flags=re.S)
        if is_landing(body):
            return True
    return False


_MERGE_REASON = (
    "Refused by merge-gate: this command lands a change on a shared branch. Run "
    "the declared check on the real path first (service start plus one unit "
    "through natively, against the shared local store container or a production "
    "read route, never a container per task; on a numeric path the target before and after), then repeat the "
    "command with the fields below: a change lands only with the check, its "
    "production position, the way back and who gets worse. Unit tests or a review "
    "alone do not count (retro 2026-10-12: 36 merges without a real-path check "
    "cost 57 h and one irreversible mass send)."
)
_MERGE_FIELD_DOCS = (
    (CHECK_RAN_TOKEN, "`CHECK_RAN: <what ran, result>`"),
    (PROD_POSITION_TOKEN, "`PROD_POSITION: <dimension along which blast radius grows, production's number on it, source>`"),
    (WAY_BACK_TOKEN, "`WAY_BACK: <means of undo, minutes>`"),
    (WORSE_FOR_TOKEN, "`WORSE_FOR: <who gets worse or can break for every consumer after this change, the new bottleneck; or none, with the reason>`"),
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


_DELEGATE_LANDING_REASON = (
    "Refused by merge-gate: a delegate never lands a change on a shared branch, "
    "whatever its prompt says (CASE-62). Commit on the task branch, reply with "
    "`commits:` and the check, and name the landing request under `deviations:`; "
    "the main session lands after the reviews (orchestrate step 8, Land)."
)


def is_delegate(payload):
    """True when a hook payload comes from a subagent: its own transcript is a
    subagents/agent-*.jsonl file, or the payload names an ``agent_id``. A main
    session started with ``claude --agent`` carries ``agent_type`` too, so the
    type alone does not tell (probe 2026-11-04, CASE-62)."""
    # A non-empty sentinel stands in for a missing transcript so that agent_id alone counts.
    return bool(own_transcript_path(payload.get("transcript_path") or "-", payload.get("agent_id") or ""))


def merge_gate_decision(command, changed_paths=None, delegate=False, ci=None):
    """Deny a delegate's landing (``is_landing``) always. Any other command that
    holds ``gh pr merge`` or a push to master/main, from any caller, is denied
    unless it carries ``CHECK_RAN:``, ``PROD_POSITION:``, ``WAY_BACK:`` and
    ``WORSE_FOR:``, ``ALWAYS_ON:`` when ``changed_paths`` touch an always-on
    rules file, and ``# CI_FIX: <sha7> ...`` when ``ci`` (``ci_tip_state``
    of the target branch) is red. An unknown ``ci`` passes open; its cause is
    named when the command is refused for another reason."""
    cmd = command or ""
    if delegate and is_landing(cmd):
        return {"decision": "deny", "kind": "delegate", "reason": _DELEGATE_LANDING_REASON}
    if not _MERGE_RE.search(cmd):
        return {"decision": "allow"}
    missing = [doc for token, doc in _MERGE_FIELD_DOCS if token not in cmd]
    lead = _MERGE_REASON if missing else _ALWAYS_ON_LEAD
    if not _always_on_satisfied(cmd, changed_paths):
        missing.append(_ALWAYS_ON_DOC)
    ci = ci or {"state": "unread"}
    red = ci["state"] == "red" and not _ci_fix_lifts(cmd, ci["sha"])
    if red:
        if not missing:
            lead = _CI_RED_LEAD
        missing.append(_ci_red_doc(ci))
    if not missing:
        return {"decision": "allow"}
    reason = lead + " Missing: " + "; ".join(missing) + "."
    if ci["state"] == "unknown":
        reason += " Workflow state unknown: %s." % ci["cause"]
    return {"decision": "deny", "reason": reason}


_CI_RED_LEAD = (
    "Refused by merge-gate: a completed workflow run (CI or publish) of the tip "
    "sha of the target branch is red; a landing on a red tip hides its own "
    "breakage behind the earlier one (25 red runs on agents-personal-skills "
    "master from 2026-10-31, 7 landings pushed on it without reading the state)."
)
_CI_RED_CONCLUSIONS = frozenset(("failure", "timed_out", "action_required", "startup_failure"))
_CI_GREEN_CONCLUSIONS = frozenset(("success", "skipped", "neutral"))
_CI_IGNORED_EVENTS = frozenset(("schedule", "workflow_dispatch"))


def ci_tip_state(runs):
    """Workflow verdict of the branch tip from ``gh run list --event push
    --json status,conclusion,databaseId,headSha,event,workflowName`` (newest
    first), or None when the read failed. Scheduled and manual runs are
    dropped; the tip is the newest sha with a completed run. Red when a
    completed run of the tip is red; green when every run of the tip is
    completed green; otherwise (a run still in progress, cancelled, stale)
    unknown. Returns ``{"state": "red", "sha", "runs"}`` (the red runs),
    ``{"state": "green", "sha"}`` or ``{"state": "unknown", "cause"}``."""
    if runs is None:
        return {"state": "unknown", "cause": "gh read failed"}
    runs = [r for r in runs if r.get("event") not in _CI_IGNORED_EVENTS]
    sha = next((r.get("headSha") for r in runs if r.get("status") == "completed"), None)
    if not sha:
        return {"state": "unknown", "cause": "no completed push run in the last %d runs" % len(runs)}
    tip = [r for r in runs if r.get("headSha") == sha]
    bad = [r for r in tip if r.get("status") == "completed" and r.get("conclusion") in _CI_RED_CONCLUSIONS]
    if bad:
        return {"state": "red", "sha": sha, "runs": bad}
    if all(r.get("status") == "completed" and r.get("conclusion") in _CI_GREEN_CONCLUSIONS for r in tip):
        return {"state": "green", "sha": sha}
    return {"state": "unknown", "cause": "tip %s has no verdict (%s)" % (
        sha[:7], ", ".join(sorted({str(r.get("conclusion") or r.get("status")) for r in tip})))}


def _ci_fix_lifts(cmd, sha):
    reason = override_marker(cmd, "CI_FIX")
    token = (reason or "").split()[0].lower() if reason else ""
    return len(token) >= 7 and sha.lower().startswith(token)


def _ci_red_doc(ci):
    runs = ", ".join("%s run %s %s" % (r.get("workflowName") or "workflow", r.get("databaseId"), r.get("conclusion"))
                     for r in ci["runs"])
    sha7 = ci["sha"][:7]
    return ("a green target branch: tip %s is red (%s); fix it in this push and add the line "
            "`# CI_FIX: %s <what the push repairs>`, or, for a red run this push cannot repair "
            "(an external publish failure), `# CI_FIX: %s publish red: <cause>, not this push`"
            % (sha7, runs, sha7, sha7))


# `gh pr merge` options that take a separate value.
_PR_MERGE_VALUE_OPTIONS = frozenset({
    "-b", "--body", "-F", "--body-file", "-t", "--subject", "-R", "--repo",
    "-A", "--author-email", "--match-head-commit",
})


def landing_target(command, cwd):
    """``{"dir", "branch", "pr"}`` of the first landing in ``command``: the
    repository directory (``cwd`` moved by a literal ``cd``/``pushd``/``git
    -C``; None when a move is not literal or ``cwd`` is None), the branch a
    push names (None for ``gh pr merge``) and the PR number it names."""
    hit = _landing_walk(command, cwd)
    if hit is None:
        return {"dir": None, "branch": None, "pr": None}
    return {"dir": hit["dir"], "branch": hit.get("target"), "pr": hit.get("pr")}


# git push options that take a separate value.
_PUSH_VALUE_OPTIONS = frozenset({"--repo", "-o", "--push-option", "--receive-pack", "--exec"})


def push_range(command, cwd):
    """``{"dir", "remote", "src", "target"}`` of the first ``git push`` in
    ``command`` that updates master or main, or None when the repository
    directory, remote or refspec is not literal. ``cwd`` is the directory the
    command starts in; a literal ``cd``, ``pushd`` and ``git -C`` move it."""
    hit = _landing_walk(command, cwd)
    if hit is None or "remote" not in hit or not hit["dir"]:
        return None
    return {k: hit[k] for k in ("dir", "remote", "src", "target")}


def _landing_walk(command, cwd):
    """The first push to master/main or ``gh pr merge`` in ``command`` with
    the directory it runs in (None when not literal)."""
    directory = cwd or None
    for tokens in _shell_segments(command or ""):
        tokens = _tokens_after_keywords(" ".join(tokens))
        prog = _program(tokens)
        if prog in ("cd", "pushd"):
            directory = _cd_target(prog, tokens[1:], {"dir": directory}) if directory else None
            continue
        if prog == "gh" and tokens[1:3] == ["pr", "merge"]:
            rest, pr, j = tokens[3:], None, 0
            while j < len(rest):
                if rest[j].startswith("-"):
                    j += 2 if rest[j] in _PR_MERGE_VALUE_OPTIONS else 1
                    continue
                pr = pr or (rest[j] if rest[j].isdigit() else None)
                j += 1
            return {"dir": directory, "pr": pr}
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
# verb of work, a deferral phrase, or "I'll <work verb>". Only a judge prefilter:
# the judge decides whether the commitment is an unbacked deferral.
_PROMISE_RE = re.compile(
    r"\b(?:поправлю|исправлю|сделаю|добавлю|заведу|открою|починю|допишу|проверю"
    r"|вернусь\s+к|займусь|доделаю|перепишу)\b"
    r"|(?:отдельн\w+|следующ\w+)\s+(?:PR|пулл?-?реквест\w*|коммит\w*)"
    r"|\bI(?:'ll|\s+will)\s+(?:fix|open|add|do|follow\s+up|look|update|write|check)\b"
    r"|\b(?:in\s+a\s+)?follow-?up\s+PR\b|\bseparately\s+later\b",
    re.IGNORECASE,
)
# Work offered on the owner's consent or command ("если подходит", "по твоей
# команде", "if that works for you"). A condition on the agent's own result
# ("если нормально отработает", "if it works") names no owner and is not one.
# Part of the judge prefilter: such an offer goes to the judge.
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
_DEFERRAL_REASON = (
    REPLY_LINT_MARKER + " the reply defers work it could do this turn (\"{sentence}\"). "
    "Do it now if it is inside your authority. A step that waits on something that is not you names what it "
    "waits on and the watch armed on it; a note or file written this turn to hold the step is not tracking. "
    "Append only the missing part; do not repeat the reply."
)
_NEXT_STEP_REASON = (
    REPLY_LINT_MARKER + " the reply ends on a step of your own that nothing waits for (\"{sentence}\"). "
    "Run it now, in this turn. A step that waits on something that is not you names what it waits on and "
    "the watch armed on it (Monitor id, Cron id, agent, PR check, the owner's instruction to stop). A file or "
    "note written this turn is not tracking."
)

_TIMED_STEP_REASON = (
    REPLY_LINT_MARKER + " the reply names a step of your own at a time that has not arrived (\"{sentence}\") "
    "and nothing wakes the session on a clock for it (no Cron job, no pending /loop wakeup). Arm one `CronCreate` one-shot at that time now and name "
    "its id, its fire time, that `CronList` shows it and that session exit kills it; or state that nothing of "
    "yours runs before the owner's word at that time. Append only the missing part; do not repeat the reply."
)

_LEVER_REASON = (
    REPLY_LINT_MARKER + " a lever that makes a step faster is listed before the read of who consumes that "
    "step's result; name the consumer (from its own code or manifest) and whether the step is needed, or list "
    "that read as the first step; work with no consumer is removed, and its removal is the lever; a stage or "
    "wait whose cause you call unknown gets no lever until its mechanism is read (a log line, an interval, a flag)."
)

# -- lever trigger: a lever that speeds up an existing step, with no read of who consumes it --

LEVER_CHARS = 600
_LEVER_WORD_RE = re.compile(r"рычаг|\blevers?\b(?!\s*#\d)", re.IGNORECASE)
_LEVER_RE = re.compile(
    r"флаг|flag|requeue|интервал|interval|параллел|parallel|слить|сплющ|merge|flatten|ускор|быстрее"
    r"|кэш|cache|таймаут|timeout|poll|(?<!в)опрос",
    re.IGNORECASE,
)
_LEVER_CONSUMER_RE = re.compile(
    r"кто\s+(?:читает|потребляет|использует|ждёт|ждет)|никто\s+не\s+(?:читает|потребляет|использует)"
    r"|никому\s+не\s+нуж\w{0,4}\s+(?:шаг|этап|результат)|потребител|нужн(?:а|о|ы|ен)?\s+ли"
    r"|зачем\s+(?:он|она|оно|они|нужн)|needed at all|who\s+(?:reads|uses|waits)"
    r"|removal\s+(?:of\s+the\s+)?(?:step|stage|job|link|edge|wait)|removal is the lever"
    r"|consumer\s+(?:of|is|reads|needs)|no\s+consumer|consumed\s+by|who\s+consumes|no one consumes"
    r"|убрать\s+(?:шаг|звено|связ)|убира(?:ем|ю)\s+(?:шаг|этап|звено|связ|джоб|job)"
    r"|лишн\w{0,12}\s+(?:шаг|этап|звен|связ|джоб|job)|(?:шаг|этап|стади)\w{0,12}\s+не\s+нуж"
    r"|(?:step|stage|job|link|edge)\s+(?:is\s+)?not\s+needed"
    r"|рычаг\w{0,4}\s+не\s+став|no\s+lever\s+(?:until|before)",
    re.IGNORECASE,
)


# A stage whose cause the reply itself leaves unexplained: a lever on it is a candidate with or
# without a speed-up form.
_LEVER_UNEXPLAINED_RE = re.compile(
    r"(?:причин|механизм)\w{0,4}(?:\s+\w+){0,2}\s+(?:не\s+найден|неизвестн|не\s+провер|не\s+выяснен)"
    r"|cause(?:\s+of(?:\s+\w+){1,3})?\s+(?:is\s+|was\s+|remains\s+)?(?:unknown|not\s+found|unverified|unexplained)"
    r"|не\s+удалось\s+(?:найти|объяснить)|unexplained",
    re.IGNORECASE,
)


def lever_candidate(reply):
    """The sentences carrying the lever word (at most ``LEVER_CHARS``) when the reply lists a lever
    with a speed-up form or on a stage it calls unexplained, and names no consumer or necessity
    read, else ""."""
    text = reply or ""
    if (not _LEVER_WORD_RE.search(text) or not (_LEVER_RE.search(text) or _LEVER_UNEXPLAINED_RE.search(text))
            or _LEVER_CONSUMER_RE.search(text)):
        return ""
    sentences = [s.strip() for s in _SENTENCE_BREAK_RE.split(text) if _LEVER_WORD_RE.search(s)]
    return re.sub(r"\s+", " ", " ".join(sentences))[:LEVER_CHARS]


# -- next-step trigger (a): the closing part of the reply names a step of the agent's own --

NEXT_STEP_CLOSING_CHARS = 600
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_URL_RE = re.compile(r"https?://\S+")
_ABS_PATH_RE = re.compile(r"(?<![\w/])(?:~|/[\w.@-]+)(?:/[\w.@-]+)+/?")
# A next-step label at a line start (list markers and bold allowed before it) ...
_NEXT_STEP_LABEL_RE = re.compile(
    r"^[ \t]*(?:(?:[-*+>]|\d+[.)])[ \t]+)*(?:\*\*|__|\*|_)?[ \t]*"
    r"(?:следующ(?:ий|ие)\s+шаг|что\s+сделаю\s+дальше|дальше:|далее:|что\s+дальше"
    r"|next\s+steps?\b|what\s+i\s+will\s+do\s+next)",
    re.IGNORECASE | re.MULTILINE,
)
# ... or a first-person future verb of work.
_NEXT_STEP_VERB_RE = re.compile(
    r"\b(?:проверю|посчитаю|начну|запущу|пришлю|разложу|сверю|прогоню|посмотрю|выясню|найду"
    r"|разберу|сравню|измерю|замерю|соберу|напишу|отправлю|доложу|вернусь)\b"
    r"|\bI(?:'ll|\s+will)\s+(?:check|run|look|measure|compare|start|send|report)\b",
    re.IGNORECASE,
)
# ... or a step of the agent's own named as not yet run ("пока не запускал", "not run yet"), the
# form a list of deciding checks closes with (CASE-64).
_NEXT_STEP_HELD_RE = re.compile(
    r"\b(?:пока|ещё|еще)\s+(?:ничего\s+)?не\s+(?:запускал|запустил|запущен\w*|проверял|проверил|делал|сделал"
    r"|начинал|начал|смотрел|считал|мерил|замерял|сверял|трогал|пробовал)\b"
    r"|\bне\s+запускал\w*\b"
    r"|\b(?:not|haven't|hasn't|have\s+not|has\s+not)\s+(?:yet\s+)?(?:run|started|launched|checked|measured)\b"
    r"|\bnot\s+(?:run|started|launched|checked)\s+yet\b",
    re.IGNORECASE,
)
# ... or a label opening the list of checks that would decide the reply's open question; the
# candidate sentence then carries the label and its first item (CASE-64).
_NEXT_STEP_DECIDE_LABEL_RE = re.compile(
    r"^[ \t]*(?:(?:[-*+>]|\d+[.)])[ \t]+)*(?:\*\*|__|\*|_)?[ \t]*"
    r"(?:что\s+(?:это\s+|всё\s+|все\s+)?реш(?:ит|ило\s+бы|ает)\b"
    r"|что\s+(?:ещё\s+|еще\s+)?(?:нужно\s+|надо\s+|осталось\s+|можно\s+)?проверить"
    r"|чем\s+(?:это\s+)?проверить"
    r"|what\s+(?:would\s+|will\s+)?(?:settle|decide)s?\b|what\s+(?:remains\s+|is\s+left\s+)?to\s+check"
    r"|remaining\s+checks?\b|checks?\s+(?:that\s+)?(?:would\s+)?(?:decide|settle)\b)",
    re.IGNORECASE | re.MULTILINE,
)
_SENTENCE_BREAK_RE = re.compile(r"[.!?\n]")


def _blank_code_paths_quoted(text):
    """Length-preserving blanking of fenced code, URLs and absolute paths, then quoted spans."""
    for pattern in (_FENCE_RE, _URL_RE, _ABS_PATH_RE):
        text = pattern.sub(lambda m: " " * len(m.group(0)), text)
    return _blank_quoted(text)


def next_step_sentence(reply):
    """Trigger (a) of the next-step rule: the sentence of the reply's closing part (its last
    NEXT_STEP_CLOSING_CHARS, code, URLs, paths and quotes blanked) holding a next-step label, a
    first-person future verb of work or a step named as not yet run; "" when none."""
    reply = reply or ""
    original = reply[-NEXT_STEP_CLOSING_CHARS:]
    blanked = _blank_code_paths_quoted(reply)[-NEXT_STEP_CLOSING_CHARS:]
    decide = _NEXT_STEP_DECIDE_LABEL_RE.search(blanked)
    match = (_NEXT_STEP_LABEL_RE.search(blanked) or decide or _NEXT_STEP_VERB_RE.search(blanked)
             or _NEXT_STEP_HELD_RE.search(blanked))
    if not match:
        return ""
    # a decide label is shown with its first item, so the judge sees the check and not a bare heading
    start, end = _sentence_span(blanked, match, match is decide)
    return re.sub(r"\s+", " ", original[start:end]).strip()


def _sentence_span(blanked, match, with_first_item=None):
    """(start, end) of the sentence of ``match``, extended by the first item that follows when
    ``with_first_item`` (None: when the sentence is a heading ending with ":")."""
    start = 0
    for brk in _SENTENCE_BREAK_RE.finditer(blanked, 0, match.start()):
        start = brk.end()
    end_match = _SENTENCE_BREAK_RE.search(blanked, match.end())
    end = end_match.end() if end_match else len(blanked)
    if with_first_item is None:
        with_first_item = blanked[start:end].rstrip().endswith(":")
    if with_first_item:
        nxt = re.compile(r"\S").search(blanked, end)
        if nxt:
            item_end = _SENTENCE_BREAK_RE.search(blanked, nxt.start())
            end = item_end.end() if item_end else len(blanked)
    return start, end


# A future time of the timed-step rule: a relative marker (always) or a date / HH:MM after a forward word.
_WEEKDAY_RU = r"(?:понедельник\w*|вторник\w*|сред[ау]|четверг\w*|пятниц\w+|суббот\w+|воскресень\w+)"
_WEEKDAY_EN = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_MONTH = (r"(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма[яй]|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*"
          r"|ноябр\w*|декабр\w*|jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*)")
_DATE = r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{2}(?:\.\d{2,4})?|\d{1,2}\s+" + _MONTH + r")"
_RELATIVE_TIME_RE = re.compile(
    r"\bзавтра\b|\b(?:сегодня|следующ\w+)\s+ночью\b|\bна\s+следующ\w+\s+(?:ночь|недел\w+)\b"
    r"|\bв\s+следующ\w+\s+" + _WEEKDAY_RU + r"|\bво?\s+" + _WEEKDAY_RU + r"\b"
    r"|\b" + _WEEKDAY_RU + r"\W{0,4}\s*(?:ночью|утром|вечером|днём|днем)\b|\b(?:ночью|утром|вечером)\b"
    r"|\b(?:tonight|tomorrow)\b|\bnext\s+(?:week|night|" + _WEEKDAY_EN + r")\b|\bon\s+" + _WEEKDAY_EN + r"\b",
    re.IGNORECASE,
)
# A date or HH:MM counts only after a forward word.
_FORWARD_ABSOLUTE_TIME_RE = re.compile(
    r"\b(?:в|к|до|после|с|начиная\s+с|at|by|until|after)\s+\d{1,2}:\d{2}\b"
    r"|\b(?:on|до|к)\s+" + _DATE + r"\b",
    re.IGNORECASE,
)
_TIME_MARKER_RE = re.compile(_RELATIVE_TIME_RE.pattern + "|" + _FORWARD_ABSOLUTE_TIME_RE.pattern, re.IGNORECASE)
# The agent's own step: the next-step future verbs plus present-tense forms used as futures.
_TIMED_VERB_RE = re.compile(
    _NEXT_STEP_VERB_RE.pattern
    + r"|\b(?:запускаю|смотрю|проверяю|катаю|присылаю|пришлю|иду|пишу|отправляю|делаю|начинаю|мержу|смержу"
    r"|сверяю|считаю|поставлю|назову|спрошу|удалю|перезапущу|выкачу|задеплою|применю|сделаю)\b"
    r"|\bI(?:'ll|\s+will|'m\s+going\s+to)\s+(?:merge|deploy|roll|arm|ask|do|apply|restart|delete)\b",
    re.IGNORECASE,
)


def timed_step_sentence(reply):
    """Trigger (a) of the timed-step rule: the closing part (as in ``next_step_sentence``) holds a future
    time marker whose sentence (with its first item when the sentence ends with ":") holds a first-person
    step verb or a next-step label. Returns that span of the first such marker; "" when none."""
    reply = reply or ""
    original = reply[-NEXT_STEP_CLOSING_CHARS:]
    blanked = _blank_code_paths_quoted(reply)[-NEXT_STEP_CLOSING_CHARS:]
    spans = [_sentence_span(blanked, m) for m in _TIME_MARKER_RE.finditer(blanked)]
    span = next((sp for sp in spans if _TIMED_VERB_RE.search(blanked, *sp)
                 or _NEXT_STEP_LABEL_RE.search(blanked[sp[0]:sp[1]])), None)
    if span is None:
        return ""
    start, end = span
    return re.sub(r"\s+", " ", original[start:end]).strip()


def live_clocks(facts):
    """Condition (c) of the timed-step rule: what will wake the session on a clock, live at the stop
    (``transcript.background_watch_scan`` facts): one-shot and recurring cron jobs and a pending /loop
    wakeup. A Monitor, agent or task is not a clock and does not exempt."""
    live = [k for k, j in facts["cron_jobs"].items() if not j["closed"]]
    live += [k for k, j in facts["recurring_jobs"].items() if not j["closed"]]
    return live + (["wakeup"] if facts["wakeup_pending"] else [])


def exempt_candidates(next_step, timed_step, facts):
    """The (next-step, timed-step) candidates of a stop given its transcript facts: each "" when the session
    holds a mechanism that exempts it (any live one for next-step, a live clock for timed-step); both "" when
    ``facts`` is None (no transcript to scan)."""
    if facts is None:
        return "", ""
    return ("" if live_mechanisms(facts) else next_step, "" if live_clocks(facts) else timed_step)


def live_mechanisms(facts):
    """Condition (c) of the next-step rule: ids of the session's mechanisms still live at the stop,
    from any turn (``transcript.background_watch_scan`` facts). A background task is live until
    its notification or TaskStop, a Monitor until its end notice, a scheduled job until its fire
    or CronDelete (a one-shot job; a recurring one is not a wait), an async agent until its
    notification or TaskStop (a SendMessage re-opens it); an Agent call with no result is live."""
    live = [k for k, t in facts["tasks"].items() if not t["closed"]]
    live += [k for k, m in facts["monitors"].items() if m["ended"] is None]
    live += [k for k, j in facts["cron_jobs"].items() if not j["closed"]]
    live += [k for k, a in facts["agents"].items() if not a["closed"]]
    live += ["Agent call"] * facts["agent_calls"]
    return live


# The one word before a done word that makes it a question, a relative clause, a condition or a
# future, not an assertion ("как работает", "когда будут готовы", "once done").
_DONE_LABEL_RE = re.compile(
    r"^\W*(?:что\s+сделано|what(?:\s+(?:was|is))?\s+done)\s*:", re.IGNORECASE
)
_NON_ASSERTION_WORD_RE = re.compile(
    r"\b(?:как|how|когда|when|once|будет|будут|станет|will|если|if)\s+$", re.IGNORECASE
)
# A condition or a negation right after the done word qualifies the status, it does not claim it.
_QUALIFIED_AFTER_RE = re.compile(
    r"(?:готов\w*,?\s+когда|done\s+when)\b"
    r"|(?:сделан\w*|done)\s+(?:через|в\s+виде|via|as\s+an?\b|\S+['`](?:ом|ем)\b)"
    r"|(?:готов\w*|сделан\w*|done)\s+(?:пока\s+|ещё\s+|еще\s+|yet\s+)?(?:не|not)\b"
    r"|(?:готов\w*|сделан\w*|done)\s+(?:за|in)\s+(?:\d+\s+)?(?:секунд\w*|минут\w*|час\w*|дн[ея]\w*|seconds?|minutes?|hours?|days?)\b",
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


JUDGE_PROMPT = (
    "You check the final reply of a coding agent before its turn ends. The reply, as a JSON string:\n"
    "{reply}\n\n"
    "Answer the questions about it (the reply may be in any language).\n\n"
    "QUESTION A, OWNER HAND-OFF. The agent's tool to ask the owner, AskUserQuestion, pauses the turn and is "
    "not visible in this text, so a reply that ends the turn while still needing the owner has not asked "
    "through it. It is a hand-off only when either holds:\n"
    "1. Work cannot continue NOW until the owner gives a choice, answer, approval or input, and the reply "
    "asks for it in plain text. Any shape counts: a direct question offering options (\"A or B?\"), "
    "\"your call\", \"waiting for your decision\", the agent's next step conditioned on the owner's word "
    "(\"I start on your go\"), the owner's choice named as pending (\"three decisions still wait for you\"), "
    "an indirect question, or a reminder that an earlier question is still open.\n"
    "2. The reply points to content of an earlier message instead of stating it (\"see the options above\", "
    "\"the command is in my previous message\") for something it asks the owner now.\n"
    "NOT a hand-off (these pass even when they mention the owner, approval or a hash):\n"
    "- an action only the owner can perform, stated exactly: an `APPROVE <hash>` line or a `! command` "
    "with the plan path or scope stated, e.g. \"Waits on you: the SQL change. Plan: /tmp/plan.sh. Rollback: "
    "the same script, 2 min. To allow the run, reply: APPROVE 6892e423...\"; a permission the hooks forbid "
    "to agents, named with its exact action;\n"
    "- a consent the agent will request LATER, once something is ready: \"then I will send you the hash for "
    "APPROVE\", \"the plan will need your APPROVE line\", \"if the reviewer approves, I will ask you to "
    "confirm\", \"I will prepare it but will not run it without your approval\";\n"
    "- a future step in a list of the agent's next steps, even if it is \"a question to you with options\";\n"
    "- an action only the owner can perform named as a future unblocker (\"only a ticket you open can lift "
    "this\") while the agent's own work continues in the same reply; a hand-off needs work to wait on the "
    "owner now;\n"
    "- a statement of a limitation or of what blocks something (\"only a ticket you open with the provider "
    "can lift this\"), when nothing is asked now;\n"
    "- a stop or hold the owner already agreed to, named as the reason work stops (\"stopped at layer 1 "
    "as agreed; further only if you say so\");\n"
    "- an answer to the owner's own question, even with a short optional follow-up offer;\n"
    "- a tunable value already set and reported (\"set to 7 days, keep or change?\");\n"
    "- a wait that is negated or resolved (\"nothing waits on you\");\n"
    "- status, results, or work that continues without the owner; a wait on the agent's own subagents, "
    "reviewers, builders, probes, watchers, CI or background tasks; a wait on a third party or an event;\n"
    "- an idle tick of an unattended goal naming the owner's own stated action as its resume condition;\n"
    "- quoting or describing such phrases as examples (in code, quotes, or rule text).\n\n"
    "QUESTION B, UNBACKED DEFERRAL. The agent says it will do some work later (\"I will add it to the "
    "plan\", \"I'll fix it in a follow-up PR\") that it could do in this turn, the deferral waits on "
    "nothing (no running agent, reviewer, tool, CI, test run, measurement, external event, or the owner's "
    "answer), and the reply names no place where the remaining work is tracked (an issue, PR, handoff or "
    "plan file that existed before this turn). Adding a point to a plan, a ticket or a file is itself work the agent "
    "can do now; a plan, ticket or file written in this turn to hold the step is not tracking. NOT a deferral: a wait on any of those, even phrased in the first person (\"when I get the "
    "agent's result I will update the ticket\", \"I will fix it after review A\", \"if the measurement shows "
    "X, then I add a cache\"); a description of a plan a delegate carries out; a section label (\"How I will "
    "check.\"); a next step the agent names while its own delegate, reviewer, job or monitor is still "
    "running (the reply says so); a follow-up already tracked. A deferral needs the agent's work on the task "
    "to have ended this turn with the step left undone and untracked.\n\n"
    "{question_c}"
    "{question_d}"
    "{question_e}"
    "For each question, find the one sentence that makes it true. If none exists, or a NOT case covers it, "
    "the answer is false. When unsure, answer false.\n\n"
    "Respond with one JSON object only, no other text, sentences written before the booleans:\n"
    "{{\"sentence\": \"<hand-off sentence quoted, at most 15 words, or none>\", \"handoff\": <true or false>, "
    "\"deferral_sentence\": \"<deferral sentence quoted, at most 15 words, or none>\", "
    "\"deferral\": <true or false>, "
    "\"next_step_sentence\": \"<the step sentence quoted, at most 15 words, or none>\", "
    "\"next_step\": <true or false>, "
    "\"lever_sentence\": \"<the lever sentence quoted, at most 15 words, or none>\", "
    "\"lever\": <true or false>, "
    "\"timed_step_sentence\": \"<the timed step sentence quoted, at most 15 words, or none>\", "
    "\"timed_step\": <true or false>}}"
)
_QUESTION_C = (
    "QUESTION C, OWN NEXT STEP. The reply's closing part names a step the agent itself will take "
    "(\"{sentence}\"); a check the reply lists as what would decide its open question, or a check it says "
    "it has not yet run (\"пока не запускал\"), counts as such a step. Answer true when that step could "
    "start now in this same turn: nothing named in the reply must happen first that is not the agent's own "
    "action. Answer false when the step waits on any of: "
    "a running agent, reviewer, builder, probe, watcher, CI, test run, background task or job (the reply says "
    "so or names its id); a named person, colleague, client, other session or third party acting first; a "
    "clock time, date, data window or period that has not arrived; the owner's answer, approval, choice or "
    "instruction, or the owner's earlier instruction to stop or hold; or when the sentence is a quote, a "
    "section label, a description of a delegate's plan, a step already done, or a bare statement that nothing "
    "was run or changed with no check named. When unsure, answer false.\n\n"
)
_NO_QUESTION_C = "QUESTION C is not asked for this reply: answer next_step false.\n\n"
_QUESTION_D = (
    "QUESTION D, LEVER WITHOUT A CONSUMER READ. The reply lists a lever that makes an existing step or wait "
    "faster (a flag, an interval, a parallel run, a merge, a cache); its lever sentences: \"{sentence}\". "
    "Answer true when such a lever is listed for a step and the reply names neither who consumes that step's "
    "result nor whether the step is needed at all. Answer false when the reply names the consumer read as the "
    "first step or states the step is needed and by what; when the lever itself is already applied or its "
    "effect measured (a result, a retrospective, a status; measured durations of the step do not excuse a "
    "proposed lever); when the text explains a cause or a feature rollout "
    "rather than proposing a lever; or when the lever is a rewrite of a query or code judged on its own cost, "
    "a design alternative, a feature or product rollout, a paid option, or a retrospective of work already "
    "done. True only for a change to the timing, frequency, parallelism, caching or merging of an existing "
    "step or wait whose consumer the reply never names, or when the reply credits a lever to a stage or wait "
    "whose cause the reply itself calls unknown, unverified or not found (a lever on an unexplained stage). "
    "When unsure, answer false.\n\n"
)
_NO_QUESTION_D = "QUESTION D is not asked for this reply: answer lever false.\n\n"
_QUESTION_E = (
    "QUESTION E, OWN STEP AT A TIME NOT YET ARRIVED. The reply's closing part names a future time next to "
    "steps (\"{sentence}\"). Answer true when the reply says the agent itself will take an action at or "
    "after a clock time, date, night, window or period that has not arrived, at least one such action runs "
    "without the owner's word first (a check, a message or a plan the agent sends before the owner's go "
    "counts, even when later steps wait on the owner), and the reply names no scheduled one-shot (a CronCreate "
    "id) already armed for it; a promise to arm one later is not an armed one. Answer false when the time "
    "belongs to an external event the agent only describes (a job's or system cron's schedule, CI, someone "
    "else's window), when the time is when something already happened or was observed (a merge, a build, a "
    "failure at that time), when the action at that time is a delegate's (an agent, watcher, builder or probe "
    "waits or checks) or the owner's (\"you run it in the morning\"), when the reply names an armed loop wake-up "
    "or scheduled check of this session for it, when every step at that time starts only on the owner's word and the reply says so, "
    "when the named time is only a deadline or bound (\"до 24 ноября\", \"by/until <date>\") on a step whose "
    "start is an event or the owner's word (\"после выкатки\", \"after the rollout\") rather than that time, "
    "when the sentence quotes a rule or an example, when the time has passed (a report of what happened), "
    "or when a one-shot id is named as armed. When unsure, answer false.\n\n"
)
_NO_QUESTION_E = "QUESTION E is not asked for this reply: answer timed_step false.\n\n"
_HANDOFF_REASON = (
    REPLY_LINT_MARKER + " the reply leaves work waiting on the owner in plain text "
    "(\"{sentence}\"). If the decision is inside your authority (a technical question by the "
    "authority rule, or a tunable), decide it, do it now and report it. Otherwise ask now with the "
    "AskUserQuestion tool: each option self-contained (what the owner sees change, the "
    "deciding numbers, your recommendation first), never a pointer to an earlier message. The "
    "question and its options are a chat reply to the owner, not an artifact: write them in the "
    "language your reply rules set for chat replies."
)
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


# A reply that hands work to the owner addresses the owner or conditions work on the
# owner's act: a second-person pronoun, possessive, finite or imperative verb (RU/EN),
# "owner", an idiom like "по команде" / "жду подтверждения" / "пока не скажете" /
# "let me know", a consent offer ("если подходит, сделаю"), or a question mark. A wide
# net: the model judge filters.
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
    """The reply can be a hand-off at all."""
    return bool(_ADDRESSES_OWNER_RE.search(reply))


def needs_judge(reply):
    """The reply can be a hand-off or a deferral, so the model judge is worth a call:
    it addresses the owner or holds a first-person commitment (``_PROMISE_RE``)."""
    return addresses_owner(reply) or bool(_PROMISE_RE.search(_blank_quoted(reply)))


def judge_prompt(reply, next_step="", lever="", *, timed_step=""):
    """The judge prompt for one final reply; the reply goes in as a JSON string. Question C is
    asked only for a next-step candidate (``next_step`` = its trigger sentence), question D only
    for a lever candidate (``lever`` = ``lever_candidate``), question E only for a timed-step candidate."""
    question_c = _QUESTION_C.format(sentence=next_step.replace('"', "'")) if next_step else _NO_QUESTION_C
    question_d = _QUESTION_D.format(sentence=lever.replace('"', "'")) if lever else _NO_QUESTION_D
    question_e = _QUESTION_E.format(sentence=timed_step.replace('"', "'")) if timed_step else _NO_QUESTION_E
    return JUDGE_PROMPT.format(reply=json.dumps(reply, ensure_ascii=False), question_c=question_c,
                               question_d=question_d, question_e=question_e)


def parse_judge_verdict(cli_stdout):
    """Verdict from `claude -p --output-format json` output:
    {"handoff", "sentence", "deferral", "deferral_sentence", "next_step", "next_step_sentence"}, or None when the run
    failed or the result is not exactly one verdict object (the hook then fails open)."""
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
    if not isinstance(verdict, dict) or not all(
        isinstance(verdict.get(k), bool) for k in ("handoff", "deferral", "next_step")
    ):
        return None
    return {
        "handoff": verdict["handoff"],
        "sentence": str(verdict.get("sentence") or ""),
        "deferral": verdict["deferral"],
        "deferral_sentence": str(verdict.get("deferral_sentence") or ""),
        "next_step": verdict["next_step"],
        "next_step_sentence": str(verdict.get("next_step_sentence") or ""),
        "lever": verdict.get("lever") is True,
        "lever_sentence": str(verdict.get("lever_sentence") or ""),
        "timed_step": verdict.get("timed_step") is True,
        "timed_step_sentence": str(verdict.get("timed_step_sentence") or ""),
    }


def reply_lint_decision(last_assistant_text, stop_hook_active, verdict=None, next_step="", lever="", *,
                        timed_step=""):
    """Return the main session once when its final reply claims completion
    without an observation, or the judge ``verdict`` (``parse_judge_verdict``,
    None when no verdict exists) finds a plain-text hand-off to the owner or an
    unbacked deferral of work, or, for a next-step candidate (``next_step`` = the
    trigger sentence of a reply whose session holds no live mechanism), the judge
    finds the step could start now; ``next-step`` then replaces ``deferral``; or, for a
    lever candidate (``lever``), the judge finds a lever listed with no consumer read; or, for a timed-step
    candidate (``timed_step`` = ``timed_step_sentence`` of a reply whose session holds no live clock,
    ``live_clocks``), the judge finds an own step at a time not yet arrived. Rules add up.
    ``rules`` names every rule that fired.
    Not claims (2026-10-15): a Russian not-verified disclosure, a done word
    as a section label or with a manner complement.
    The second stop (``stop_hook_active``) always passes."""
    text = (last_assistant_text or "").strip()
    if stop_hook_active or not text:
        return {"decision": "allow", "rules": []}
    fired = []
    if _has_done_claim(text) and not _OBSERVATION_RE.search(text):
        fired.append(("claim", _CLAIM_REASON))
    if verdict and verdict["handoff"]:
        fired.append(("handoff", _HANDOFF_REASON.format(sentence=verdict["sentence"].replace('"', "'"))))
    if next_step and verdict and verdict["next_step"]:
        sentence = verdict["next_step_sentence"] or next_step
        fired.append(("next-step", _NEXT_STEP_REASON.format(sentence=sentence.replace('"', "'"))))
    elif verdict and verdict["deferral"]:
        fired.append(("deferral", _DEFERRAL_REASON.format(sentence=verdict["deferral_sentence"].replace('"', "'"))))
    if lever and verdict and verdict.get("lever"):
        fired.append(("lever", _LEVER_REASON))
    if timed_step and verdict and verdict.get("timed_step"):
        sentence = verdict.get("timed_step_sentence") or timed_step
        fired.append(("timed-step", _TIMED_STEP_REASON.format(sentence=sentence.replace('"', "'"))))
    if not fired:
        return {"decision": "allow", "rules": []}
    return {"decision": "block", "rules": [r for r, _ in fired], "reason": " ".join(m for _, m in fired)}


# -- brief gate: a delegate's budget goes to the deliverable, not to discovery --

_BRIEF_LINES = {
    "builder": ("files:", "check:"),
    "prod-probe": ("access:",),
}
_BRIEF_REASON = (
    "Refused by brief-gate: a `{requested}` prompt needs the line(s) {missing} "
    "(each at the start of a line). `files:` = file:line pointers from one "
    "`orch:scout` run; `check:` = the cheapest run that decides: logic as native "
    "tests; a third-party store through the one shared local container per store "
    "(a database per task, dropped at the end), never a container, image or volume "
    "per task; real behaviour on production after rollout as the last step; a "
    "container or image only to compare two adjacent variants, with its reason and "
    "its removal; "
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
    "# scope:", "# undo:", "# count:", "# contradiction:", "# prod_position:", "# way_back:",
    "# effect_read:",
)
_PLAN_HEADER_LIST = " / ".join(h.rstrip(":") for h in _PLAN_HEADERS)
# One reason for every command that writes a live Secret, whatever program carries the write.
_SECRET_WRITE_REASON = "the command writes a live Secret: the previous value cannot be restored"
_PLAN_INDIRECT_RE = re.compile(r"^\s*(?:(?:source|\.|exec|eval)\s|(?:bash|sh|zsh)\s+\S)")
_SEGMENT_PUNCT = frozenset(";&|()\n`{}")
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_SHELLS = frozenset({"bash", "sh", "zsh"})
_INNER_SUBST_RE = re.compile(r"`([^`]*)`|\$\(([^()]*)\)")
_KUBECTL_VALUE_FLAGS = frozenset({
    "-n", "-s", "-v", "--v", "--as", "--as-group", "--token", "--request-timeout",
    "--namespace", "--context", "--kubeconfig", "--cluster", "--user", "--server",
    "-c", "--container", "--pod-running-timeout",
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
# Server-side effects a SELECT can carry: terminate or cancel sessions, reload
# config, drop slots, switch WAL, promote, write server files, reach another
# database, run a program.
_SQL_SIDE_EFFECT_RE = re.compile(
    r"\b(?:pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_drop_replication_slot"
    r"|pg_switch_wal|pg_promote|pg_rotate_logfile|pg_file_write|lo_export|dblink\w*)\s*\("
    r"|\bCOPY\b[^;]*\bPROGRAM\b",
    re.I,
)
# Table functions through which clickhouse local reaches outside its own data.
_SQL_REMOTE_RE = re.compile(
    r"\b(?:remote|remoteSecure|cluster|clusterAllReplicas|url|urlCluster|s3|s3Cluster|gcs|azureBlobStorage"
    r"|hdfs|mysql|postgresql|mongodb|redis|sqlite|jdbc|odbc|file|executable)\s*\(",
    re.I,
)
_SQL_TEXT_FLAGS = frozenset({"-c", "--command", "-q", "--query", "-e", "--execute"})
_PG_READ_ONLY_START_RE = re.compile(
    r"^\s*(?:BEGIN(?:\s+(?:TRANSACTION|WORK))?|START\s+TRANSACTION)\s+READ\s+ONLY\s*;", re.I
)
# What ends or rewrites the read-only transaction (measured on PostgreSQL 16:
# SET transaction_read_only=off under psql -1, or COMMIT, let later writes through).
_PG_READ_ONLY_BREAK_RE = re.compile(
    r"\b(?:COMMIT|END|ROLLBACK|ABORT|PREPARE\s+TRANSACTION|SET\s+(?:LOCAL\s+|SESSION\s+)?TRANSACTION"
    r"|SET\s+SESSION\s+CHARACTERISTICS|transaction_read_only|default_transaction_read_only|RESET\s+ALL)\b"
    r"|\\[A-Za-z!]",
    re.I,
)
_CH_READONLY_SET_RE = re.compile(r"^\s*SET\s+readonly\s*=\s*1\s*(?:;|$)", re.I)
_SQL_READ_HINT = (
    " (a read passes unasked as clickhouse-client --readonly=1, or as psql SQL that starts with"
    " BEGIN READ ONLY; and has no COMMIT, END, ROLLBACK, ABORT, SET TRANSACTION, transaction_read_only,"
    " RESET ALL or backslash command, run without -1)"
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
# docker options before the subcommand that take a value (docker -H tcp://x exec ...).
_DOCKER_GLOBAL_VALUE_FLAGS = frozenset({
    "-H", "--host", "--context", "-c", "--config", "-l", "--log-level",
    "--tlscacert", "--tlscert", "--tlskey",
})
_COMPOSE_VALUE_FLAGS = frozenset({
    "-f", "--file", "-p", "--project-name", "--project-directory", "--env-file", "--profile",
    "--ansi", "--progress", "--parallel",
})
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
        cfg.setdefault("exec_targets", []).append(_exec_target(
            args[:end], words[1:2], args[end + 1:] if end < len(args) else words[2:], ctx.get("stdin_body")))
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
        return [("impossible", _SECRET_WRITE_REASON)]
    phrase = " ".join(words[:2]) if " ".join(words[:2]) in _KUBECTL_CHEAP_UNDO else verb
    undo = _KUBECTL_CHEAP_UNDO.get(phrase)
    if undo and (
        any(a.split("=", 1)[0] in ("--all", "--filename", "-k", "--kustomize") for a in rest)
        or any(re.match(r"-[fk](?!-)", a) for a in rest)
        or _selector_keys(rest) or _kubectl_named_objects(rest, len(phrase.split())) > 1
    ):
        return [("approval", "kubectl " + phrase + " with --all, a selector, several objects or a -f/-k manifest reaches beyond one object; the owner decides that blast radius")]
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
    "-o", "--output", "-f", "--filename", "-k", "--kustomize", "--field-manager", "--template",
    "--to-revision",
})


def _kubectl_named_objects(rest, phrase_len):
    """Number of objects a resource verb names on its command line: each
    TYPE/NAME word, or the names after a bare TYPE (times the types in a
    TYPE,TYPE list). Objects read from -f/-k are not counted here."""
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
        return [("impossible", "SQL DROP/TRUNCATE/ALTER ... DROP has no undo" + _SQL_READ_HINT)]
    for m in _SQL_DELETE_RE.finditer(text):
        if not re.search(r"\bWHERE\b", m.group(1), re.I):
            return [("impossible", "SQL DELETE without WHERE has no undo" + _SQL_READ_HINT)]
    if _SQL_DELETE_RE.search(text) or _SQL_WRITE_RE.search(text):
        return [("approval", "SQL write to a database" + _SQL_READ_HINT)]
    if _SQL_SIDE_EFFECT_RE.search(text):
        return [("approval", "SQL calls a server-side effect (session kill, config reload, dblink, COPY PROGRAM)")]
    hits = []
    for body in file_texts:
        if body is None:
            hits.append(("approval", "SQL from a file: its statements are not visible" + _SQL_READ_HINT))
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


def _sql_texts(args, file_texts):
    """The SQL a client runs in argv order: query strings and the bodies of
    the files it reads (``file_texts`` in ``_sql_file_args`` order, None when
    not visible); bodies with no file argument (stdin) come last."""
    files, texts = iter(file_texts), []
    for i, a in enumerate(args):
        if a in _SQL_TEXT_FLAGS and i + 1 < len(args):
            texts.append(args[i + 1])
        elif a.startswith(("--command=", "--query=", "--execute=")):
            texts.append(a.split("=", 1)[1])
        elif _sql_file_args(args[i:i + 2]) and (a in _SQL_FILE_FLAGS or a.startswith(("<", "--file=", "--queries-file="))):
            texts.append(next(files, None))
    return texts + list(files)


def _read_only_marker(program, args, file_texts):
    """True when the server itself keeps the run read-only: clickhouse-client
    with readonly=1 (flag, or the first statement), enforced by whichever
    server answers; or psql whose visible SQL opens a READ ONLY transaction
    that nothing in it can end or switch."""
    texts = _sql_texts(args, file_texts)
    if program.startswith("clickhouse"):
        if "--readonly=1" in args or any(
            a == "--readonly" and args[i + 1:i + 2] == ["1"] for i, a in enumerate(args)
        ):
            return True
        return bool(texts) and None not in texts and bool(_CH_READONLY_SET_RE.match(texts[0]))
    if program != "psql" or not texts or None in texts:
        return False
    if any(a in ("-1", "--single-transaction") for a in args):
        return False
    text = re.sub(r"--[^\n]*|/\*.*?\*/", " ", "\n".join(texts), flags=re.S)
    if not _PG_READ_ONLY_START_RE.match(text):
        return False
    return not _PG_READ_ONLY_BREAK_RE.search(text) and not _SQL_SIDE_EFFECT_RE.search(text)


def _client_sql_hits(program, args, file_texts):
    """Hits of one SQL client run. ``clickhouse local`` without a --path has
    no server and no stored data: it is flagged only when a table function
    reaches outside it."""
    if _read_only_marker(program, args, file_texts):
        return []
    if program == "clickhouse-local" or program == "clickhouse" and args[:1] == ["local"]:
        if not any(a == "--path" or a.startswith("--path=") for a in args):
            texts = [" ".join(args)] + list(file_texts)
            if None in texts:
                return [("approval", "SQL from a file: its statements are not visible")]
            if any(_SQL_REMOTE_RE.search(t) for t in texts):
                return [("approval", "clickhouse local reaches a server or a file through a table function")]
            return []
    return _sql_hits(args, file_texts)


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


_AGENT_NAMES = ("claude", "codex")


def _is_agent(comm, args):
    """True when a process is a claude or codex agent: by its executable
    ``comm`` (intact, may hold spaces) or, for a script runtime, by the script
    path in ``args``. Never a substring of the whole command: paths such as
    ``~/.codex/plugins/...`` or ``~/.claude-accounts/...`` are not agents."""
    if os.path.basename(comm) in _AGENT_NAMES or "/claude/versions/" in comm:
        return True
    if os.path.basename(comm) in ("node", "bun", "deno"):
        for token in args.split()[1:3]:
            if (os.path.basename(token) in _AGENT_NAMES + ("codex.js",)
                    or "/@anthropic-ai/claude-code/" in token):
                return True
    return False


def _inside(path, roots):
    """True when ``path`` is one of ``roots`` or below one."""
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def own_kill_refusals(pids, rows, launchd, caller, uid, platform, cwds, git_dirs, temp_dirs,
                      markers, session):
    """{pid: None when kill-own may SIGTERM it, else the reason}.

    ``rows`` is ``{pid: (uid, ppid, tty, elapsed, comm, args)}`` from one ps
    snapshot (str pids, tty ``??`` when none), ``launchd`` the set of pids
    ``launchctl list`` shows, ``caller`` the pid of the calling process,
    ``cwds`` the working directory of processes whose parent is PID 1,
    ``git_dirs`` the directories that hold a ``.git`` entry, ``temp_dirs`` the
    resolved temp directories, ``markers`` the set of ``CLAUDE_PID`` values found in each
    process environment ps shows, ``session`` the caller's own ``CLAUDE_PID``.
    A pid is allowed when it is the caller's uid,
    not the caller or its ancestor, its subtree holds no tty and no agent, and
    its parent chain reaches the caller's agent session or reaches PID 1
    through no agent with a top process that is not a launchd job and whose
    working directory lies in a git work tree or a temp directory and whose
    subtree carries at least one session marker, every marker naming the
    caller's session or a pid no process holds any more; a subtree whose
    environments ps hides entirely (platform binaries: sleep, tail, sh)
    carries no evidence either way and passes on the rest."""
    if platform != "darwin":
        return {p: p + ": kill-own decides only on macOS" for p in pids}
    children = {}
    for p, row in rows.items():
        children.setdefault(row[1], []).append(p)

    def chain(p):
        seen = []
        while p in rows and p not in seen:
            seen.append(p)
            p = rows[p][1]
        return seen

    ancestors = chain(caller)
    root = next((p for p in ancestors[1:] if _is_agent(rows[p][4], rows[p][5])), None)

    def refusal(pid):
        if not pid.isdigit() or pid not in rows:
            return pid + ": no such process"
        if rows[pid][0] != uid:
            return pid + ": belongs to another user"
        if pid in ancestors:
            return pid + ": is the caller or its ancestor"
        todo, seen = [pid], set()
        while todo:
            p = todo.pop()
            if p in seen:
                continue
            seen.add(p)
            _, _, tty, _, comm, args = rows[p]
            if tty not in ("??", "?", "-", ""):
                return pid + ": process " + p + " in its subtree has terminal " + tty
            if _is_agent(comm, args):
                return pid + ": process " + p + " in its subtree is an agent session"
            todo.extend(children.get(p, []))
        top = pid
        for p in chain(pid)[1:]:
            if p == root:
                return None
            if _is_agent(rows[p][4], rows[p][5]):
                return pid + ": runs under live agent session " + p
            if p == "1":
                break
            top = p
        if top in launchd:
            return pid + ": its top process " + top + " is a launchd job"
        found, readable, todo = set(), False, [top]
        while todo:
            p = todo.pop()
            readable = readable or p in markers
            found |= markers.get(p, set())
            todo.extend(children.get(p, []))
        if not found and readable:
            return pid + ": its top process " + top + " has no agent session marker"
        for m in sorted(found):
            if m != session and m in rows:
                return pid + ": its top process " + top + " belongs to live session " + m
        cwd = cwds.get(top)
        if cwd is None:
            return pid + ": working directory of its top process " + top + " is unknown"
        if not (_inside(cwd, git_dirs) or _inside(cwd, temp_dirs)):
            return pid + ": its top process " + top + " runs outside a git work tree and temp: " + cwd
        return None

    return {p: refusal(p) for p in pids}


def _kill_own_job(args):
    """True when kill only probes (signal 0) or signals a job of this very
    command: a %N job spec, $! or a name bound to it. A process group
    (a negative pid) never passes."""
    probe, operands, i = False, [], 0
    args = [a for a in args if not _REDIRECT.match(a)]  # `2>/dev/null` is the shell's, not an operand
    if args[:1] == ["-0"] or args[:2] in (["-s", "0"], ["-n", "0"]):
        probe, i = True, 1 if args[:1] == ["-0"] else 2
    elif args[:1] in (["-s"], ["-n"]):
        i = 2
    elif args[:1] and args[0].startswith("-") and args[0] != "--":
        i = 1
    if args[i:i + 1] == ["--"]:
        i += 1
    operands = args[i:]
    return bool(operands) and all(
        re.match(r"^%(?:\d+|[%+-])?$", op) or op == "$!" or probe and re.match(r"^[1-9]\d*$", op)
        for op in operands
    )


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
    if program == "kill-own":
        if _is_own_tool(tokens[len(tokens) - len(args) - 1], ctx, "kill-own"):
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
    if program == "kill" and not via_xargs and _kill_own_job(args):
        return []
    if program in ("kill", "pkill", "killall"):
        return [("privileged", "kill: stop a job with its own tool or let it finish; stop your own leftover processes with ~/.claude/tools/kill-own -f '<pattern>' or ~/.claude/tools/kill-own <pid>... (~/.claude/tools/kill-own --list finds them)")]
    if program == "chmod":
        operands = _path_args(args)[1:]
        if operands and all(_ours(p, cfg, ctx) for p in operands):
            return [("ours", "chmod of project-owned scratch paths")]
        return [("privileged", "chmod outside the project-owned paths: file modes are not changed by agents; chmod works on absolute paths inside a worktree, <repo>/tmp or /tmp; to delete such paths run ~/.claude/tools/rm-own -r <path>...")]
    if program == "chown":
        return [("privileged", "chown: file owners are not changed by agents")]
    if program == "eval":
        text = " ".join(raw_args)
        if _runs_computed_command(text):
            return [("privileged", "eval runs a command named by a variable or substitution: run the command directly")]
        return _classify_text(text, cfg, ctx["depth"] + 1, ctx["dir"], None, ctx["cd_seen"])
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
    if program in _WRAPPER_TOOLS:
        # A wrapper carries the gate of the command it runs: `secretctl apply-k8s` writes a live
        # Secret itself; `secretctl run/each/capture`, `check-run` and `wait-for` run the local
        # command after `--`, classed like a bare segment (same directory and bindings).
        if program == "secretctl" and args[:1] == ["apply-k8s"] and not {"-h", "--help"} & set(args):
            return [("impossible", _SECRET_WRITE_REASON)]
        if "--" not in args:
            return []
        inner = args[args.index("--") + 1:]
        raw = ctx.get("raw", tokens)
        return _classify_tokens(inner, cfg, dict(ctx, raw=raw[len(raw) - len(inner):]))
    if program == "docker":
        rest = args[_after_options(args, _DOCKER_GLOBAL_VALUE_FLAGS):]
        if rest[:1] == ["compose"]:
            rest = rest[1:][_after_options(rest[1:], _COMPOSE_VALUE_FLAGS):]
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
        return _client_sql_hits(program, args, [None if k is None else cfg["sql_files"].get(k) for k in keys])
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
        if bind and not plus and (value == "$!" or not any(ch in value for ch in "$`")):
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
        if "|" not in before:
            ctx["stdin_body"] = None  # a heredoc reaches the next command only through a pipe
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
            ctx["stdin_body"] = body
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
            return _client_sql_hits(program, args, [None] * len(_sql_file_args(args)) + [body])
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


_SESSION_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def session_id_ok(session_id):
    """True for a Claude Code session id (a UUID); a hand-piped hook payload
    carries another shape and neither logs nor reads or writes approvals."""
    return bool(_SESSION_ID_RE.match(session_id or ""))


_NO_WORKAROUND = ("\nDo not work around this refusal (other spellings, wrappers, eval, variables,"
                  " scripts): use the named tool or a plan.")


def destructive_decision(*args, **kwargs):
    """``_destructive_decision`` with every refusal ending in one line that
    tells the agent not to work around it: the gate catches mistakes, not a
    deliberate bypass."""
    result = _destructive_decision(*args, **kwargs)
    if result["decision"] == "deny":
        result = dict(result, reason=result["reason"] + _NO_WORKAROUND)
    return result


def _destructive_decision(
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
        result = dict(_command_decision(command or "", cfg, os.path.realpath(cwd) if cwd else ""),
                      exec_targets=cfg.get("exec_targets", []))
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
        reason += ": batch it into a plan script (" + _PLAN_HEADER_LIST + ") run from the main session"
    elif cls == "impossible":
        reason += ("; if the step is needed, write a plan script stating exactly what it does"
                   " (" + _PLAN_HEADER_LIST.replace("# undo", "# undo: none") + "),"
                   " give the owner its path and ask for the line APPROVE <sha256>, then run it from the main"
                   " session as bash <plan>  # DESTRUCTIVE_PLAN sha256=<hex>")
    return {"decision": _DECISION[cls], "class": cls, "reason": reason}


# --- Loop-cost gate -----------------------------------------------------------
# Pure policy behind hooks/loop-cost-gate.py. The journal is one JSONL file per
# repository; rows are dicts {ts (ISO UTC), duration_ms, agent_type, key} for
# a long command, plus {ack, command} on an acknowledgement row (duration 0).

# Tools that run the command after `--`: the destructive gate classes that command, loop-cost keys on it.
_WRAPPER_TOOLS = ("check-run", "wait-for", "secretctl")
# Heads that never carry a command's wait: navigation, shell builtins and the
# small file and text tools that edit or inspect around the real run.
_LOOP_SKIP_HEADS = frozenset({
    "cd", "pushd", "popd", "export", "source", ".", "set", "unset", "true", "false",
    "echo", "printf", "grep", "rg", "tee", "cat", "sed", "awk", "ls", "mv", "cp",
    "rm", "mkdir", "touch", "ln", "test", "[", "date", "sleep", "kill", "wait",
    "pgrep", "ps", "paste",
})
# Wrappers that run the next command unchanged: dropped with their options and
# a duration or count argument (`timeout 600`, `nice -n 5`, `sudo -u x`).
_LOOP_RUNNERS = frozenset({"timeout", "gtimeout", "nice", "caffeinate", "sudo", "xargs", "env", "stdbuf"})
_LOOP_RUNNER_VALUE_OPTIONS = frozenset({"-n", "-u", "-g", "-s", "-k", "-I", "-P", "-L", "-d", "-w"})
_LOOP_RUNNER_ARG = re.compile(r"^[0-9.]+[smhd]?$")
_LOOP_PROGRAM = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_.+-]*$")
_LOOP_LETTER = re.compile(r"[A-Za-z]")
_LOOP_INNER = re.compile(r"""\b(?:ba|z|k)?sh\s+-l?c\s+('([^']*)'|"((?:[^"\\]|\\.)*)")""")
_LOOP_CD_TARGET = re.compile(r"""^\s*cd\s+("[^"]+"|'[^']+'|[^\s;|&]+)\s*(?:&&|;|\n)""")


def loop_cost_cd_target(command):
    """The directory of a leading ``cd <path> &&`` (quotes removed), else None."""
    match = _LOOP_CD_TARGET.match(command or "")
    if not match:
        return None
    return match.group(1).strip("\"'")


def _strip_comments(script):
    """``script`` without its shell comments: a ``#`` that starts a word outside
    quotes, up to the end of its line (a ``# LOOP_COST:`` reason is prose)."""
    out, quote, i, n = [], None, 0, len(script)
    while i < n:
        ch = script[i]
        if quote is None and ch == "#" and (i == 0 or script[i - 1] in " \t\n;&|()"):
            while i < n and script[i] != "\n":
                i += 1
            continue
        if ch == "\\" and quote != "'":
            out.append(script[i:i + 2])
            i += 2
            continue
        if ch in ("'", '"') and quote in (None, ch):
            quote = None if quote else ch
        out.append(ch)
        i += 1
    return "".join(out)


def _loop_unwrap_runners(tokens):
    for _ in range(4):
        while tokens and _ENV_ASSIGN.match(tokens[0]):
            tokens = tokens[1:]
        if not tokens or os.path.basename(tokens[0]) not in _LOOP_RUNNERS:
            return _unwrap(tokens)
        tokens = tokens[1:]
        while tokens and (tokens[0].startswith("-") or _ENV_ASSIGN.match(tokens[0])
                          or _LOOP_RUNNER_ARG.match(tokens[0])):
            tokens = tokens[2:] if tokens[0] in _LOOP_RUNNER_VALUE_OPTIONS else tokens[1:]
        tokens = _unwrap(tokens)
    return tokens


def _loop_heads(command, depth=0):
    heads = set()
    script = _strip_comments(_split_heredocs(command)[0])
    for seg in _segments(_strip_quotes(script)):
        if seg.split()[0] in ("for", "select"):
            continue
        tokens = _loop_unwrap_runners(_tokens_after_keywords(seg))
        if tokens and os.path.basename(tokens[0]) in _WRAPPER_TOOLS and "--" in tokens:
            tokens = _loop_unwrap_runners(_unwrap(tokens[tokens.index("--") + 1:]))
        prog = _program(tokens)
        if not prog or prog in _LOOP_SKIP_HEADS or prog in _NEUTRAL_TOOLS:
            continue
        if _LOOP_PROGRAM.match(prog) and _LOOP_LETTER.search(prog):
            heads.add(prog)
    if depth < 2:
        for match in _LOOP_INNER.finditer(script):
            heads |= _loop_heads(match.group(2) if match.group(2) is not None else match.group(3), depth + 1)
    return heads


def command_identity(command):
    """Identity of a command for the loop-cost gate: the basenames of the
    programs it runs once wrappers (``cd``, assignments, ``env``, ``time``,
    ``check-run --``, ``wait-for ... --``, interpreters with their flags, ``sh
    -c``), pipe filters and redirects are removed, sorted and joined by ``+``.
    An interpreter contributes the script it runs (``python3 -m x`` -> ``x``)."""
    return "+".join(sorted(_loop_heads(command or "")))


def _loop_ts(row):
    try:
        return datetime.datetime.fromisoformat(str(row.get("ts", "")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def loop_cost_live(rows, now, params=None):
    """The journal rows inside the window ending at ``now`` (epoch seconds)."""
    window = float(_merged_params(params)["loop_cost"]["window_seconds"])
    out = []
    for row in rows:
        ts = _loop_ts(row)
        if ts is not None and now - window <= ts <= now + 60:
            out.append(row)
    return out


def loop_cost_row(command, duration_ms, agent_type, now, params=None):
    """The journal row for one finished Bash command, or None when it ran
    shorter than ``long_command_min_seconds``. A wait (``wait-for``) is
    journaled with an empty key: its minutes count, it never forms a key."""
    p = _merged_params(params)["loop_cost"]
    if duration_ms is None or duration_ms < float(p["long_command_min_seconds"]) * 1000:
        return None
    key = "" if classify_command(command, params) == WAIT_FOR else command_identity(command)
    return {
        "ts": loop_cost_iso(now),
        "duration_ms": int(duration_ms),
        "agent_type": agent_type or "main",
        "key": key,
    }


def loop_cost_iso(now):
    return datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_LOOP_DENY_HEAD = (
    "Loop-cost gate: {key} ran long {runs} times for {total} min in this "
    "repository since its last acknowledgement; all long commands here took "
    "{window} min in the last {hours} h.\n"
    "Long commands by identity in the window (top 5 by total minutes):\n"
)
_LOOP_DENY_TAIL = (
    "Trigger: {trigger}.\n"
    "Before the next run, name the incremental form (resume from a saved point, "
    "the affected target only, one full run as the gate before merge) or why "
    "none exists, as a line `# LOOP_COST: <reason>` in the command; the reason "
    "is logged."
)


def _minutes(ms):
    return "%.0f" % (ms / 60000.0)


def loop_cost_reads_journal(command, params=None):
    """Whether a PreToolUse decision on ``command`` depends on the journal: an
    acknowledgement is written to it, a wait or a keyless command never is
    refused."""
    if override_marker(command, "LOOP_COST", params):
        return True
    return bool(command_identity(command)) and classify_command(command, params) != WAIT_FOR


def loop_cost_decision(rows, command, now, agent_type="", params=None):
    """Decide one Bash launch against the repository's loop-cost journal.

    ``rows`` are the journal rows, ``now`` epoch seconds. A wait, and a
    command whose identity never ran long in the window, pass. For an identity
    that did, deny when (a) all long rows of the repository (waits included)
    after the latest acknowledgement of any key sum to
    ``cumulative_budget_seconds`` and the key ran long ``cumulative_own_runs``
    times after its own latest acknowledgement, or (b) the key ran long
    ``repeat_count`` times after it. A ``# LOOP_COST: <reason>`` line returns
    ``override`` with the reason and the key; the caller journals it as an
    acknowledgement row, which restarts the pooled sum and that key's count."""
    reason = override_marker(command, "LOOP_COST", params)
    key = command_identity(command)
    if reason:
        return {"decision": "override", "reason": reason, "key": key}
    if not loop_cost_reads_journal(command, params):
        return {"decision": "allow"}
    p = _merged_params(params)["loop_cost"]
    live = sorted(loop_cost_live(rows, now, params), key=_loop_ts)
    long_rows = [r for r in live if not r.get("ack")]
    acks = [r for r in live if r.get("ack")]
    last_any_ack = max((_loop_ts(r) for r in acks), default=float("-inf"))
    last_ack = max((_loop_ts(r) for r in acks if r.get("key") == key), default=float("-inf"))
    since = [r for r in long_rows if r.get("key") == key and _loop_ts(r) > last_ack]
    pooled_ms = sum(int(r.get("duration_ms") or 0) for r in long_rows if _loop_ts(r) > last_any_ack)
    budget_ms = float(p["cumulative_budget_seconds"]) * 1000
    triggers = []
    if pooled_ms >= budget_ms and len(since) >= int(p["cumulative_own_runs"]):
        triggers.append(
            "%s min waited on long commands here since the last acknowledgement reached "
            "the budget of %s min, and %s ran long %d times" % (
                _minutes(pooled_ms), _minutes(budget_ms), key, len(since)))
    if len(since) >= int(p["repeat_count"]):
        triggers.append("%s ran long %d times" % (key, len(since)))
    if not triggers:
        return {"decision": "allow"}
    return {"decision": "deny", "reason": _loop_deny_text(
        key, since, long_rows, acks, triggers, agent_type, p)}


def _loop_deny_text(key, since, long_rows, acks, triggers, agent_type, p):
    folded = {}
    for row in long_rows:
        entry = folded.setdefault(row.get("key") or "(waits)", [0, 0, set()])
        entry[0] += 1
        entry[1] += int(row.get("duration_ms") or 0)
        entry[2].add(row.get("agent_type") or "main")
    lines = [
        "  %s: %d runs, avg %s min, total %s min, run by %s" % (
            k, n, _minutes(ms / n), _minutes(ms), ", ".join(sorted(types)))
        for k, (n, ms, types) in sorted(folded.items(), key=lambda kv: -kv[1][1])[:5]
    ]
    text = _LOOP_DENY_HEAD.format(
        key=key, runs=len(since),
        total=_minutes(sum(int(r.get("duration_ms") or 0) for r in since)),
        window=_minutes(sum(int(r.get("duration_ms") or 0) for r in long_rows)),
        hours="%g" % (float(p["window_seconds"]) / 3600),
    ) + "\n".join(lines) + "\n"
    if acks:
        text += "Prior acknowledgements in the window:\n" + "\n".join(
            '  "%s" (%s, %s)' % (a.get("ack"), a.get("key") or "-", a.get("ts")) for a in acks[-5:]
        ) + "\n"
    else:
        text += "Prior acknowledgements in the window: none\n"
    text += _LOOP_DENY_TAIL.format(trigger="; ".join(triggers))
    if agent_type:
        text += " Carry the reason into your reply's deviations."
    return text


# --- Headroom gate --------------------------------------------------------------
# Pure policy behind the headroom check in hooks/deny-destructive.py: before a
# process starts inside a pod (kubectl exec), the memory it adds is compared
# with the container's limit minus its working set. The facts are read by
# tools/headroom_read.py.

_QUANTITY_RE = re.compile(r"^(\d+(?:\.\d+)?)([KMGTE]i?|[kmgte])?$")
_QUANTITY_UNITS = {
    "": 1, "k": 1000, "K": 1000, "M": 1000 ** 2, "G": 1000 ** 3, "T": 1000 ** 4,
    "Ki": 1024, "Mi": 1024 ** 2, "Gi": 1024 ** 3, "Ti": 1024 ** 4, "m": 0.001,
}
_XMX_RE = re.compile(r"-Xmx(\d+)([kKmMgG]?)\b")
_JVM_ENV_RE = re.compile(r"(?:^|[\s'\"])(?:[A-Za-z_]*_HEAP_OPTS|JAVA_TOOL_OPTIONS|JVM_OPTS|_JAVA_OPTIONS)=")


def kube_quantity(text):
    """Bytes of a Kubernetes memory quantity (``1Gi``, ``896Mi``, ``1G``), None when unparsable."""
    match = _QUANTITY_RE.match((text or "").strip())
    if not match or (match.group(2) or "") not in _QUANTITY_UNITS:
        return None
    return int(float(match.group(1)) * _QUANTITY_UNITS[match.group(2) or ""])


def _exec_target(kube_args, pod_words, inner, stdin_body=None):
    pod = pod_words[0] if pod_words else ""
    return {
        "context": _flag_value(kube_args, "--context"),
        "kubeconfig": _flag_value(kube_args, "--kubeconfig"),
        "namespace": _flag_value(kube_args, "-n") or _flag_value(kube_args, "--namespace"),
        "pod": pod[4:] if pod.startswith("pod/") else pod,
        "container": _flag_value(kube_args, "-c") or _flag_value(kube_args, "--container"),
        "inner": list(inner),
        "stdin": stdin_body,
    }


def kubectl_exec_targets(command, params=None):
    """Every ``kubectl exec`` the destructive walk finds in a Bash command
    (wrappers, same-command functions and ``sh -c`` strings resolved): dicts
    {context, kubeconfig, namespace, pod, container, inner}."""
    cfg = _decision_cfg(params, {})
    _classify_text(command or "", cfg, 0, "")
    return cfg.get("exec_targets", [])


def _exec_runs(segments, stdin_body, inherited="", depth=0):
    """(program basename, heap text) per program the exec'd command runs. A
    shell is followed into its ``-c`` script or the heredoc fed to it; heap
    text is the env assignments before the program (those of enclosing
    shells included) plus, for ``java``, its arguments. Quoted text passed as
    an argument to another program is never read."""
    runs = []
    for tokens in segments:
        program, args, _ = _command_head(tokens)
        assigned = [t for t in tokens if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t)]
        if not program or program == "export":
            # A bare assignment or an export sets the env of the later segments.
            inherited = " ".join([inherited] + assigned)
            continue
        prefix = tokens[:len(tokens) - len(args) - 1]
        env = " ".join([inherited] + [t for t in prefix if t in assigned])
        if program in _SHELLS and depth < 3:
            flag = next((a for a in args if re.match(r"^-[a-z]*c[a-z]*$", a)), None)
            script = args[args.index(flag) + 1] if flag and args.index(flag) + 1 < len(args) else None
            if script is None and not flag:
                script = stdin_body
            if script:
                runs.extend(_exec_runs(_shell_segments(script), None, env, depth + 1))
                continue
        runs.append((program, env + (" " + " ".join(args) if program == "java" else "")))
    return runs


def headroom_adds(inner, params=None, stdin_body=None):
    """(bytes the exec'd process adds, what it is) or (None, why) when unknown.
    A JVM (a program matching jvm_launchers, or one started with a heap env
    assignment) adds its heap cap plus jvm_overhead_bytes; a JVM with no heap
    cap in the command inherits whatever the container's env sets, so its
    size is unknown. Any other process adds process_floor_bytes."""
    cfg = _merged_params(params)["headroom"]
    heaps = []
    for program, text in _exec_runs([list(inner)], stdin_body):
        if not (any(fnmatch.fnmatch(program, pat) for pat in cfg["jvm_launchers"]) or _JVM_ENV_RE.search(" " + text)):
            continue
        found = _XMX_RE.findall(text)
        if not found:
            return None, "a JVM (%s) with no heap cap (-Xmx) in the command" % program
        number, unit = found[-1]
        heaps.append(int(number) * {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[unit.lower()])
    if not heaps:
        return int(cfg["process_floor_bytes"]), "a process (floor)"
    heap = max(heaps)
    return heap + int(cfg["jvm_overhead_bytes"]), "a JVM, heap %s plus overhead %s" % (
        _mib(heap), _mib(int(cfg["jvm_overhead_bytes"])))


def _mib(n):
    return "%dMi" % round(n / 1024 ** 2)


_HEADROOM_ROUTES = (
    "Run it outside the pod: a throwaway client pod with its own memory limit "
    "(kubectl run --rm with resources), or a local client over a port-forward. "
    "If you have sized the process and it fits, add a line "
    "`# HEADROOM: limit <L> use <U> adds <A>` stating the container's limit, its working set and what the process adds."
)


def headroom_decision(target, adds, facts, params=None):
    """Decision for one exec target. ``adds`` is the pair from headroom_adds;
    ``facts`` is {limit, use, container, error} from the reads (None when the
    reads were skipped). Returns {decision: allow|deny, context, reason}:
    context is set only on unknown headroom and on a near miss."""
    cfg = _merged_params(params)["headroom"]
    name = "%s/%s" % (target["pod"], (facts or {}).get("container") or target.get("container") or "?")
    size, what = adds
    if size is None:
        return {"decision": "deny", "context": None, "target": name, "adds": None, "reason": (
            "headroom-gate: exec into %s starts %s; its memory is unknown (a JVM takes the heap the "
            "container's env sets, possibly the server's own). State the heap (-Xmx) in the command, or run "
            "outside the pod. %s" % (name, what, _HEADROOM_ROUTES))}
    if not facts or facts.get("error"):
        return {"decision": "allow", "reason": None, "context": "headroom unknown (%s): %s" % (
            (facts or {}).get("error") or "not read", name)}
    if facts.get("limit") is None:
        return {"decision": "allow", "reason": None,
                "context": "headroom unknown (no memory limit on %s)" % name}
    free = facts["limit"] - facts["use"]
    numbers = "limit %s, working set %s, free %s; %s adds ~%s" % (
        _mib(facts["limit"]), _mib(facts["use"]), _mib(free), what, _mib(size))
    if free < size:
        return {"decision": "deny", "context": None, "target": name, "limit": facts["limit"],
                "use": facts["use"], "adds": size, "reason": (
            "headroom-gate: exec into %s would exceed its memory limit and get the container OOM-killed: %s. %s"
            % (name, numbers, _HEADROOM_ROUTES))}
    if free < float(cfg["near_miss_factor"]) * size:
        return {"decision": "allow", "reason": None, "context": "headroom near miss on %s: %s" % (name, numbers)}
    return {"decision": "allow", "reason": None, "context": None}


def headroom_params(params=None):
    return _merged_params(params)["headroom"]


def headroom_acknowledged(command, params=None):
    return override_marker(command, "HEADROOM", params) is not None


def headroom_unreadable_pod(target):
    """Facts for a target the reads cannot resolve (``deploy/x``, a missing
    name), else None: the one owner of that rule."""
    if not target["pod"] or "/" in target["pod"]:
        return {"error": "exec %s not resolved to a pod" % (target["pod"] or "target")}
    return None


def headroom_error(exc):
    """The fail-open answer when the headroom check itself breaks."""
    return {"decision": "allow", "reason": None,
            "context": "headroom unknown (headroom check error: %s)" % type(exc).__name__}


def headroom_facts(target, pod_json, top_text):
    """{limit, use, container, error} from ``kubectl get pod -o json`` text and
    ``kubectl top pod --containers --no-headers`` text (either None when unread)."""
    unreadable = headroom_unreadable_pod(target)
    if unreadable:
        return unreadable
    try:
        pod = json.loads(pod_json)
        spec, meta = pod["spec"], pod.get("metadata") or {}
    except (TypeError, ValueError, KeyError):
        return {"error": "pod spec unreadable"}
    containers = spec.get("containers") or []
    name = target.get("container") or (meta.get("annotations") or {}).get(
        "kubectl.kubernetes.io/default-container") or (containers[0]["name"] if containers else "")
    match = [c for c in containers if c.get("name") == name]
    if not match:
        return {"error": "container %s not in pod" % name, "container": name}
    limit = kube_quantity(((match[0].get("resources") or {}).get("limits") or {}).get("memory"))
    if limit is None:
        return {"limit": None, "use": None, "container": name, "error": None}
    for line in (top_text or "").splitlines():
        cols = line.split()
        if len(cols) >= 4 and cols[1] == name:
            use = kube_quantity(cols[3])
            if use is not None:
                return {"limit": limit, "use": use, "container": name, "error": None}
    return {"error": "working set unreadable (metrics-server)", "container": name}


# Fixed first sentence of the background-watch block reason; the hook finds it
# in the transcript to know the session was already returned once.
BACKGROUND_WATCH_MARKER = "A background task of this session is open with no watch on it."

_BACKGROUND_WATCH_REASON = (
    BACKGROUND_WATCH_MARKER + " Task(s): {ids}. The harness notifies only when the whole "
    "command exits; a child that fails under `&` + `wait`, a stalled job or an hours-long "
    "run stays silent (measured: 48 of 286 such turn ends in 14 days ended with the owner "
    "back first, up to 20 h). Arm one Monitor on this run's terminal and failure lines in "
    "its own log (done, exit=, Traceback, gave up, process gone), timeout sized from the "
    "measured time of one unit, at most 60 min, re-armed on expiry while the job lives; "
    "or stop the task. Then start the goal's next independent item in this turn."
)


def background_watch_decision(facts):
    """Return the stop once when a background task is open at the stop and
    unwatched, and it was launched this turn or its last watch ended this turn.

    facts: one snapshot of transcript.background_watch_scan. A watch is a
    Monitor armed after the launch and not ended, or any CronCreate after the
    launch. A launch that only waits is its own event and is exempt. Only this
    hook's marker (``facts["prior_block_seen"]``) allows the second stop:
    ``stop_hook_active`` is set by any Stop hook's block (reply-lint)."""
    ids, waits = [], []
    turn_start = facts["turn_start"]
    for task, info in sorted(facts["tasks"].items()):
        if info["closed"]:
            continue
        if is_pure_wait(info["command"]):
            waits.append(task)
            continue
        launch = info["launch"]
        if any(c > launch for c in facts["crons"]):
            continue
        watches = [m for m in facts["monitors"].values() if m["armed"] > launch]
        if any(m["ended"] is None for m in watches):
            continue
        if launch >= turn_start or any(m["ended"] >= turn_start for m in watches):
            ids.append(task)
    if not ids or facts["prior_block_seen"]:
        return {"decision": "allow", "ids": ids, "waits": waits}
    return {
        "decision": "block",
        "ids": ids,
        "waits": waits,
        "reason": _BACKGROUND_WATCH_REASON.format(ids=", ".join(ids)),
    }
