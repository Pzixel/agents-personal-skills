"""The nested small-model judge shared by hooks: `claude -p --model haiku` on the session's own login,
with no tools, hooks, plugins, MCP or settings; ``env_name`` set in its environment guards recursion."""
import os
import subprocess
import tempfile

BASE_ARGS = [
    "claude", "-p", "--model", "haiku", "--tools", "", "--no-session-persistence",
    "--setting-sources", "", "--settings", '{"disableAllHooks":true}', "--strict-mcp-config",
    "--disable-slash-commands", "--output-format", "json",
]


def run(args, prompt, timeout_s, env_name, parse):
    """(verdict or None, error or None): ``parse`` applied to the judge's stdout."""
    try:
        done = subprocess.run(
            args + [prompt], capture_output=True, text=True, timeout=timeout_s,
            cwd=tempfile.gettempdir(), env=dict(os.environ, **{env_name: "1"}),
        )
    except Exception as exc:
        return None, type(exc).__name__
    if done.returncode != 0:
        return None, "exit %d" % done.returncode
    verdict = parse(done.stdout)
    return verdict, None if verdict else "unparsable: " + done.stdout[-200:]
