"""Pure policy for `chat`: resolve a query against session candidates."""

import re
from typing import List, NamedTuple, Tuple

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
ID_PREFIX_RE = re.compile(r"^[0-9a-f][0-9a-f-]{3,}$", re.I)


class Session(NamedTuple):
    id: str
    path: str
    titles: Tuple[str, ...]  # in file order; the last one is current
    mtime: float


def parse_query(query: str) -> Tuple[str, str]:
    """Return ("id", prefix) or ("name", fragment).

    A pasted block or text carrying a full UUID resolves by that UUID; a bare
    run of at least four hex characters is an ID prefix; anything else is a
    name fragment.
    """
    query = query.strip()
    full = UUID_RE.search(query)
    if full:
        return "id", full.group(0).lower()
    if ID_PREFIX_RE.match(query):
        return "id", query.lower()
    return "name", query


def match(kind: str, value: str, sessions: List[Session]) -> List[Session]:
    """Sessions matching the query, most recent first."""
    if kind == "id":
        hits = [s for s in sessions if s.id.lower().startswith(value)]
    else:
        needle = value.lower()
        hits = [s for s in sessions if any(needle in t.lower() for t in s.titles)]
    return sorted(hits, key=lambda s: s.mtime, reverse=True)
