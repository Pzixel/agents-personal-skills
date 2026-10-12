"""I/O for the secret vault: where it is, and the texts it holds.

The vault is `$SECRET_VAULT_DIR` or `~/.secrets` (mode 0700, files 0600).
`.protected-paths` inside it lists in-pod mount paths (one per line, `#`
comments) that no Bash command may name. Every other regular file up to
MAX_FILE_BYTES is a vault file (see `vault_policy` for the format).
"""

import os

VAULT_DIR = os.environ.get("SECRET_VAULT_DIR") or os.path.expanduser("~/.secrets")
PROTECTED_FILE = ".protected-paths"
MAX_FILE_BYTES = 262144


def load_vault(vault_dir=None):
    """``(texts, protected_paths)``: contents of every vault file and the protected paths."""
    vault_dir = vault_dir or VAULT_DIR
    texts, protected = [], []
    if not os.path.isdir(vault_dir):
        return texts, protected
    for root, _dirs, files in os.walk(vault_dir):
        for name in sorted(files):
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
            else:
                texts.append(text)
    return texts, protected


def vault_file_path(vault_dir, name):
    """Absolute path of the vault file called ``name`` (``acme/service_keys``).
    Names are relative, without ``..``; the ``.txt`` suffix is implied."""
    if not name or name.startswith(("/", "~")) or ".." in name.split("/"):
        raise ValueError("vault file names are relative paths without '..'")
    if not name.endswith(".txt"):
        name += ".txt"
    return os.path.join(vault_dir, name)


def write_vault_file(path, text):
    """Create or replace a vault file with owner-only permissions (dir 0700, file 0600)."""
    old_umask = os.umask(0o077)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
    finally:
        os.umask(old_umask)
