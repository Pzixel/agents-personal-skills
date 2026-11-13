#!/usr/bin/env python3
"""PreToolUse gate on AskUserQuestion. First the ask-after-pin rule (gate_policy.ask_after_pin_decision) over
the mtime of the session's pin file and its transcript read backwards to the pin (at most PIN_SCAN_BYTES; no
pin or any read error passes). Then
the rule, fail-open on a payload without a stem included, gate_policy.ask_existing_decision. A question that
passes the spend prefilter (gate_policy.spend_candidate) is judged by the small model (model_judge, the
session's own login); a judge error or timeout passes it (fail open). Every judged call is logged (judged /
fired / judge error) to LOG_PATH, so the unjudged share is readable. Input that is not a JSON object passes."""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import gate_policy  # noqa: E402
import model_judge  # noqa: E402
import transcript  # noqa: E402

LOG_PATH = os.path.expanduser("~/.claude/ask-existing-gate-log.jsonl")
JUDGE_ENV = "ASK_SPEND_JUDGE"
# 5 s under the hook's 30 s timeout; the shared runner's history: p50 3.9 s, p99 9.5 s.
JUDGE_TIMEOUT_S = 25
# The scan stops at the first work call or the first entry older than the pin: the largest read over 1404
# stored dialogs (28 d, `replay.py ask-after-pin`) was 16.6 KB; 12 MB is far over 10x. A cut read passes.
PIN_SCAN_BYTES = 12 << 20
JUDGE_ARGS = model_judge.BASE_ARGS + ["--system-prompt", "You classify one question. Respond with JSON only."]


def log(record):
    try:
        record["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with open(LOG_PATH, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except BaseException:
        pass


def pin_reason(payload):
    try:
        session, path = payload.get("session_id"), payload.get("transcript_path")
        if not gate_policy.session_id_ok(session) or not isinstance(path, str) or not os.path.isfile(path):
            return None
        pin = gate_policy.open_question_path(os.path.expanduser("~"), session)
        if not os.path.isfile(pin) or not open(pin, errors="replace").read().strip():
            return None
        return gate_policy.ask_after_pin_decision(payload, os.path.getmtime(pin),
                                                  transcript.entries_from_end(path, PIN_SCAN_BYTES))
    except OSError:
        return None


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    payload = payload if isinstance(payload, dict) else {}
    reason = pin_reason(payload)
    if reason:
        print(reason, file=sys.stderr)
        return 2
    tool_input = payload.get("tool_input")
    spend = None
    judged = isinstance(tool_input, dict) and not os.environ.get(JUDGE_ENV) and gate_policy.spend_candidate(tool_input)
    if judged:
        spend, error = model_judge.run(JUDGE_ARGS, gate_policy.spend_judge_prompt(tool_input), JUDGE_TIMEOUT_S,
                                       JUDGE_ENV, gate_policy.parse_spend_verdict)
    reason = gate_policy.ask_existing_decision(tool_input, spend)
    if judged:
        log({"session": payload.get("session_id"), "judged": spend is not None, "error": error,
             "spend": bool(spend and spend["spend"]), "sentence": (spend or {}).get("sentence", ""),
             "fired": reason == gate_policy._ASK_SPEND_REASON})
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
