#!/usr/bin/env bash
# Local Dragonfly lab: primary :6379, replica :6380, optional single-shard :6381.
# Wraps ../assets/compose.yaml (image pinned by digest).
set -euo pipefail

COMPOSE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../assets" && pwd)/compose.yaml"
PROJECT=dfskill

usage() {
  cat <<EOF
usage: lab.sh {up|down|status} [--single]

  up       start primary(:6379) + replica(:6380), wait for the healthcheck
  down     stop and remove the containers and their volumes
  status   container state, PING and server version for each node

  --single also run the single-shard node on :6381 (--proactor_threads=1).
           Use it to separate "slow script" from "slow because it fans out
           across shards": on one shard there is no cross-shard coordination.

compose file: $COMPOSE_FILE
EOF
}

profile_args=()
cmd=${1:-}
[[ $# -gt 0 ]] && shift
for arg in "$@"; do
  case "$arg" in
    --single) profile_args=(--profile single) ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

compose() { docker compose -p "$PROJECT" -f "$COMPOSE_FILE" ${profile_args+"${profile_args[@]}"} "$@"; }

ports=(6379 6380)
[[ ${profile_args+set} == set ]] && ports+=(6381)

case "$cmd" in
  up)
    compose up -d --wait   # --wait blocks on the compose healthcheck
    compose ps --format 'table {{.Name}}\t{{.State}}\t{{.Ports}}'
    ;;
  down)
    compose --profile single down -v
    ;;
  status)
    compose ps --format 'table {{.Name}}\t{{.State}}\t{{.Status}}\t{{.Ports}}'
    for p in "${ports[@]}"; do
      name=$(docker ps --filter "publish=$p" --format '{{.Names}}' | head -1)
      [[ -z "$name" ]] && { echo ":$p  (no container)"; continue; }
      pong=$(docker exec "$name" redis-cli -p 6379 PING 2>/dev/null || echo unreachable)
      ver=$(docker exec "$name" redis-cli -p 6379 INFO server 2>/dev/null \
            | awk -F: '/^dragonfly_version|^thread_count/ {printf "%s=%s ", $1, $2}' \
            | tr -d '\r')
      echo ":$p  $name  $pong  $ver"
    done
    ;;
  -h|--help) usage; exit 0 ;;
  *) usage; exit 2 ;;
esac
