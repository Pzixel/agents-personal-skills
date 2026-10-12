#!/bin/bash
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/../lib/mini.sh"
mini_copy

mini_commit
