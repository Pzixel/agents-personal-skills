#!/bin/sh
# Link this checkout's skills and orch directories into the Codex and Claude Code
# config directories on macOS. Idempotent: rerun after every pull.
set -eu

case "${1:-}" in
  claude) targets=claude ;;
  codex) targets=codex ;;
  both) targets="codex claude" ;;
  *) echo "usage: $0 claude|codex|both" >&2; exit 2 ;;
esac

root=$(realpath "$(dirname "$0")")
codex="$HOME/.agents"
claude="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

common=$(cd "$root" && echo superpowers-*-psilon dragonfly-scripting test-audit pareto-design-selection retro)

# One "<destination> <source>" pair per line; names contain no spaces.
plan() {
  for t in $targets; do
    if [ "$t" = codex ]; then
      for s in $common codex-simplify; do echo "$codex/skills/$s $s"; done
    else
      for s in $common orch; do echo "$claude/skills/$s $s"; done
      for d in agents hooks tools; do echo "$claude/$d orch/$d"; done
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

# Dangling links into this checkout are retired skills.
for t in $targets; do
  if [ "$t" = codex ]; then dir="$codex/skills"; else dir="$claude/skills"; fi
  [ -d "$dir" ] || continue
  for l in "$dir"/*; do
    [ -L "$l" ] && [ ! -e "$l" ] || continue
    case $(readlink "$l") in
      "$root"/*) rm "$l"; echo "removed $l" ;;
    esac
  done
done

plan | while read -r dest src; do
  [ -e "$dest" ] && continue
  mkdir -p "$(dirname "$dest")"
  ln -s "$root/$src" "$dest"
  echo "linked $dest -> $root/$src"
done
