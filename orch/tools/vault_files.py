"""I/O for the secret vault: where it is, and the texts it holds.

The vault is `$SECRET_VAULT_DIR` or `~/.secrets` (mode 0700, files 0600).
`.protected-paths` inside it lists in-pod mount paths (one per line, `#`
comments) that no Bash command may name. Every other regular file up to
MAX_FILE_BYTES is a vault file (see `vault_policy` for the format): a keys file,
a `.raw` file (one secret value) or a `.public` file (one value that is not secret).
"""

import os
import tempfile

from vault_policy import PUBLIC_SUFFIX, RAW_SUFFIX

VAULT_DIR = os.environ.get("SECRET_VAULT_DIR") or os.path.expanduser("~/.secrets")
PROTECTED_FILE = ".protected-paths"
MAX_FILE_BYTES = 262144


def load_vault(vault_dir=None):
    """``(texts, raw_values, protected_paths)``: contents of every keys file, the value
    of every `.raw` file (`.public` files are not read) and the protected paths."""
    vault_dir = vault_dir or VAULT_DIR
    texts, raw_values, protected = [], [], []
    if not os.path.isdir(vault_dir):
        return texts, raw_values, protected
    for root, _dirs, files in os.walk(vault_dir):
        for name in sorted(files):
            if name.endswith(PUBLIC_SUFFIX):
                continue
            path = os.path.join(root, name)
            try:
                if os.path.getsize(path) > MAX_FILE_BYTES:
                    continue
                with open(path, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue
            if name == PROTECTED_FILE:
                protected.extend(
                    line.strip()
                    for line in text.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                )
            elif name.endswith(RAW_SUFFIX):
                raw_values.append(text.strip())
            else:
                texts.append(text)
    return texts, raw_values, protected


def vault_file_path(vault_dir, name, suffix=".txt"):
    """Absolute path of the vault file called ``name`` (``acme/service_keys``).
    Names are relative, without ``..``; the ``suffix`` is implied."""
    if not name or name.startswith(("/", "~")) or ".." in name.split("/"):
        raise ValueError("vault file names are relative paths without '..'")
    if not name.endswith(suffix):
        name += suffix
    return os.path.join(vault_dir, name)


def read_raw(vault_dir, name):
    """``(suffix, value)`` of ``name``'s raw file, or None when it has none."""
    for suffix in (RAW_SUFFIX, PUBLIC_SUFFIX):
        path = vault_file_path(vault_dir, name, suffix)
        if os.path.exists(path):
            return suffix, read_vault_file(path).strip()
    return None


def write_vault_file(path, text):
    """Create or replace a vault file with owner-only permissions (dir 0700, file 0600).
    Atomic: the text goes to a temp file renamed over ``path``, so a killed write
    leaves the old file or the new one, never a truncated one."""
    old_umask = os.umask(0o077)
    try:
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            os.replace(tmp, path)
        except BaseException:
            os.unlink(tmp)
            raise
    finally:
        os.umask(old_umask)


def read_vault_file(path):
    """Text of a vault file exactly as stored (no newline translation: a raw value
    with ``\r\n`` is written back byte-for-byte by ``apply-k8s``)."""
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()
