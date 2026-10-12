#!/usr/bin/env python3
"""scout — find code outside the caller's context: the script searches, the model picks.

One Bash call takes questions and returns verified pointers (file:line). The
pipeline is data: a named configuration is a list of
stage records, so cheap stages can be repeated or run in parallel and the
expensive model is only asked about the remainder.

Stage kinds
  rg_symbol       no model. If the question names a symbol, rg for definition
                  patterns; a single hit is the answer at zero token cost.
  query_writer    one tool-less call for the whole batch, returns <=4
                  (regex, glob) pairs per question; the SCRIPT runs rg.
  selector        N parallel tool-less calls; each sees the question and the
                  numbered candidates in its own shuffled order and returns a
                  candidate ID or "none". File/line/quote of an accepted answer
                  come from the script's candidate table, never from model text.
  agentic_finder  one call WITH Read/Grep/Glob under the scout-bounds hook; its
                  pointer is verified mechanically against the real file.
  confirm         one tool-less call at a higher tier that sees a +-15 line
                  excerpt around the accepted pointer and judges relevance.

Every model call is logged as one JSON line to ~/.claude/scout-log.jsonl.
The account is inherited from CLAUDE_CONFIG_DIR; scout never sets it.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scout_policy as sp  # noqa: E402

HOME = os.path.expanduser("~")
LOG_PATH = os.path.join(HOME, ".claude", "scout-log.jsonl")
BOUNDS_HOOK = os.path.join(HOME, ".claude", "hooks", "scout-bounds.py")
# The bounds hook is registered in NO settings.json. Setting CLAUDE_SCOUT=1 alone
# leaves it inert, so every agentic_finder call writes its own --settings file
# that registers the PreToolUse matcher AND exports the env var (see
# finder_settings_file). Each actual clamp is appended here, one JSON line, which
# is the only evidence that the hook fired on a given run.
BOUNDS_LOG = os.path.join(HOME, ".claude", "scout-bounds-clamps.jsonl")
# Headless calls otherwise load every MCP server + plugin definition into the
# base prompt (~75k tokens). An empty MCP config drops it to ~11k.
MCP_EMPTY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scout-mcp-empty.json")

RG_HITS_PER_PATTERN = 40
RG_CONTEXT = 2
MAX_CANDIDATES = 40
CALL_TIMEOUT = 300
# Questions per agentic finder call. Per-call cost is dominated by a fixed ~30k
# base prompt, so cost tracks calls per question; 8 is the batch size measured
# in lab report e5-endtoend-test.md ($0.0329/question).
FINDER_BATCH = 8


# --------------------------------------------------------------------------
# configuration: stages are data
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Stage:
    kind: str
    model: str | None = None
    copies: int = 1
    threshold: int = 1
    round: int = 1
    # agentic_finder only: register the scout-bounds PreToolUse clamp for this
    # call. This is the measured difference between the b1 and b2 configs.
    bounds: bool = True


_B = [
    Stage("rg_symbol"),
    Stage("query_writer", "haiku"),
    Stage("selector", "haiku", copies=3, threshold=2),
    Stage("query_writer", "haiku", round=2),
    Stage("selector", "haiku", copies=3, threshold=2),
    Stage("selector", "sonnet", copies=1, threshold=1),
    Stage("agentic_finder", "opus"),
]

CONFIGS: dict[str, list[Stage]] = {
    # b2 — the measured winner. One agentic Opus call under the bounds clamp.
    "b2": [Stage("agentic_finder", "opus", bounds=True)],
    # b1 — the same call with the clamp off. Kept because it is the measured
    # comparison point, not because it is a recommended setting.
    "b1": [Stage("agentic_finder", "opus", bounds=False)],
    "baseline": [Stage("agentic_finder", "opus")],
    "a": [
        Stage("agentic_finder", "haiku"),
        Stage("agentic_finder", "sonnet"),
        Stage("agentic_finder", "opus"),
    ],
    "b": list(_B),
    "c": list(_B) + [Stage("confirm", "sonnet")],
}

# DEFAULT CONFIG: set by the 2026-09-18 end-to-end measurement on 58 held-out
# questions (lab report e5-endtoend-test.md).
#   b2  one agentic opus call WITH the bounds clamp: accuracy 0.897 [0.788, 0.961],
#       6 accepted-wrong, $1.9084 total = $0.0329/question.   <- winner
#   b1  the same call without the clamp:            accuracy 0.845 [0.726, 0.927],
#       9 accepted-wrong, $3.6391 total = $0.0627/question.
#   b2 dominates b1 on every axis (McNemar b=4 c=1 p=0.375, so the accuracy edge
#   alone is not significant; the 1.9x cost and 3-fewer accepted-wrong are the
#   decisive margins).
#   The cheap staged pipeline reached 0.879 [0.767, 0.950] with 7 accepted-wrong
#   but cost $0.2185/question, 6.6x b2, so it loses on cost, not accuracy. Its
#   ceiling is the haiku retrieval stage (recall@40 = 0.905), not the selector.
DEFAULT_CONFIG = "b2"


# --------------------------------------------------------------------------
# run state
# --------------------------------------------------------------------------


@dataclass
class Candidate:
    cid: int
    path: str
    line: int
    quote: str
    context: str


@dataclass
class Question:
    qid: str
    text: str
    candidates: list[Candidate] = field(default_factory=list)
    searched: list[str] = field(default_factory=list)
    pointer: tuple[str, int, str] | None = None
    negative: bool = False
    resolved: bool = False
    note: str = ""
    stage_path: list[str] = field(default_factory=list)


class CostCap(Exception):
    pass


class Ledger:
    def __init__(self, cap: float):
        self.cap = cap
        self.spent = 0.0
        self.calls = 0

    def check(self):
        if self.spent >= self.cap:
            raise CostCap(f"cost cap reached: ${self.spent:.4f} of ${self.cap:.2f}")

    def add(self, usd: float):
        self.spent += usd or 0.0
        self.calls += 1


def account_name() -> str:
    cfg = os.environ.get("CLAUDE_CONFIG_DIR", "")
    return "work" if cfg.rstrip("/").endswith("/work") else "default"


def log_call(run_id, config, qid, stage_kind, model, result, outcome, extra=None):
    usage = (result or {}).get("usage") or {}
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_id": run_id,
        "config": config,
        "question_id": qid,
        "stage": stage_kind,
        "model": model,
        "tokens": {
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "cache_read": usage.get("cache_read_input_tokens"),
            "cache_creation": usage.get("cache_creation_input_tokens"),
        },
        "total_cost_usd": (result or {}).get("total_cost_usd"),
        "outcome": outcome,
        "account": account_name(),
    }
    if extra:
        record.update(extra)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------
# mechanical execution: headless calls and rg
# --------------------------------------------------------------------------


def headless(model, system_prompt, schema, prompt, repo, tools=(), settings=None, env_extra=None):
    """One `claude -p` call. Returns the parsed JSON envelope, or None on failure."""
    cmd = [
        "claude",
        "-p",
        prompt,
        "--model",
        model,
        "--system-prompt",
        system_prompt,
        "--json-schema",
        json.dumps(schema),
        "--output-format",
        "json",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--mcp-config",
        MCP_EMPTY,
    ]
    if settings:
        cmd += ["--settings", settings]
    cmd.append("--tools")
    if tools:
        cmd += list(tools)
        cmd += ["--allowed-tools"] + list(tools)
    else:
        cmd.append("")
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(
            cmd,
            cwd=repo,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=CALL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"_error": "timeout"}
    if proc.returncode != 0:
        return {"_error": (proc.stderr or proc.stdout or "").strip()[:500]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"_error": "unparseable envelope: " + proc.stdout.strip()[:300]}


def structured(result):
    if not result or result.get("_error"):
        return None
    out = result.get("structured_output")
    if isinstance(out, dict):
        return out
    raw = result.get("result")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return None


def rg_lines(repo, regex, glob=None, limit=RG_HITS_PER_PATTERN):
    cmd = ["rg", "--line-number", "--no-heading", "--color", "never", "--max-filesize", "1M"]
    if glob and glob not in ("*", "**", "**/*"):
        cmd += ["-g", glob]
    cmd += ["-e", regex, "."]
    try:
        proc = subprocess.run(
            cmd, cwd=repo, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL
        )
    except (OSError, subprocess.SubprocessError):
        return []
    hits = []
    for raw in proc.stdout.splitlines()[:limit]:
        parts = raw.split(":", 2)
        if len(parts) < 3 or not parts[1].isdigit():
            continue
        path = parts[0][2:] if parts[0].startswith("./") else parts[0]
        hits.append((path, int(parts[1]), parts[2]))
    return hits


def read_lines(repo, path):
    abs_path = path if os.path.isabs(path) else os.path.join(repo, path)
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def excerpt(repo, path, line, radius):
    lines = read_lines(repo, path)
    start = max(1, line - radius)
    end = min(len(lines), line + radius)
    return "\n".join(f"{i:>6}| {lines[i - 1]}" for i in range(start, end + 1))


def add_candidates(repo, question, hits):
    seen = {(c.path, c.line) for c in question.candidates}
    for path, line, text in hits:
        if (path, line) in seen or len(question.candidates) >= MAX_CANDIDATES:
            continue
        seen.add((path, line))
        question.candidates.append(
            Candidate(
                cid=len(question.candidates) + 1,
                path=path,
                line=line,
                quote=text.strip(),
                context=excerpt(repo, path, line, RG_CONTEXT),
            )
        )


# --------------------------------------------------------------------------
# stage runners
# --------------------------------------------------------------------------

SYMBOL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
DEF_KEYWORDS = "struct|enum|fn|class|def|trait|type|const|let|interface|func|impl"
STOPWORDS = {
    "where", "what", "which", "does", "the", "and", "for", "from", "with", "that",
    "this", "file", "line", "code", "function", "method", "class", "module", "called",
    "defined", "registered", "handler", "route", "config", "value", "when", "how",
    "why", "who", "runs", "run", "used", "uses", "using", "there", "any", "into",
    "not", "are", "was", "has", "have", "its", "it", "in", "of", "to", "is", "do",
}


def question_symbols(text):
    out = []
    for token in re.findall(r"`([^`]+)`|\"([^\"]+)\"|'([^']+)'", text):
        for piece in token:
            if piece.strip():
                out.append(piece.strip())
    for token in SYMBOL_RE.findall(text):
        if token.lower() in STOPWORDS:
            continue
        if "_" in token or re.search(r"[a-z][A-Z]", token) or token[0].isupper():
            out.append(token)
    seen, uniq = set(), []
    for token in out:
        if token not in seen:
            seen.add(token)
            uniq.append(token)
    return uniq[:4]


def stage_rg_symbol(repo, question, *_):
    """No model. A single definition hit is the answer for free."""
    for symbol in question_symbols(question.text):
        pattern = rf"\b({DEF_KEYWORDS})\s+{re.escape(symbol)}\b"
        hits = rg_lines(repo, pattern, limit=5)
        question.searched.append(pattern)
        if len(hits) == 1:
            path, line, text = hits[0]
            add_candidates(repo, question, hits)
            question.pointer = (path, line, text.strip())
            return sp.AgreementVerdict(sp.ACCEPTED, "rg", 1)
        add_candidates(repo, question, hits)
    return sp.AgreementVerdict(sp.NO_AGREEMENT, None, 0)


QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "string"},
                    "patterns": {
                        "type": "array",
                        "maxItems": 4,
                        "items": {
                            "type": "object",
                            "properties": {
                                "regex": {"type": "string"},
                                "glob": {"type": "string"},
                            },
                            "required": ["regex", "glob"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["question_id", "patterns"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["queries"],
    "additionalProperties": False,
}

QUERY_SYSTEM = (
    "You write ripgrep searches for a code repository. You cannot read files. "
    "For each question return at most 4 (regex, glob) pairs, most specific first. "
    "Use Rust regex syntax, no lookaround. Prefer definition/registration shapes "
    "(fn NAME, def NAME, class NAME, .route(, register, NAME =) over bare words. "
    "glob is a path filter such as '*.py', 'src/**/*.rs' or '*' for any file."
)


def stage_query_writer(repo, questions, stage, ctx):
    """One batched call, then the SCRIPT runs rg under hard limits."""
    ctx.ledger.check()
    lines = []
    for q in questions:
        lines.append(f"[{q.qid}] {q.text}")
        if stage.round > 1 and q.searched:
            lines.append(f"    already searched (found nothing useful): {q.searched[-8:]}")
    prompt = (
        f"Repository root: {ctx.repo}\nQuestions:\n" + "\n".join(lines) +
        ("\nRound 2: propose DIFFERENT patterns from the ones already searched."
         if stage.round > 1 else "")
    )
    result = headless(stage.model, QUERY_SYSTEM, QUERY_SCHEMA, prompt, repo)
    ctx.ledger.add((result or {}).get("total_cost_usd") or 0.0)
    data = structured(result)
    log_call(
        ctx.run_id, ctx.config, "batch", "query_writer", stage.model, result,
        "ok" if data else "failed",
        {"questions": [q.qid for q in questions], "error": (result or {}).get("_error")},
    )
    by_id = {q.qid: q for q in questions}
    for entry in (data or {}).get("queries", []):
        q = by_id.get(str(entry.get("question_id")))
        if q is None:
            continue
        for pat in entry.get("patterns", [])[:4]:
            regex = (pat.get("regex") or "").strip()
            if not regex:
                continue
            q.searched.append(regex)
            add_candidates(repo, q, rg_lines(repo, regex, pat.get("glob")))
    return {q.qid: sp.AgreementVerdict(sp.NO_AGREEMENT, None, 0) for q in questions}


SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_id": {"type": "string"},
        "why": {"type": "string"},
    },
    "required": ["candidate_id"],
    "additionalProperties": False,
}

SELECT_SYSTEM = (
    "You pick the one code location that answers a question. You cannot read files: "
    "decide only from the numbered candidates shown. Return the candidate number as "
    "candidate_id, or the string 'none' if no candidate answers the question. "
    "Prefer the place where the thing is defined or registered over a mention, "
    "import, doc comment, annotation or test. Never invent a number."
)


def render_candidates(question, order):
    blocks = []
    for cid in order:
        cand = next(c for c in question.candidates if c.cid == cid)
        blocks.append(f"[{cand.cid}] {cand.path}:{cand.line}\n{cand.context}")
    return "\n\n".join(blocks)


def stage_selector(repo, question, stage, ctx):
    if not question.candidates:
        return sp.AgreementVerdict(sp.NO_AGREEMENT, None, 0)
    ids = [c.cid for c in question.candidates]
    votes = []

    def one_copy(copy_index):
        rng = random.Random(f"{ctx.seed}|{question.qid}|{ctx.stage_index}|{copy_index}")
        order = list(ids)
        rng.shuffle(order)
        prompt = (
            f"Question: {question.text}\n\nCandidates:\n{render_candidates(question, order)}"
        )
        result = headless(stage.model, SELECT_SYSTEM, SELECT_SCHEMA, prompt, repo)
        data = structured(result)
        vote = (data or {}).get("candidate_id")
        log_call(
            ctx.run_id, ctx.config, question.qid, "selector", stage.model, result,
            f"vote={vote}", {"copy": copy_index, "error": (result or {}).get("_error")},
        )
        return (result or {}).get("total_cost_usd") or 0.0, vote

    ctx.ledger.check()
    with ThreadPoolExecutor(max_workers=max(1, stage.copies)) as pool:
        for cost, vote in pool.map(one_copy, range(stage.copies)):
            ctx.ledger.add(cost)
            votes.append(vote)

    verdict = sp.decide_agreement(votes, stage.threshold)
    if verdict.outcome == sp.ACCEPTED:
        # The pointer comes from the script's table, by ID. Model text is only an ID.
        try:
            cid = int(str(verdict.candidate_id).strip())
        except ValueError:
            return sp.AgreementVerdict(sp.NO_AGREEMENT, None, verdict.votes)
        cand = next((c for c in question.candidates if c.cid == cid), None)
        if cand is None:
            return sp.AgreementVerdict(sp.NO_AGREEMENT, None, verdict.votes)
        question.pointer = (cand.path, cand.line, cand.quote)
    return verdict


FINDER_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "found": {"type": "boolean"},
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "quote": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["id", "found", "file", "line", "quote", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["answers"],
    "additionalProperties": False,
}

FINDER_SYSTEM = (
    "You locate, for each question you are given, the one place in a repository that "
    "answers it. Use Grep and Glob to narrow down, Read only the few lines you need. "
    "Return one answers entry per question id, in the order asked, with the "
    "repository-relative file path, the 1-based line number, and the exact text of that "
    "line as quote. The quote is checked against the file, so copy it character for "
    "character. If nothing in the repository answers a question, set found=false for "
    "that id. Never merge or skip an id."
)


def finder_settings_file(ctx):
    if ctx.settings_path:
        return ctx.settings_path
    path = os.path.join(
        ctx.tmpdir, f"scout-settings-{ctx.run_id}.json"
    )
    # Two halves, both required. The matcher makes the hook run at all; the env
    # block is what makes it do anything, because scout-bounds.py returns
    # immediately unless CLAUDE_SCOUT=1. It is set here as well as in the child
    # environment so the clamp does not depend on how the CLI forwards env to
    # hook processes. The shell wrapper appends every non-empty hook response to
    # BOUNDS_LOG and passes it through unchanged, so a clamp leaves a trace
    # without changing what the CLI receives.
    command = (
        f'o=$(python3 {BOUNDS_HOOK}); '
        f'[ -n "$o" ] && printf \'%s\\n\' "$o" >> {BOUNDS_LOG}; '
        f'printf \'%s\' "$o"'
    )
    payload = {
        "env": {"CLAUDE_SCOUT": "1"},
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Grep|Read",
                    "hooks": [{"type": "command", "command": command, "timeout": 15}],
                }
            ]
        },
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    ctx.settings_path = path
    return path


def finder_answer_verdict(repo, question, data):
    """Turn one finder answer into (outcome, verdict), setting question.pointer."""
    if data is None:
        return "failed", sp.AgreementVerdict(sp.NO_AGREEMENT, None, 0)
    if not data.get("found"):
        return "none", sp.AgreementVerdict(sp.AGREED_NONE, None, 1)
    path = str(data.get("file") or "")
    if path.startswith(repo):
        path = os.path.relpath(path, repo)
    lines = read_lines(repo, path)
    check = sp.verify_pointer(lines, data.get("line") or 0, str(data.get("quote") or ""))
    if check.outcome in (sp.OK, sp.CORRECTED):
        question.pointer = (path, check.line, lines[check.line - 1].strip())
        return check.outcome, sp.AgreementVerdict(sp.ACCEPTED, "finder", 1)
    return "rejected:" + check.reason, sp.AgreementVerdict(sp.NO_AGREEMENT, None, 0)


def stage_agentic_finder(repo, questions, stage, ctx):
    """One agentic call per batch of up to FINDER_BATCH questions.

    Cost per question is dominated by the fixed CLI base prompt (~30k tokens per
    call), so questions are batched: the 2026-09-18 measurement priced this
    configuration at $0.0329/question against $0.16-$0.32 one call per question.
    """
    verdicts = {}
    for start in range(0, len(questions), FINDER_BATCH):
        batch = questions[start:start + FINDER_BATCH]
        ctx.ledger.check()
        listed = "\n".join(f"- {q.qid}: {q.text}" for q in batch)
        prompt = (
            f"Repository root: {repo}\n"
            f"Answer each of these {len(batch)} questions, one entry per id:\n{listed}"
        )
        result = headless(
            stage.model,
            FINDER_SYSTEM,
            FINDER_SCHEMA,
            prompt,
            repo,
            tools=("Read", "Grep", "Glob"),
            settings=finder_settings_file(ctx) if stage.bounds else None,
            env_extra={"CLAUDE_SCOUT": "1"} if stage.bounds else None,
        )
        total = (result or {}).get("total_cost_usd") or 0.0
        ctx.ledger.add(total)
        data = structured(result)
        answers = {}
        for item in ((data or {}).get("answers") or []):
            if isinstance(item, dict):
                answers[str(item.get("id"))] = item
        share = total / len(batch)
        amortized = dict(result or {})
        amortized["total_cost_usd"] = share
        for q in batch:
            outcome, verdict = finder_answer_verdict(repo, q, answers.get(q.qid))
            verdicts[q.qid] = verdict
            log_call(
                ctx.run_id, ctx.config, q.qid, "agentic_finder", stage.model,
                amortized, outcome,
                {
                    "error": (result or {}).get("_error"),
                    "batch_size": len(batch),
                    "batch_cost_usd": total,
                },
            )
    return verdicts


CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {"relevant": {"type": "boolean"}, "why": {"type": "string"}},
    "required": ["relevant", "why"],
    "additionalProperties": False,
}

CONFIRM_SYSTEM = (
    "You judge whether one code excerpt really answers a question. You cannot read "
    "other files. A quote that merely mentions the subject (an import, a doc comment, "
    "an annotation, a test, a re-export) is NOT relevant; the place where the thing is "
    "defined, registered or executed is relevant. Answer relevant true or false."
)


def stage_confirm(repo, question, stage, ctx):
    if not question.pointer:
        return True
    ctx.ledger.check()
    path, line, _ = question.pointer
    prompt = (
        f"Question: {question.text}\n\nExcerpt {path}:{line}\n"
        + excerpt(repo, path, line, 15)
    )
    result = headless(stage.model, CONFIRM_SYSTEM, CONFIRM_SCHEMA, prompt, repo)
    ctx.ledger.add((result or {}).get("total_cost_usd") or 0.0)
    data = structured(result)
    relevant = True if data is None else bool(data.get("relevant"))
    log_call(
        ctx.run_id, ctx.config, question.qid, "confirm", stage.model, result,
        "relevant" if relevant else "rejected", {"pointer": f"{path}:{line}"},
    )
    return relevant


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


@dataclass
class RunContext:
    repo: str
    config: str
    run_id: str
    seed: int
    ledger: Ledger
    tmpdir: str
    stage_index: int = 0
    settings_path: str | None = None


def run_pipeline(questions, stages, ctx):
    kinds = [s.kind for s in stages]
    confirm_stage = next((s for s in stages if s.kind == "confirm"), None)
    active = list(questions)
    stopped = None

    for index, stage in enumerate(stages):
        if not active:
            break
        if stage.kind == "confirm":
            continue
        ctx.stage_index = index
        try:
            if stage.kind == "query_writer":
                verdicts = stage_query_writer(ctx.repo, active, stage, ctx)
            elif stage.kind == "agentic_finder":
                verdicts = stage_agentic_finder(ctx.repo, active, stage, ctx)
            else:
                runner = {
                    "rg_symbol": stage_rg_symbol,
                    "selector": stage_selector,
                }[stage.kind]
                verdicts = {}
                for q in active:
                    verdicts[q.qid] = runner(ctx.repo, q, stage, ctx)
        except CostCap as exc:
            stopped = str(exc)
            break

        still = []
        for q in active:
            verdict = verdicts.get(q.qid) or sp.AgreementVerdict(sp.NO_AGREEMENT, None, 0)
            q.stage_path.append(f"{stage.kind}:{verdict.outcome}")
            decision = sp.decide_escalation(verdict.outcome, index, kinds)
            if decision.action == sp.ACCEPT and not decision.negative and confirm_stage:
                try:
                    if not stage_confirm(ctx.repo, q, confirm_stage, ctx):
                        q.pointer = None
                        q.stage_path.append("confirm:rejected")
                        decision = sp.decide_escalation(sp.NO_AGREEMENT, index, kinds)
                except CostCap as exc:
                    stopped = str(exc)
            if decision.action == sp.ACCEPT:
                q.resolved = True
                q.negative = decision.negative
                q.note = decision.reason
            elif decision.action == sp.GIVE_UP:
                q.resolved = True
                q.note = "no answer found"
            else:
                still.append(q)
        active = still
        if stopped:
            break

    for q in active:
        q.note = stopped or "pipeline exhausted"
    return stopped


def summarize(questions, stopped, ledger):
    out = []
    for q in questions:
        if q.negative:
            out.append(f"{q.qid}: NEGATIVE (no such thing in this repo) -- {q.text[:60]}")
        elif q.pointer:
            path, line, quote = q.pointer
            out.append(f"{q.qid}: {path}:{line}  {quote[:70]}")
        else:
            out.append(f"{q.qid}: UNRESOLVED ({q.note or 'no answer'})")
    out.append(f"-- {ledger.calls} model calls, ${ledger.spent:.4f}")
    if stopped:
        out.append(f"-- STOPPED: {stopped}")
    return out[:40]


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="scout.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Find code outside the caller's context. Input: questions as positional\n"
            "arguments or --questions-file (one question per line). Output: a summary\n"
            "of at most 40 lines on stdout, one line per question, of the form\n"
            "  q1: src/app.py:142  <the matched line>\n"
            "with negative answers printed as NEGATIVE and failures as UNRESOLVED,\n"
            "followed by the model-call count and the run cost.\n"
            "Every model call is appended to ~/.claude/scout-log.jsonl."
        ),
        epilog="configurations: " + ", ".join(CONFIGS),
    )
    ap.add_argument("questions", nargs="*", help="one or more questions about the repository")
    ap.add_argument("--questions-file", help="file with one question per line (# comments ok)")
    ap.add_argument("--repo", default=os.getcwd(), help="repository root to search (default: cwd)")
    ap.add_argument("--config", default=DEFAULT_CONFIG,
                    help=f"pipeline configuration (default: {DEFAULT_CONFIG})")
    ap.add_argument("--cost-cap", type=float, default=15.0,
                    help="stop cleanly when this run's total_cost_usd reaches this (default: 15.0)")
    ap.add_argument("--seed", type=int, default=0, help="shuffle seed, for reproducible runs")
    ap.add_argument("--json", action="store_true", help="also print the full result as JSON")
    args = ap.parse_args(argv)

    texts = list(args.questions)
    if args.questions_file:
        with open(args.questions_file, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if line and not line.startswith("#"):
                    texts.append(line)
    if not texts:
        ap.error("no questions given")
    if args.config not in CONFIGS:
        ap.error(f"unknown config {args.config!r}; known: {', '.join(CONFIGS)}")

    repo = os.path.abspath(args.repo)
    questions = [Question(qid=f"q{i + 1}", text=t) for i, t in enumerate(texts)]
    ctx = RunContext(
        repo=repo,
        config=args.config,
        run_id=uuid.uuid4().hex[:12],
        seed=args.seed,
        ledger=Ledger(args.cost_cap),
        tmpdir=os.path.join(HOME, ".claude"),
    )
    stopped = run_pipeline(questions, CONFIGS[args.config], ctx)

    for line in summarize(questions, stopped, ctx.ledger):
        print(line)
    if args.json:
        print(json.dumps(
            {
                "run_id": ctx.run_id,
                "config": args.config,
                "cost_usd": round(ctx.ledger.spent, 6),
                "results": [
                    {
                        "id": q.qid, "question": q.text, "negative": q.negative,
                        "file": q.pointer[0] if q.pointer else None,
                        "line": q.pointer[1] if q.pointer else None,
                        "quote": q.pointer[2] if q.pointer else None,
                        "stages": q.stage_path, "note": q.note,
                    }
                    for q in questions
                ],
            },
            indent=2,
        ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
