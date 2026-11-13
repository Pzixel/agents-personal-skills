"""Transcript parsing shared by the stop hooks and their replays.

A transcript is one JSON entry per line. Background-task facts are read from
the harness's own acknowledgements and task notifications."""

import datetime
import json
import re

# Only the harness's three launch acknowledgements, each the whole tool result:
# an explicit background launch, a foreground call moved there on timeout, and
# one moved there so that an arriving message can reach the agent.
# Hook denials that mention the background are not launches. The template alone
# is not enough: a command whose output starts with another transcript's
# acknowledgement (a grep over transcripts) matches it, so launch_id also needs
# the harness's output path `/tasks/<id>.output`, present in every genuine
# acknowledgement of all three forms. Residual: a full-line reprint of another
# session's acknowledgement, path included (no instance in 14 days).
LAUNCH_RE = re.compile(
    r"^(?:Command running in background with ID: ([A-Za-z0-9_-]+)"
    r"|Command did not complete within its \d+s timeout and was moved to the background"
    r"[^\n]*?ID:?\s*([A-Za-z0-9_-]+)"
    r"|Command was moved to the background \(ID: ([A-Za-z0-9_-]+)\))"
)
# The harness's prefix on a user entry that carries a Stop hook's block reason.
STOP_FEEDBACK_PREFIX = "Stop hook feedback:"
MONITOR_STARTED_RE = re.compile(r"^Monitor started \(task ([A-Za-z0-9_-]+)")
_NOTIFICATION_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.DOTALL)
_TASK_ID_RE = re.compile(r"<task-id>([A-Za-z0-9_-]+)</task-id>")
# The harness's acknowledgements of an async Agent spawn and of a scheduled job.
_AGENT_LAUNCHED_RE = re.compile(r"^Async agent launched\b.*?agentId:\s*([A-Za-z0-9_-]+)", re.DOTALL)
# Only a one-shot job is a wait (a clock time); a recurring job is a clock the session wakes on.
_CRON_SCHEDULED_RE = re.compile(r"^Scheduled one-shot task ([A-Za-z0-9_-]+)")
# A recurring job and a /loop wakeup wake the session on a clock (the timed-step condition only).
_CRON_RECURRING_RE = re.compile(r"^Scheduled recurring (?:job|task) ([A-Za-z0-9_-]+)")
_WAKEUP_SCHEDULED_RE = re.compile(r"^Next wakeup scheduled\b[^\n]*?\(in (\d+)s\)")


