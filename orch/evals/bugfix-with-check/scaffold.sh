#!/bin/bash
set -euo pipefail
. "$(cd "$(dirname "$0")" && pwd)/../lib/mini.sh"
mini_copy
# Inject the bug: remove() reports success for an unknown id.
perl -0pi -e 's/        return self\._records\.pop\(record_id, None\) is not None\n/        self._records.pop(record_id, None)\n        return True\n/' stats_svc/store.py
grep -q 'return True' stats_svc/store.py
mini_commit
