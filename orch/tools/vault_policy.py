"""Pure policy for the secret vault (`~/.secrets`): values held there never enter
a transcript.

Two mechanical layers use it:

1. `secret_access_violation` (PreToolUse) refuses any tool call that would read
   vault material directly: the vault path, Secret payloads via kubectl,
   protected in-pod paths, a hidden-directory glob under HOME, or a known value
   inside a tool input (a prompt, a Write, an Edit).
2. `redact_known` / `redact_shaped` (PostToolUse, through
   `gate_policy.redact_secrets`) mask every known value and every standalone
   API-key-shaped token in a tool result. A value the model never sees is a
   value it cannot print.

Vault file format is the Kubernetes keys-file format used by
`example-keys` (see `example-collector/src/keys.rs`): a label line
followed by its value line, repeated. A value line is one unbroken token of 30+
characters from ``[A-Za-z0-9+/=_-]``; every other non-empty line is a pending
label, and a label that is not followed by a value is dropped. Header lines
therefore need no special syntax. A label ending in ``:id`` marks an identifier
(e.g. a cloud access key ID): its value line may be 20+ characters. Such short
values are redacted only by exact known-value match, never by shape.

No I/O lives here. Functions take texts and facts as values and return
decisions, texts, and summaries that never contain a value, only its length
and a short digest.
"""

import hashlib
import os
import re

VAULT_DIR_NAME = ".secrets"

_VALUE_RE = re.compile(r"^[A-Za-z0-9+/=_-]{30,}$")
IDENTIFIER_SUFFIX = ":id"
_IDENTIFIER_VALUE_RE = re.compile(r"^[A-Za-z0-9+/=_-]{20,}$")
# A table row ends in its value; the label is everything before it. Also the
# threshold for "this vault line is a value" when collecting known tokens.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9+/=_.:-]{16,}$")
# Standalone standard-base64 token long enough to be an API key (an Examplevendor key
# is 68 chars). May not start at or right after a path separator: paths stay readable.
_SHAPED_RE = re.compile(
    r"(?<![A-Za-z0-9+/=_.-])[A-Za-z0-9+][A-Za-z0-9+/]{39,}={0,2}(?![A-Za-z0-9+/=_-])"
)
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_K8S_SECRET_READ_RE = re.compile(
    r"\bkube"
    r"ctl\b[^|;&\n]*\bsecrets?\b[^|;&\n]*(?:\s-o\b|\s-o[a-z=]|--output\b|jsonpath|go-template|custom-columns)"
)
# `cat ~/.s*/x`: a glob over hidden directories under HOME reaches the vault by another name.
_HOME_HIDDEN_GLOB_RE = re.compile(
    r"(?:~|\$HOME|\$\{HOME\}|/Users/[^/\s]+|/home/[^/\s]+)/\.[A-Za-z0-9_-]*[*?\[]"
)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def mask(value):
    return "<redacted sha256:%s>" % digest(value)


def is_value_line(line, label):
    """Whether ``line`` is the value of the pending ``label``."""
    if label.endswith(IDENTIFIER_SUFFIX):
        return bool(_IDENTIFIER_VALUE_RE.match(line))
    return bool(_VALUE_RE.match(line))


# -- file format -------------------------------------------------------------------


def parse_entries(text):
    """Ordered ``[(label, value)]`` with the keys.rs semantics: a repeated label
    keeps the last value in the position of its first occurrence."""
    entries = []
    index = {}
    pending = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if pending is not None and is_value_line(line, pending):
            if pending in index:
                entries[index[pending]] = (pending, line)
            else:
                index[pending] = len(entries)
                entries.append((pending, line))
            pending = None
        else:
            pending = line
    return entries


def summary_rows(entries):
    """``[(label, length, digest)]`` for display; never the value."""
    return [(label, len(value), digest(value)) for label, value in entries]


def import_table(text):
    """Vault text from a table whose rows are ``<label words...> <value>``.
    Rows whose last field is not a value (headers) are skipped."""
    lines = []
    for raw in text.splitlines():
        fields = raw.split()
        if len(fields) < 2 or not _TOKEN_RE.match(fields[-1]):
            continue
        lines.append(" ".join(fields[:-1]))
        lines.append(fields[-1])
    return "\n".join(lines) + ("\n" if lines else "")


