"""Transcript parsing shared by the stop hooks and their replays.

A transcript is one JSON entry per line. Background-task facts are read from
the harness's own acknowledgements and task notifications."""

import json
import re

# Only the harness's two launch acknowledgements, each the whole tool result:
# an explicit background launch, and a foreground call moved there on timeout.
# Hook denials that mention the background are not launches.
LAUNCH_RE = re.compile(
    r"^(?:Command running in background with ID: ([A-Za-z0-9_-]+)"
    r"|Command did not complete within its \d+s timeout and was moved to the background"
    r"[^\n]*?ID:?\s*([A-Za-z0-9_-]+))"
)
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
    match = LAUNCH_RE.match(result_text(block).strip())
    return next(g for g in match.groups() if g) if match else None


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
    prior_block_seen: ``marker`` appears after the last launch or monitor end."""
    state = {"tasks": {}, "monitors": {}, "crons": [], "markers": []}
    commands = {}  # Bash tool_use id -> command
    monitor_uses = set()
    turn_start = 0
    for index, (raw, entry) in enumerate(entries):
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
        if marker in raw:
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
