"""Pure policy for `chat`: resolve a query against session candidates."""

import re
from typing import List, NamedTuple, Tuple

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
ID_PREFIX_RE = re.compile(r"^[0-9a-f][0-9a-f-]{3,}$", re.I)


class Session(NamedTuple):
    id: str
    path: str
    titles: Tuple[str, ...]  # distinct, first-seen order
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
