"""Names the model sees from two sources: the policy and the config scan.

A skill or agent name reaches the model from several sources: user skill
directories, skills-dir plugins, user agent files, enabled marketplace plugins
and claude.ai synced skills. Claude Code shows a plugin entry as
`<plugin>:<name>`; the same bare name from two sources is a duplicate. Skills
and agents are separate namespaces (the Skill and the Agent tool).
"""

import json
import os
import re

_PREFIX = re.compile(r"^[A-Za-z0-9_-]+:")
_NAME = re.compile(r"^name:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", re.M)


def duplicates(entries):
    """Lines naming every (kind, bare name) seen from two or more sources.

    entries -- iterable of (kind, name, source); kind is "skill" or "agent".
    """
    seen = {}
    for kind, name, source in entries:
        key = (kind, _PREFIX.sub("", name, count=1))
        seen.setdefault(key, [])
        if source not in seen[key]:
            seen[key].append(source)
    return [
        "duplicate %s `%s`: %s" % (kind, name, " | ".join(sorted(sources)))
        for (kind, name), sources in sorted(seen.items())
        if len(sources) > 1
    ]


def _frontmatter_name(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            head = fh.read(4096)
    except OSError:
        return default
    if not head.startswith("---"):
        return default
    m = _NAME.search(head.split("---", 2)[1] if head.count("---") >= 2 else "")
    return m.group(1).strip() if m else default


def _dirs(path):
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return []
    return [(n, os.path.join(path, n)) for n in names]


def _plugin_entries(root, label):
    out = []
    for name, d in _dirs(os.path.join(root, "skills")):
        md = os.path.join(d, "SKILL.md")
        if os.path.isfile(md):
            out.append(("skill", _frontmatter_name(md, name), label))
    for name, f in _dirs(os.path.join(root, "agents")):
        if name.endswith(".md") and os.path.isfile(f):
            out.append(("agent", _frontmatter_name(f, name[:-3]), label))
    return out


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def scan(config):
    """(kind, name, source) for every skill and agent the config dir exposes."""
    settings = _load_json(os.path.join(config, "settings.json"))
    entries = []
    skills = os.path.join(config, "skills")
    for name, d in _dirs(skills):
        if os.path.isfile(os.path.join(d, ".claude-plugin", "plugin.json")):
            entries += _plugin_entries(d, "skills-dir plugin " + d)
        md = os.path.join(d, "SKILL.md")
        if os.path.isfile(md):
            entries.append(("skill", _frontmatter_name(md, name), d))
    for name, f in _dirs(os.path.join(config, "agents")):
        if name.endswith(".md") and os.path.isfile(f):
            entries.append(("agent", _frontmatter_name(f, name[:-3]), f))
    installed = _load_json(os.path.join(config, "plugins", "installed_plugins.json"))
    for plugin, enabled in sorted((settings.get("enabledPlugins") or {}).items()):
        if enabled is not True:
            continue
        for install in (installed.get("plugins") or {}).get(plugin) or []:
            path = install.get("installPath")
            if path:
                entries += _plugin_entries(path, "plugin " + plugin)
    if settings.get("syncClaudeAiSkills") is not False:
        for _, d in _dirs(os.path.join(skills, "synced")):
            manifest = _load_json(os.path.join(d, "manifest.json"))
            for skill in manifest.get("skills") or []:
                if isinstance(skill, dict) and skill.get("name"):
                    entries.append(("skill", skill["name"], "claude.ai sync " + d))
    return entries


def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
