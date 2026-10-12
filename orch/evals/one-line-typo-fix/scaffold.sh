#!/bin/bash
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/../lib/mini.sh"
mini_copy
perl -pi -e 's/keeps records in memory/keeps records in memroy/' README.md
grep -q memroy README.md
mini_commit
