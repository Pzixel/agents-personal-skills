#!/bin/sh
# Link this checkout's skills and orch directories into the Codex and Claude Code
# config directories on macOS and import rules/always-on.md into each client's
# user-level rules file. Idempotent: rerun after every pull.
set -eu

[ $# -eq 1 ] || set -- ""
case "$1" in
  claude) targets=claude ;;
  codex) targets=codex ;;
  both) targets="codex claude" ;;
  *) echo "usage: $0 claude|codex|both" >&2; exit 2 ;;
esac

root=$(dirname "$(realpath "$0")")
codex="$HOME/.agents"
codex_rules="${CODEX_HOME:-$HOME/.codex}/AGENTS.md"
claude="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

common=$(cd "$root" && echo superpowers-*-psilon dragonfly-scripting test-audit pareto-design-selection retro)
for s in $common; do
  [ -d "$root/$s" ] || { echo "Missing skill directory: $root/$s" >&2; exit 1; }
done

# One "<destination> <source>" pair per line; names contain no spaces.
plan() {
  for t in $targets; do
    if [ "$t" = codex ]; then
      for s in $common codex-simplify; do echo "$codex/skills/$s $s"; done
    else
      for s in $common orch; do echo "$claude/skills/$s $s"; done
      for d in agents hooks tools; do
        for e in $(cd "$root/orch/$d" && echo *); do [ "$e" = __pycache__ ] || echo "$claude/$d/$e orch/$d/$e"; done
      done
    fi
  done
}

rules="$root/rules/always-on.md"
begin="<!-- BEGIN agents-personal-skills rules/always-on.md -->"
end="<!-- END agents-personal-skills rules/always-on.md -->"
[ -f "$rules" ] || { echo "Missing rules file: $rules" >&2; exit 1; }

# A Codex rules file may hold at most one complete marked block.
case " $targets " in
  *" codex "*)
    if [ -f "$codex_rules" ]; then
      nb=$(grep -cxF "$begin" "$codex_rules" || true)
      ne=$(grep -cxF "$end" "$codex_rules" || true)
      bl=$(grep -nxF "$begin" "$codex_rules" | cut -d: -f1 || true)
      el=$(grep -nxF "$end" "$codex_rules" | cut -d: -f1 || true)
      if [ "$nb" -gt 1 ] || [ "$nb" != "$ne" ] || { [ "$nb" = 1 ] && [ "$bl" -gt "$el" ]; }; then
        echo "Malformed always-on block in $codex_rules (BEGIN=$nb END=$ne)" >&2
        exit 1
      fi
    fi ;;
esac

# Check every destination before changing anything.
plan | while read -r dest src; do
  if [ -e "$dest" ] || [ -L "$dest" ]; then
    [ "$(realpath "$dest" 2>/dev/null)" = "$root/$src" ] && continue
    echo "Conflicting destination: $dest" >&2
    exit 1
  fi
done

# Dangling links into this checkout are retired skills, agents, hooks or tools.
# A directory that is itself a link (legacy whole-directory install) is the checkout; skip it.
for t in $targets; do
  if [ "$t" = codex ]; then dirs="$codex/skills"; else dirs="$claude/skills $claude/agents $claude/hooks $claude/tools"; fi
  for dir in $dirs; do
    [ -d "$dir" ] && [ ! -L "$dir" ] || continue
    for l in "$dir"/*; do
      [ -L "$l" ] && [ ! -e "$l" ] || continue
      case $(readlink "$l") in
        "$root"/*) rm "$l"; echo "removed $l" ;;
      esac
    done
  done
done

plan | while read -r dest src; do
  [ -e "$dest" ] && continue
  mkdir -p "$(dirname "$dest")"
  ln -s "$root/$src" "$dest"
  echo "linked $dest -> $root/$src"
done

# Always-on rules. Claude Code: one import line in the user CLAUDE.md, unless an
# existing @-import already loads a file holding the Codex block.
# Codex has no import: one marked block holding the file, replaced on every run
# (targets list codex first, so with `both` the block exists before the check).
for t in $targets; do
  if [ "$t" = claude ]; then
    line="@$rules"
    mkdir -p "$claude"
    touch "$claude/CLAUDE.md"
    via=""
    for ref in $(sed -n 's/^@//p' "$claude/CLAUDE.md"); do
      case $ref in "~/"*) ref="$HOME/${ref#"~/"}" ;; esac
      [ -f "$ref" ] && grep -qxF "$begin" "$ref" && via=$ref && break
    done
    if grep -qxF "$line" "$claude/CLAUDE.md"; then
      :
    elif [ -n "$via" ]; then
      echo "skipped $line: $claude/CLAUDE.md already imports the rules block via $via"
    else
      if [ -s "$claude/CLAUDE.md" ] && [ -n "$(tail -c 1 "$claude/CLAUDE.md")" ]; then echo >> "$claude/CLAUDE.md"; fi
      echo "$line" >> "$claude/CLAUDE.md"
      echo "added $line to $claude/CLAUDE.md"
    fi
  else
    # Write the real file behind a symlink; keep its mode (new file: 600 from mktemp).
    mkdir -p "$(dirname "$codex_rules")"
    if [ -e "$codex_rules" ]; then real=$(realpath "$codex_rules"); else real="$(realpath "$(dirname "$codex_rules")")/$(basename "$codex_rules")"; fi
    tmp=$(mktemp "$real.XXXXXX")
    trap 'rm -f "$tmp"' EXIT
    [ -f "$real" ] && cp -p "$real" "$tmp"
    {
      if [ -f "$real" ] && grep -qxF "$begin" "$real"; then
        awk -v b="$begin" '$0 == b { exit } { print }' "$real"
      elif [ -s "$real" ]; then
        cat "$real"
        [ -z "$(tail -c 1 "$real")" ] || echo
      fi
      echo "$begin"
      cat "$rules"
      echo "$end"
      if [ -f "$real" ] && grep -qxF "$begin" "$real"; then
        awk -v e="$end" 'after { print } $0 == e { after = 1 }' "$real"
      fi
    } > "$tmp"
    if [ -f "$real" ] && cmp -s "$tmp" "$real"; then
      rm "$tmp"
    else
      mv "$tmp" "$real"
      echo "updated always-on block in $real"
    fi
    trap - EXIT
  fi
done
