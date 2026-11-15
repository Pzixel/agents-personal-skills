"""The nested model judge shared by hooks: `claude -p` on the session's own login, with no tools, hooks,
plugins, MCP or settings; ``env_name`` set in its environment guards recursion. The prompt goes on stdin:
one argv string is capped at 128 KiB on Linux and the fresh reader's prompts reach about 256k chars."""
import json
import os
import subprocess
import tempfile


def judge_args(model, system_prompt):
    """The judge's command line: ``system_prompt`` replaces the CLI's default (15.6k tokens a call)."""
    return [
        "claude", "-p", "--model", model, "--tools", "", "--no-session-persistence",
        "--setting-sources", "", "--settings", '{"disableAllHooks":true}', "--strict-mcp-config",
        "--disable-slash-commands", "--output-format", "json", "--system-prompt", system_prompt,
    ]


def run(args, prompt, timeout_s, env_name, parse):
    """(verdict or None, error or None): ``parse`` applied to the judge's stdout."""
    try:
        done = subprocess.run(
            args, input=prompt, capture_output=True, text=True, timeout=timeout_s,
            cwd=tempfile.gettempdir(), env=dict(os.environ, **{env_name: "1"}),
        )
    except Exception as exc:
        return None, type(exc).__name__
    if done.returncode != 0:
        return None, "exit %d" % done.returncode
    verdict = parse(done.stdout)
    return verdict, None if verdict else "unparsable: " + answer_head(done.stdout)


def answer_head(cli_stdout, size=200):
    """The first ``size`` chars of the answer inside the CLI's JSON envelope (the stdout itself when it is not
    one): the envelope's tail is usage metadata and says nothing about why an answer did not parse."""
    try:
        answer = json.loads(cli_stdout).get("result")
    except (ValueError, AttributeError):
        answer = None
    return (answer if isinstance(answer, str) else cli_stdout)[:size]
