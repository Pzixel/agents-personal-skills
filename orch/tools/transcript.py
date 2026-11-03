"""Transcript parsing shared by the stop hooks and their replays.

A transcript is one JSON entry per line. Background-task facts are read from
the harness's own acknowledgements and task notifications."""

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


def launch_id(block):
    """Task id when this tool_result block is a background-launch acknowledgement."""
    text = result_text(block).strip()
    match = LAUNCH_RE.match(text)
    if not match:
        return None
    task = next(g for g in match.groups() if g)
    return task if "/tasks/%s.output" % task in text else None


def _snapshot(state, turn_start, marker, index, timestamp):
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
        "prior_block_seen": any(i > last_event for i in state["markers"]),
    }


def background_watch_scan(entries, marker):
    """Yield background-watch facts at every turn end recorded in the transcript
    (a `system`/`turn_duration` entry, over the entries before it) and, last,
    at the end of the transcript (the stop being decided now).

    tasks: id -> {launch, command, closed}; monitors: id -> {armed, ended};
    crons: indexes of CronCreate calls; turn_start: first index of the turn;
    prior_block_seen: a user entry starting with ``Stop hook feedback:`` and
    carrying ``marker`` follows the last launch or monitor end; a tool result
    that quotes the marker (a read of this source or a review) does not count."""
    state = {"tasks": {}, "monitors": {}, "crons": [], "markers": []}
    commands = {}  # Bash tool_use id -> command
    monitor_uses = set()
    turn_start = 0
    for index, (_, entry) in enumerate(entries):
        kind = entry.get("type")
        if kind == "system" and entry.get("subtype") == "turn_duration":
            yield _snapshot(state, turn_start, marker, index, entry.get("timestamp") or "")
            turn_start = index + 1
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
                elif name in ("TaskStop", "KillShell"):
                    target = str(args.get("task_id") or args.get("shell_id") or "")
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
                    if task in state["tasks"]:
                        state["tasks"][task]["closed"] = True
                    monitor = state["monitors"].get(task)
                    if monitor and monitor["ended"] is None and (
                        "<status>" in body or "[Monitor expired" in body
                    ):
                        monitor["ended"] = index
    yield _snapshot(state, turn_start, marker, len(entries), "")