def _instant(timestamp):
    """UTC datetime of an entry's ISO timestamp (fraction and zone suffix dropped); None when absent."""
    try:
        return datetime.datetime.strptime(str(timestamp or "")[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def epoch(timestamp):
    """Epoch seconds of an entry's ISO timestamp (UTC, `Z` suffix), fraction kept; None when absent or malformed."""
    try:
        return datetime.datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def entries_of(path):
    """(raw line, parsed entry) pairs in transcript order."""
    out = []
    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                out.append((line, json.loads(line)))
            except Exception:
                continue
    return out


def entries_from_end(path, max_bytes, block_size=1 << 16):
    """Parsed entries newest first, read backwards in blocks so a consumer that stops early reads only the
    tail; stops after ``max_bytes`` read. Lines that are not JSON objects are skipped."""
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        position, read, rest = fh.tell(), 0, b""
        while position > 0 and read < max_bytes:
            step = min(block_size, position)
            position -= step
            read += step
            fh.seek(position)
            lines = (fh.read(step) + rest).split(b"\n")
            rest = lines.pop(0) if position > 0 else b""
            for line in reversed(lines):
                line = line.strip()
                if not line.startswith(b"{"):
                    continue
                try:
                    entry = json.loads(line.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                yield entry


def blocks_of(entry):
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def result_text(block):
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(c.get("text") or "") for c in content if isinstance(c, dict))
    return ""


def stop_feedback_this_turn(entries):
    """Texts of the `Stop hook feedback:` user entries of the current turn, in order. The one turn boundary is
    the last `system`/`turn_duration` entry, as in ``background_watch_scan``; a headless transcript, which has
    none, is one turn."""
    feedback = []
    for _raw, entry in entries:
        if entry.get("type") == "system" and entry.get("subtype") == "turn_duration":
            feedback = []
        elif entry.get("type") == "user":
            feedback += [t for t in (str(b.get("text") or "") for b in blocks_of(entry) if b.get("type") == "text")
                         if t.startswith(STOP_FEEDBACK_PREFIX)]
    return feedback


def launch_id(block):
    """Task id when this tool_result block is a background-launch acknowledgement."""
    text = result_text(block).strip()
    match = LAUNCH_RE.match(text)
    if not match:
        return None
    task = next(g for g in match.groups() if g)
    return task if "/tasks/%s.output" % task in text else None


def _snapshot(state, turn_start, marker, index, timestamp, now):
    last_event = max(
        [t["launch"] for t in state["tasks"].values()]
        + [m["ended"] for m in state["monitors"].values() if m["ended"] is not None]
        + [-1]
    )
    return {
        "index": index,
        "timestamp": timestamp,
        "turn_start": turn_start,
        "tasks": {k: dict(v) for k, v in state["tasks"].items()},
        "monitors": {k: dict(v) for k, v in state["monitors"].items()},
        "crons": list(state["crons"]),
        "agents": {k: dict(v) for k, v in state["agents"].items()},
        "agent_calls": len(state["agent_calls"]),
        "cron_jobs": {k: dict(v) for k, v in state["cron_jobs"].items()},
        "recurring_jobs": {k: dict(v) for k, v in state["recurring_jobs"].items()},
        "wakeup_pending": bool(state["wakeup_due"] and now and now < state["wakeup_due"]),
        "wakeup_prompt": state["wakeup_prompt"],
        "prior_block_seen": any(i > last_event for i in state["markers"]),
    }


def background_watch_scan(entries, marker):
    """Yield background-watch facts at every turn end recorded in the transcript
    (a `system`/`turn_duration` entry, over the entries before it) and, last,
    at the end of the transcript (the stop being decided now).

    tasks: id -> {launch, command, closed}; monitors: id -> {armed, ended};
    crons: indexes of CronCreate calls; turn_start: first index of the turn;
    agents: async Agent id -> {launch, closed, call} (call: the Agent tool_use id; closed by its notification or a
    TaskStop, re-opened by a SendMessage to it); agent_calls: Agent calls with no
    tool_result yet; cron_jobs: one-shot job id -> {created, closed, prompt, fired} (closed by
    its `scheduled_task_fire` entry, which also sets fired, or CronDelete); recurring_jobs: recurring
    job id -> {created, closed, prompt} (closed by CronDelete); wakeup_prompt: the ``prompt`` of the
    last ScheduleWakeup call, "" after a ``{"stop": true}`` call; its fire entry does not empty it
    (the fire may be written before the UserPromptSubmit hook reads the transcript); wakeup_pending: the last ScheduleWakeup armed a wakeup that has
    not fired, was not stopped (``{"stop": true}``) and whose due time (the result's timestamp plus its
    ``(in Ns)``) is after the latest entry timestamp seen; a compaction or an owner message does not clear it;
    prior_block_seen: a user entry starting with ``Stop hook feedback:`` and
    carrying ``marker`` follows the last launch or monitor end; a tool result
    that quotes the marker (a read of this source or a review) does not count."""
    state = {"tasks": {}, "monitors": {}, "crons": [], "markers": [],
             "agents": {}, "agent_calls": set(), "cron_jobs": {}, "recurring_jobs": {}, "wakeup_due": None,
             "wakeup_prompt": ""}
    wakeup_uses, wakeup_stops = set(), set()
    now = None  # the latest entry timestamp seen
    commands = {}  # Bash tool_use id -> command
    monitor_uses = set()
    cron_prompts = {}  # CronCreate tool_use id -> the prompt the job will submit
    turn_start = 0
    for index, (_, entry) in enumerate(entries):
        kind = entry.get("type")
        stamp = _instant(entry.get("timestamp"))
        now = max(now, stamp) if now and stamp else stamp or now  # entry timestamps are not monotonic
        if kind == "system" and entry.get("subtype") == "turn_duration":
            yield _snapshot(state, turn_start, marker, index, entry.get("timestamp") or "", now)
            turn_start = index + 1
            continue
        if kind == "system" and entry.get("subtype") == "scheduled_task_fire":
            job = state["cron_jobs"].get(str(entry.get("taskId") or ""))
            if job:
                job["closed"] = True
                job["fired"] = True
            if "/loop wakeup" in str(entry.get("content") or ""):
                state["wakeup_due"] = None
            continue
        if kind == "assistant":
            for block in blocks_of(entry):
                if block.get("type") != "tool_use":
                    continue
                name, args = block.get("name"), block.get("input") or {}
                if name == "Bash":
                    commands[block.get("id")] = str(args.get("command") or "")
                elif name == "Monitor":
                    monitor_uses.add(block.get("id"))
                elif name == "CronCreate":
                    state["crons"].append(index)
                    cron_prompts[block.get("id")] = str(args.get("prompt") or "")
                elif name == "CronDelete":
                    for jobs in (state["cron_jobs"], state["recurring_jobs"]):
                        job = jobs.get(str(args.get("id") or ""))
                        if job:
                            job["closed"] = True
                elif name == "ScheduleWakeup":
                    wakeup_uses.add(block.get("id"))
                    if args.get("stop") is True:
                        wakeup_stops.add(block.get("id"))
                    state["wakeup_prompt"] = "" if args.get("stop") is True else str(args.get("prompt") or "")
                elif name == "Agent":
                    state["agent_calls"].add(block.get("id"))
                elif name == "SendMessage" and str(args.get("to") or "") in state["agents"]:
                    state["agents"][str(args["to"])]["closed"] = False
                elif name in ("TaskStop", "KillShell"):
                    target = str(args.get("task_id") or args.get("shell_id") or "")
                    if target in state["agents"]:
                        state["agents"][target]["closed"] = True
                    if target in state["tasks"]:
                        state["tasks"][target]["closed"] = True
                    if target in state["monitors"] and state["monitors"][target]["ended"] is None:
                        state["monitors"][target]["ended"] = index
            continue
        if kind == "user" and any(
            block.get("type") == "text"
            and str(block.get("text") or "").startswith(STOP_FEEDBACK_PREFIX)
            and marker in str(block.get("text") or "")
            for block in blocks_of(entry)
        ):
            state["markers"].append(index)
        for block in blocks_of(entry):
            if block.get("type") == "tool_result":
                if block.get("tool_use_id") in state["agent_calls"]:
                    state["agent_calls"].discard(block.get("tool_use_id"))
                    spawned = _AGENT_LAUNCHED_RE.match(result_text(block).strip())
                    if spawned:
                        state["agents"][spawned.group(1)] = {
                            "launch": index, "closed": False, "call": block.get("tool_use_id")}
                    continue
                if block.get("tool_use_id") in cron_prompts:
                    job = {"created": index, "closed": False, "prompt": cron_prompts[block.get("tool_use_id")]}
                    scheduled = _CRON_SCHEDULED_RE.match(result_text(block).strip())
                    if scheduled:
                        state["cron_jobs"][scheduled.group(1)] = job
                    recurring = _CRON_RECURRING_RE.match(result_text(block).strip())
                    if recurring:
                        state["recurring_jobs"][recurring.group(1)] = job
                    continue
                if block.get("tool_use_id") in wakeup_uses:
                    armed = _WAKEUP_SCHEDULED_RE.match(result_text(block).strip())
                    if armed and (stamp or now):
                        state["wakeup_due"] = (stamp or now) + datetime.timedelta(seconds=int(armed.group(1)))
                    elif block.get("tool_use_id") in wakeup_stops:
                        state["wakeup_due"] = None
                    continue
                task = launch_id(block)
                if task:
                    state["tasks"][task] = {
                        "launch": index,
                        "command": commands.get(block.get("tool_use_id"), ""),
                        "closed": False,
                    }
                elif block.get("tool_use_id") in monitor_uses:
                    started = MONITOR_STARTED_RE.match(result_text(block).strip())
                    if started:
                        state["monitors"][started.group(1)] = {"armed": index, "ended": None}
            elif block.get("type") == "text":
                for body in _NOTIFICATION_RE.findall(str(block.get("text") or "")):
                    found = _TASK_ID_RE.search(body)
                    if not found:
                        continue
                    task = found.group(1)
                    if task in state["agents"]:
                        state["agents"][task]["closed"] = True
                    if task in state["tasks"]:
                        state["tasks"][task]["closed"] = True
                    monitor = state["monitors"].get(task)
                    if monitor and monitor["ended"] is None and (
                        "<status>" in body or "[Monitor expired" in body
                    ):
                        monitor["ended"] = index
    yield _snapshot(state, turn_start, marker, len(entries), "", now)

