"""Pure policy for `chat`: resolve a query against session candidates; human text includes mid-turn owner messages."""

import re
from typing import List, NamedTuple, Tuple

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
ID_PREFIX_RE = re.compile(r"^[0-9a-f][0-9a-f-]{3,}$", re.I)


NOISE_PREFIXES = ("<task-notification", "<system-reminder", "<local-command")


def typed_by_owner(prompt, facts=None):
    """True when a UserPromptSubmit ``prompt`` is text the owner typed. False for a
    tagged system turn (NOISE_PREFIXES), for the prompt of a scheduled job the scan
    lists as open or as just fired, and for the prompt of the last /loop wakeup: the
    hook payload carries no origin field (CASE-80). ``facts``: the last
    ``transcript.background_watch_scan`` snapshot, or None."""
    text = (prompt or "").strip() if isinstance(prompt, str) else ""
    if not text or text.startswith(NOISE_PREFIXES):
        return False
    if not facts:
        return True
    if (facts.get("wakeup_prompt") or "").strip() == text:
        return False
    jobs = list(facts.get("cron_jobs", {}).values()) + list(facts.get("recurring_jobs", {}).values())
    return not any(
        (job.get("prompt") or "").strip() == text and (not job.get("closed") or job.get("fired")) for job in jobs
    )


class Session(NamedTuple):
    id: str
    path: str
    titles: Tuple[str, ...]  # distinct, last-seen order (current title last)
    mtime: float


def parse_query(query: str) -> Tuple[Tuple[str, ...], str]:
    """Return the kinds to try in order ("id", "name") and the value to match.

    A pasted block or text carrying a full UUID resolves only by that UUID. A
    bare word of at least four hex characters or dashes is tried as an ID
    prefix first and, when no ID matches, as a name fragment ("ace-1596" is
    hex-looking and also a fragment of a title). Anything else is a name
    fragment. The caller stops at the first kind with a match.
    """
    query = query.strip()
    full = UUID_RE.search(query)
    if full:
        return ("id",), full.group(0).lower()
    if ID_PREFIX_RE.match(query):
        return ("id", "name"), query.lower()
    return ("name",), query


def match(kind: str, value: str, sessions: List[Session]) -> List[Session]:
    """Sessions matching the query, most recent first."""
    if kind == "id":
        hits = [s for s in sessions if s.id.lower().startswith(value)]
    else:
        needle = value.lower()
        hits = [s for s in sessions if any(needle in t.lower() for t in s.titles)]
    return sorted(hits, key=lambda s: s.mtime, reverse=True)


def human_text(record):
    """Owner text of a transcript record, or None: user messages and mid-turn queued_command attachments."""
    if record.get("isSidechain"):
        return None
    if record.get("type") == "user":
        content = record.get("message", {}).get("content")
    elif record.get("type") == "attachment":
        attachment = record.get("attachment") or {}
        if attachment.get("type") != "queued_command" or (attachment.get("origin") or {}).get("kind") != "human":
            return None
        content = attachment.get("prompt")
    else:
        return None
    if isinstance(content, list):
        if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content):
            return None
        text = " ".join(x.get("text", "") for x in content if isinstance(x, dict))
    else:
        text = content or ""
    text = text.strip()
    if not text or text.startswith(NOISE_PREFIXES):
        return None
    return text
