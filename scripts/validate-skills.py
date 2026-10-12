#!/usr/bin/env python3
"""Validate psilon skill structure, metadata budgets, and local links."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"^[a-z0-9-]{1,64}$")
LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")
EXPECTED_SKILLS = 12
REQUIRED_KEYS = {"name", "description"}
# Claude Code fields; Codex ignores them and uses agents/openai.yaml instead.
OPTIONAL_KEYS = {"disable-model-invocation", "argument-hint"}
# Agent Skills spec caps description at 1024 characters; ~100 words is the
# skill-creator guidance for the always-loaded metadata level.
MAX_DESCRIPTION_CHARS = 1024
MAX_DESCRIPTION_WORDS = 120
MAX_BODY_LINES = 250
MAX_BODY_WORDS = 2_500


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def frontmatter(text: str, path: Path) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        fail(f"{path}: missing opening frontmatter fence")
    try:
        raw, body = text[4:].split("\n---\n", 1)
    except ValueError:
        fail(f"{path}: missing closing frontmatter fence")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        fail(f"{path}: frontmatter must be a mapping")
    return data, body


def validate_skill(directory: Path) -> None:
    skill_file = directory / "SKILL.md"
    if not skill_file.is_file():
        fail(f"{directory}: missing SKILL.md")

    data, body = frontmatter(skill_file.read_text(encoding="utf-8"), skill_file)
    if not REQUIRED_KEYS <= set(data) <= REQUIRED_KEYS | OPTIONAL_KEYS:
        fail(f"{skill_file}: frontmatter keys must be name, description and optionally {sorted(OPTIONAL_KEYS)}")

    name = data["name"]
    description = data["description"]
    if not isinstance(name, str) or not NAME.fullmatch(name):
        fail(f"{skill_file}: invalid name")
    if name != directory.name:
        fail(f"{skill_file}: name does not match directory")
    if not isinstance(description, str) or not description.strip():
        fail(f"{skill_file}: description must be non-empty text")
    if len(description) > MAX_DESCRIPTION_CHARS:
        fail(f"{skill_file}: description exceeds {MAX_DESCRIPTION_CHARS} characters")
    if len(description.split()) > MAX_DESCRIPTION_WORDS:
        fail(f"{skill_file}: description exceeds {MAX_DESCRIPTION_WORDS} words")

    if len(body.splitlines()) > MAX_BODY_LINES:
        fail(f"{skill_file}: body exceeds {MAX_BODY_LINES} lines")
    if len(body.split()) > MAX_BODY_WORDS:
        fail(f"{skill_file}: body exceeds {MAX_BODY_WORDS} words")

    for target in LINK.findall(body):
        if target.startswith(("http://", "https://", "#")):
            continue
        local = target.split("#", 1)[0]
        if local and not (directory / local).resolve().exists():
            fail(f"{skill_file}: missing linked file {target}")

    metadata = directory / "agents" / "openai.yaml"
    if not metadata.is_file():
        fail(f"{directory}: missing agents/openai.yaml")
    openai = yaml.safe_load(metadata.read_text(encoding="utf-8"))
    if not isinstance(openai, dict):
        fail(f"{metadata}: invalid YAML mapping")
    # A manual-only skill must be manual-only in both clients.
    claude_manual = data.get("disable-model-invocation") is True
    codex_manual = (openai.get("policy") or {}).get("allow_implicit_invocation") is False
    if claude_manual and not codex_manual:
        fail(f"{metadata}: manual-only skill must set policy.allow_implicit_invocation: false")

    print(
        f"OK {name}: {len(description.split())} description words, "
        f"{len(body.split())} body words, {len(body.splitlines())} body lines"
    )


def main() -> None:
    skills = sorted(
        path
        for pattern in ("superpowers-*-psilon", "dragonfly-scripting", "test-audit", "pareto-design-selection", "retro")
        for path in ROOT.glob(pattern)
        if path.is_dir()
    )
    if len(skills) != EXPECTED_SKILLS:
        fail(f"expected {EXPECTED_SKILLS} skills, found {len(skills)}")
    for skill in skills:
        validate_skill(skill)


if __name__ == "__main__":
    main()
