#!/usr/bin/env python3
"""Stop hook (main session, off unless turned on): a fresh reader checks a number before it goes out.

On a stop whose reply states a number after a main-thread data query that no earlier judged stop of this owner
turn covered, `claude -p --model opus` with no tools reads a packet of the turn and the earlier session
(fresh_reader_policy.build_packet) and answers per number whether the meaning of each label behind it was read;
`VERDICT: HOLD` returns the stop once with the read to run, or "not known yet" with what blocks it. After a
HOLD return nothing more is judged until the next owner message, and a `stop_hook_active` stop is never judged,
so an owner turn gets at most one return from this hook. A judge error,
timeout or malformed verdict passes the stop (fail open); every judged stop is logged to
~/.claude/fresh-reader-log.jsonl. Turned on by a line `Fresh reader: on` in the user's own rules file
(~/.codex/AGENTS.md or $CLAUDE_CONFIG_DIR/CLAUDE.md); absent, the hook exits before reading the transcript.
Measurement and decisions: ORIGINS.md (fresh reader), retro-evals CASE-84. Any error exits 0 with no output.
"""
import datetime
import json
import os
import sys
import time

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
sys.path.insert(0, TOOLS_DIR)

import fresh_reader_policy as policy  # noqa: E402
import gate_policy  # noqa: E402
import model_judge  # noqa: E402
import transcript  # noqa: E402

LOG_PATH = os.path.expanduser("~/.claude/fresh-reader-log.jsonl")
STATE_DIR = os.path.expanduser("~/.claude/fresh-reader")
STATE_KEEP_S = 30 * 86400  # a session idle this long has no owner turn left to resume
JUDGE_ENV = "FRESH_READER_JUDGE"
JUDGE_MODEL = "opus"
# Measured opus wall time 4-31 s over 48 calls at 16-215k chars (CASE-84); about twice the maximum. The hook's
# timeout in hooks.json is 70 s.
JUDGE_TIMEOUT_S = 60
JUDGE_ARGS = model_judge.judge_args(JUDGE_MODEL, "You check numbers in one reply. Answer in the requested line format.")


def rules_text():
    config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    texts = []
    for path in (os.path.expanduser("~/.codex/AGENTS.md"), os.path.join(config, "CLAUDE.md")):
        if os.path.isfile(path):
            with open(path, errors="replace") as fh:
                texts.append(fh.read())
    return "\n".join(texts)


def state_path(session):
    return os.path.join(STATE_DIR, session + ".json")


def read_state(session):
    """{"query": last judged query id, "held_turn": turn key of the last HOLD return} of the session."""
    try:
        with open(state_path(session)) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def write_state(session, state):
    """Writes the session's state and drops the files of sessions idle for STATE_KEEP_S."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(state_path(session), "w") as fh:
        json.dump(state, fh)
    cutoff = time.time() - STATE_KEEP_S
    for name in os.listdir(STATE_DIR):
        stale = os.path.join(STATE_DIR, name)
        if os.path.getmtime(stale) < cutoff:
            os.unlink(stale)


def judge(prompt):
    return model_judge.run(JUDGE_ARGS, prompt, JUDGE_TIMEOUT_S, JUDGE_ENV, policy.parse_verdict)


def main():
    raw = sys.stdin.read()
    if not raw.strip() or os.environ.get(JUDGE_ENV):
        return
    payload = json.loads(raw)
    path, session = payload.get("transcript_path") or "", payload.get("session_id")
    if (gate_policy.is_delegate(payload) or payload.get("stop_hook_active") or not gate_policy.session_id_ok(session)
            or not os.path.isfile(path) or not policy.enabled(rules_text())):
        return
    text = payload.get("last_assistant_message")
    if isinstance(text, str) and text.strip() and not policy.states_number(text):
        return
    entries = [entry for _raw, entry in transcript.entries_of(path)]
    reply, reply_uuid = policy.final_reply(entries, text)
    clients = gate_policy._merged_params(gate_policy.load_params())["fresh_reader"]["query_clients"]
    state = read_state(session)
    found = policy.trigger(entries, reply, False, state.get("query"), state.get("held_turn"), clients)
    if not found["fire"]:
        return
    prompt, sizes = policy.build_packet(entries, found["start"], len(entries), reply, found["queries"])
    started = time.monotonic()
    verdict, error = judge(prompt)
    judge_ms = int((time.monotonic() - started) * 1000)
    outcome = policy.decision(verdict)
    write_state(session, policy.next_state(state, found, outcome["returned"]))
    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session_id": session, "transcript_path": path, "reply_uuid": reply_uuid,
        "query_patterns": sorted({k for q in found["new"] for k in q[2]}), "query_count": len(found["new"]),
        "verdict": verdict and verdict["verdict"], "numbers": (verdict or {}).get("numbers", []),
        "hold": (verdict or {}).get("hold", ""), "model": JUDGE_MODEL, "judge_ms": judge_ms,
        "prompt_chars": len(prompt), "sections": sizes, "revision": policy.REVISION,
        "returned": outcome["returned"], "fail_open": error,
    }
    try:
        with open(LOG_PATH, "a") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
    if outcome["returned"]:
        json.dump({"decision": "block", "reason": outcome["reason"]}, sys.stdout)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)
