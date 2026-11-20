#!/usr/bin/env python3
"""Stop hook (main session): the final reply carries its observation, does
not defer work it could do now, and does not leave the owner's input in plain text.

Returned once, with the rules that failed, when the reply claims completion
without an observation (command, value, time; a regex), or a small-model judge
finds either an owner hand-off (work cannot continue now until the owner's
choice, answer, approval or input, asked in plain text instead of
AskUserQuestion, or a pointer to an earlier message) or an unbacked deferral
(work the agent could do this turn, waiting on nothing, tracked nowhere). The
judge is called only when gate_policy.reply_lint_judge_needed holds (a rule
candidate, or the reply addresses the owner or holds a first-person
commitment): `claude -p --model haiku` sees only the final reply
(gate_policy.judge_prompt), runs with no hooks, plugins, MCP or settings, and
is guarded by REPLY_LINT_JUDGE against recursion. A later stop
(`stop_hook_active`) runs two rule groups, each on a budget counted from this
turn's Stop-hook feedback in the transcript: the deferral family (deferral,
next-step, timed-step; judged while no later-stop deferral return exists this
turn, returned at most once, not when the verdict says handoff) and first-cause
(at most twice per turn; a `Proven:` opening is returned first for its unread records unless a line holds only
`Unread: none` / `нет` / `ничего`, then for its form); handoff, claim and lever stay first-stop only (a later
stop answering a stop-reason return runs the deferral family even when the verdict says handoff). So a
turn holds at most four returns, never a loop.
Stop-reason (first stop only): an interactive main stop (`CLAUDE_CODE_ENTRYPOINT=cli`; any other value or none is
exempt, an unattended caller parses the final message) with nothing of the session live (the payload's
`background_tasks` / `session_crons` lists, else the transcript's live mechanisms and clocks) must close with a
section headed `Why work stopped` / `Почему работа остановлена` (gate_policy.stop_reason_section); a reply without
it is returned once. A reply with the section goes to the judge, so its reason is checked by the other rules; the
next-step and timed-step triggers read the whole reply and the reply without the section, first-cause reads the
reply without the section. A judge error, timeout or
unparsable verdict passes the reply (fail open) and is logged; every record
names the rules that fired. Measurement and acceptance condition: ORIGINS.md.
Not claims (2026-10-15): Russian not-verified disclosures, done words as
section labels, with a manner complement or followed by a negation.
Only payloads whose transcript_path is a file are logged, so probes stay out.
The reason asks for an addendum with only the missing part, never a repeat
of the whole reply.
Decision: gate_policy.reply_lint_decision. Any error exits 0 with no output.

Why a hook: the rule "every done carries proof" was loaded in the reviewed sessions and not applied at the moment
of replying (retro 2026-10-12: 158 claims without observation, ~30 owned
decisions handed to the owner, about 170 h).
"""
import datetime
import json
import os
import sys
import time

_PLUGIN_TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
TOOLS_DIR = (
    _PLUGIN_TOOLS if os.path.isdir(_PLUGIN_TOOLS) else os.path.expanduser("~/.claude/tools")
)
sys.path.insert(0, TOOLS_DIR)

import gate_policy  # noqa: E402
import model_judge  # noqa: E402
import transcript as transcript_scan  # noqa: E402

LOG_PATH = os.path.expanduser("~/.claude/reply-lint-log.jsonl")
JUDGE_ENV = "REPLY_LINT_JUDGE"
# The stop-reason rule applies only to the terminal client (`cli`); every other entrypoint and an absent one are
# exempt: unattended runs (sdk-cli, sdk-ts, sdk-py, the GitHub Action) parse the final message.
ENTRYPOINT_ENV, ATTENDED_ENTRYPOINT = "CLAUDE_CODE_ENTRYPOINT", "cli"
# 5 s under the hook's 30 s timeout; sized from the judge wall time in decisions/lever-starts-from-who-consumes-the-work.md.
JUDGE_TIMEOUT_S = 25
JUDGE_ARGS = model_judge.judge_args("haiku", "You classify one reply. Respond with JSON only.")


def candidates(text, scan):
    """(next-step, timed-step) trigger sentences (``gate_policy.stop_reason_candidates``) after
    gate_policy.exempt_candidates; ``scan()`` (the transcript facts, None without a transcript) runs only on a
    match."""
    next_step = gate_policy.stop_reason_candidates(text, gate_policy.next_step_sentence)
    timed_step = gate_policy.stop_reason_candidates(text, gate_policy.timed_step_sentence)
    if not (next_step or timed_step):
        return "", ""
    return gate_policy.exempt_candidates(next_step, timed_step, scan())


def pinned_while_working(payload, scan):
    """The session has a non-empty open-question file and live work in its transcript scan (CASE-80)."""
    session = payload.get("session_id")
    if not gate_policy.session_id_ok(session):
        return False
    path = gate_policy.open_question_path(os.path.expanduser("~"), session)
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8", errors="replace") as fh:
        pinned_text = fh.read()
    facts = scan() if pinned_text.strip() else None
    return facts is not None and gate_policy.question_pinned_while_working(pinned_text, facts)


class Transcript:
    """The transcript read at most once per stop: its entries and its last background-watch facts."""

    def __init__(self, path):
        self.path = path
        self._entries = self._facts = None

    def entries(self):
        if self._entries is None:
            self._entries = transcript_scan.entries_of(self.path) if os.path.isfile(self.path) else []
        return self._entries

    def facts(self):
        """The background-watch facts at the stop; None when there is no transcript."""
        if self._facts is None and os.path.isfile(self.path):
            for self._facts in transcript_scan.background_watch_scan(self.entries(), "\x00"):
                pass
        return self._facts


