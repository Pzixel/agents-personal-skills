#!/bin/bash
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/../lib/mini.sh"
mini_copy
# Flip one expectation: the even-count median test now expects the upper middle value.
perl -pi -e 's/\["median"\], 2\.5\)/["median"], 3.0)/' tests/test_summary.py
grep -q '\["median"\], 3.0)' tests/test_summary.py
mini_commit
