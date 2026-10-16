#!/bin/sh
# Link this checkout's skills and orch directories into the Codex and Claude Code
# config directories on macOS. Idempotent: rerun after every pull.
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