def stop_feedback(transcript):
    """This turn's Stop-hook feedback texts at a later stop; [] when the transcript is missing or unreadable,
    which closes both later-stop budgets."""
    try:
        return transcript_scan.stop_feedback_this_turn(transcript.entries())
    except Exception:
        return []


def judge(text, next_step="", lever="", timed_step=""):
    """(verdict or None, error or None) from the nested small-model judge."""
    prompt = gate_policy.judge_prompt(text, next_step, lever, timed_step=timed_step)
    return model_judge.run(JUDGE_ARGS, prompt, JUDGE_TIMEOUT_S, JUDGE_ENV, gate_policy.parse_judge_verdict)


def last_assistant_text(transcript_path):
    """Last non-empty assistant text of a transcript (payload copy wins when present)."""
    last = ""
    with open(transcript_path, errors="replace") as fh:
        for line in fh:
            if not line.startswith("{"):
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("type") != "assistant":
                continue
            content = (entry.get("message") or {}).get("content")
            if isinstance(content, str):
                texts = [content]
            elif isinstance(content, list):
                texts = [str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            else:
                texts = []
            if any(t.strip() for t in texts):
                last = "\n".join(texts)
    return last


def log(record):
    try:
        record["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with open(LOG_PATH, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except BaseException:
        pass


def main():
    raw = sys.stdin.read()
    if not raw.strip() or os.environ.get(JUDGE_ENV):
        return 0
    payload = json.loads(raw)
    # Subagents have their own SubagentStop gates; this one is for the session that talks to the owner.
    if payload.get("agent_type") or payload.get("agent_id"):
        return 0
    text = payload.get("last_assistant_message")
    if not isinstance(text, str) or not text.strip():
        transcript = payload.get("transcript_path") or ""
        if not transcript or not os.path.isfile(transcript):
            return 0
        text = last_assistant_text(transcript)
    stop_hook_active = bool(payload.get("stop_hook_active"))
    transcript_path = payload.get("transcript_path") or ""
    transcript = Transcript(transcript_path)
    outside_terminal = os.environ.get(ENTRYPOINT_ENV) != ATTENDED_ENTRYPOINT
    section = gate_policy.stop_reason_section(text)[1]
    feedback = stop_feedback(transcript) if stop_hook_active else []
    cause_open = gate_policy.first_cause_open(feedback)
    deferral_open = gate_policy.later_deferral_open(feedback)
    stop_reason_answer = gate_policy.stop_reason_answer_open(feedback)
    next_step, lever, timed_step, scan_error, idle = "", "", "", None, None
    # cheap facts first: a payload list or a present section settles the rule without a transcript parse
    if not (outside_terminal or stop_hook_active or section or gate_policy.payload_live(payload)):
        try:
            facts = transcript.facts()
            idle = facts is not None and not gate_policy.live_at_stop(facts)
        except Exception as exc:  # the rule passes; the failure is logged
            scan_error = "%s: %s" % (type(exc).__name__, exc)
    if not stop_hook_active or deferral_open:
        try:
            next_step, timed_step = candidates(text, transcript.facts)
            lever = "" if stop_hook_active else gate_policy.lever_candidate(text)
        except Exception as exc:  # the reply passes; the failure is logged
            scan_error = "%s: %s" % (type(exc).__name__, exc)
    run_judge = gate_policy.reply_lint_judge_needed(text, stop_hook_active, deferral_open, next_step, lever,
                                                    timed_step, section=bool(section) and not outside_terminal)
    started = time.monotonic()
    verdict, judge_error = judge(text, next_step, lever, timed_step) if run_judge else (None, None)
    judge_ms = int((time.monotonic() - started) * 1000) if run_judge else None
    pinned = False
    if verdict and verdict["handoff"] and not stop_hook_active:
        try:
            pinned = pinned_while_working(payload, transcript.facts)
        except Exception as exc:  # the hand-off rule stays in force; the failure is logged
            scan_error = "%s: %s" % (type(exc).__name__, exc)
    decision = gate_policy.reply_lint_decision(text, stop_hook_active, verdict, next_step, lever,
                                            timed_step=timed_step, pinned_while_working=pinned,
                                            first_cause_open=cause_open, later_deferral_open=deferral_open,
                                            stop_idle=bool(idle), stop_reason_answer=stop_reason_answer)
    if os.path.isfile(transcript_path):
        log(
            {
                "session_id": payload.get("session_id") or "",
                "stop_hook_active": stop_hook_active,
                "decision": decision["decision"],
                "rules": decision["rules"],
                "next_step_candidate": next_step,
                "lever_candidate": lever,
                "timed_step_candidate": timed_step,
                "pinned_while_working": pinned,
                "idle": idle,
                "outside_terminal_client": outside_terminal,
                "stop_reason_section": bool(section),
                "scan_error": scan_error,
                "verdict": verdict,
                "judge_error": judge_error,
                "judge_ms": judge_ms,
                "tail": text.strip()[-120:],
            }
        )
    if decision["decision"] == "block":
        json.dump({"decision": "block", "reason": decision["reason"]}, sys.stdout)
    return 0


try:
    main()
except BaseException:
    pass
sys.exit(0)