def merge(base_text, updates_text, renames=None):
    """Replace base values by label with those from ``updates``; append labels
    the base lacks. ``renames`` maps a base label to the updates label that
    replaces it (the entry keeps its position, takes the new label). Returns
    ``(text, rows)`` where rows are ``(label, status, old_digest, new_digest)``
    and status is kept / replaced / renamed / added. Base lines that are not
    entries (headers, comments) are kept verbatim."""
    renames = dict(renames or {})
    updates = dict(parse_entries(updates_text))
    unknown = [new for new in renames.values() if new not in updates]
    if unknown:
        raise ValueError("rename target not in updates: %s" % ", ".join(unknown))
    consumed = set()
    out = []
    rows = []
    pending = None  # (label, index into out)
    for raw in base_text.splitlines():
        line = raw.strip()
        if pending is not None and line and is_value_line(line, pending[0]):
            label, label_index = pending
            pending = None
            target = renames.get(label, label)
            if target in updates and target not in consumed:
                consumed.add(target)
                out[label_index] = target
                out.append(updates[target])
                status = "renamed" if target != label else "replaced"
                rows.append((target, status, digest(line), digest(updates[target])))
            else:
                out.append(raw)
                rows.append((label, "kept", digest(line), digest(line)))
            continue
        out.append(raw)
        pending = (line, len(out) - 1) if line else None
    for label, value in updates.items():
        if label in consumed:
            continue
        out.append(label)
        out.append(value)
        rows.append((label, "added", "", digest(value)))
    return "\n".join(out) + "\n", rows


# -- redaction ---------------------------------------------------------------------


def vault_tokens(file_texts):
    """Secret values held by vault files: every line that is one unbroken token of 16+ chars."""
    tokens = set()
    for text in file_texts:
        for raw in text.splitlines():
            line = raw.strip()
            # An identifier label is never a value: value lines cannot hold ':'.
            if _TOKEN_RE.match(line) and not line.isdigit() and not line.endswith(IDENTIFIER_SUFFIX):
                tokens.add(line)
    return tokens


def redact_known(text, tokens):
    """Mask every exact occurrence of a known value (longest first)."""
    for token in sorted(tokens, key=len, reverse=True):
        if token in text:
            text = text.replace(token, mask(token))
    return text


def _looks_like_key(token):
    return (
        any(c.isupper() for c in token)
        and any(c.islower() for c in token)
        and any(c.isdigit() for c in token)
        and not _HEX_RE.match(token)
    )


def redact_shaped(text):
    """Mask standalone API-key-shaped tokens: 40+ base64 chars with upper, lower and
    digit, not a hex digest, not inside a path."""
    return _SHAPED_RE.sub(
        lambda m: mask(m.group(0)) if _looks_like_key(m.group(0)) else m.group(0), text
    )


def redact_tree(value, tokens):
    """Apply ``redact`` to every string inside a JSON-like value; same shape back."""
    if isinstance(value, str):
        return redact_known(redact_shaped(value), tokens) if tokens else redact_shaped(value)
    if isinstance(value, dict):
        return {k: redact_tree(v, tokens) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_tree(v, tokens) for v in value]
    return value


# -- access gate -------------------------------------------------------------------


def _input_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            for text in _input_strings(item):
                yield text
    elif isinstance(value, list):
        for item in value:
            for text in _input_strings(item):
                yield text


def _resolve(path, cwd):
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        expanded = os.path.join(cwd, expanded)
    return os.path.normpath(expanded)


def _within(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


def secret_access_violation(tool_name, tool_input, cwd, vault_dir, known_tokens, protected_paths):
    """Reason to refuse the call, or None. ``vault_dir`` is absolute; ``protected_paths``
    are strings (in-pod mount paths) that may never appear in a command."""
    tool_input = tool_input or {}
    for text in _input_strings(tool_input):
        for token in known_tokens:
            if token in text:
                return "a known secret value appears in the tool input"
    if tool_name == "Bash":
        command = tool_input.get("command") or ""
        if VAULT_DIR_NAME in command:
            return "the command names the secret vault (%s)" % VAULT_DIR_NAME
        if _HOME_HIDDEN_GLOB_RE.search(command):
            return "the command globs hidden directories under HOME"
        if _K8S_SECRET_READ_RE.search(command):
            return "the command would print Secret payloads (`secretctl pull-k8s` reads them silently)"
        for path in protected_paths:
            if path and path in command:
                return "the command names a protected secret path (%s)" % path
        return None
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
    if tool_name in ("Grep", "Glob") and not path:
        path = cwd
    if not path:
        return None
    resolved = _resolve(path, cwd)
    vault_dir = os.path.normpath(vault_dir)
    if _within(resolved, vault_dir) or VAULT_DIR_NAME in resolved.split("/"):
        return "the path is inside the secret vault"
    if tool_name in ("Grep", "Glob") and _within(vault_dir, resolved):
        return "the search root contains the secret vault; search a narrower directory"
    return None
