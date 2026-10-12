"""Pure decision rules for the scout pipeline.

No I/O, no subprocess, no logging: every function takes already-acquired facts
as values and returns a complete decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# --- outcomes -------------------------------------------------------------

OK = "ok"
CORRECTED = "corrected"
REJECTED = "rejected"

ACCEPTED = "accepted"
AGREED_NONE = "agreed_none"
NO_AGREEMENT = "no_agreement"

RUN_STAGE = "run_stage"
ACCEPT = "accept"
GIVE_UP = "give_up"

NONE_VOTE = "none"


@dataclass(frozen=True)
class PointerVerdict:
    """outcome is ok / corrected / rejected; line is the line to trust."""

    outcome: str
    line: int | None
    reason: str = ""


@dataclass(frozen=True)
class AgreementVerdict:
    """outcome is accepted / agreed_none / no_agreement."""

    outcome: str
    candidate_id: str | None
    votes: int


@dataclass(frozen=True)
class EscalationVerdict:
    """action is run_stage / accept / give_up."""

    action: str
    stage_index: int | None
    negative: bool
    reason: str = ""


# --- pointer verification -------------------------------------------------


def verify_pointer(file_lines: Sequence[str], line: int, quote: str) -> PointerVerdict:
    """Check a model-reported pointer against the real file lines.

    Accepted when the quote (leading/trailing whitespace ignored) appears on
    `line` or within two lines of it. Otherwise, if the quote appears exactly
    once in the whole file, the line number is corrected to that occurrence.
    Anything else is rejected.
    """
    needle = quote.strip() if isinstance(quote, str) else ""
    if not needle:
        return PointerVerdict(REJECTED, None, "empty quote")

    hits = [i for i, raw in enumerate(file_lines, start=1) if needle in raw.strip()]
    if not hits:
        return PointerVerdict(REJECTED, None, "quote not found in file")

    if isinstance(line, int):
        near = [i for i in hits if abs(i - line) <= 2]
        if near:
            best = min(near, key=lambda i: (abs(i - line), i))
            return PointerVerdict(OK, best, "quote found at the reported line")

    if len(hits) == 1:
        return PointerVerdict(
            CORRECTED, hits[0], f"quote occurs exactly once, at line {hits[0]}"
        )
    return PointerVerdict(
        REJECTED, None, f"quote not near line {line}; {len(hits)} occurrences elsewhere"
    )


# --- selector agreement ---------------------------------------------------


def _is_none_vote(vote) -> bool:
    return vote is None or (isinstance(vote, str) and vote.strip().lower() in ("", NONE_VOTE))


def decide_agreement(votes: Sequence, threshold: int) -> AgreementVerdict:
    """Fold selector votes into one decision. A tie never accepts."""
    tally: dict[str, int] = {}
    for vote in votes:
        key = NONE_VOTE if _is_none_vote(vote) else str(vote).strip()
        tally[key] = tally.get(key, 0) + 1
    if not tally:
        return AgreementVerdict(NO_AGREEMENT, None, 0)

    top = max(tally.values())
    leaders = [key for key, count in tally.items() if count == top]
    if len(leaders) > 1:
        return AgreementVerdict(NO_AGREEMENT, None, top)
    if top < threshold:
        return AgreementVerdict(NO_AGREEMENT, None, top)
    winner = leaders[0]
    if winner == NONE_VOTE:
        return AgreementVerdict(AGREED_NONE, None, top)
    return AgreementVerdict(ACCEPTED, winner, top)


# --- escalation -----------------------------------------------------------


def next_answering_stage(stage_kinds: Sequence[str], stage_index: int) -> int | None:
    """Index of the next stage that can produce an answer (confirm cannot)."""
    for j in range(stage_index + 1, len(stage_kinds)):
        if stage_kinds[j] != "confirm":
            return j
    return None


def decide_escalation(
    agreement_outcome: str,
    stage_index: int,
    stage_kinds: Sequence[str],
    was_negative: bool = False,
) -> EscalationVerdict:
    """Decide what happens after a stage produced `agreement_outcome`.

    A negative answer ("nothing matches") is only accepted from the top tier:
    the last answer-producing stage in the configuration. A negative from any
    lower tier escalates to the next stage instead.
    """
    nxt = next_answering_stage(stage_kinds, stage_index)
    is_top = nxt is None

    if agreement_outcome == ACCEPTED:
        return EscalationVerdict(ACCEPT, None, False, "candidate agreed")

    negative = was_negative or agreement_outcome == AGREED_NONE
    if negative:
        if is_top:
            return EscalationVerdict(ACCEPT, None, True, "negative from the top tier")
        return EscalationVerdict(RUN_STAGE, nxt, False, "negative below the top tier escalates")

    if is_top:
        return EscalationVerdict(GIVE_UP, None, False, "no agreement at the top tier")
    return EscalationVerdict(RUN_STAGE, nxt, False, "no agreement escalates")
