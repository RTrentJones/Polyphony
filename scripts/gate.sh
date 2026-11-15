#!/usr/bin/env bash
# The push gate (SPEC-98 P1): what test.yml's lint job and sqlite test leg check, in one command.
# Run it on the exact commit you are about to push; push only if it exits 0.
#   scripts/gate.sh            # black, ruff, the whole pytest suite
#   scripts/gate.sh --fast     # black, ruff, unit tests only
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "gate: the working tree differs from HEAD; commit first, so the gate checks what is pushed" >&2
  exit 2
fi
tests=tests
[ "${1:-}" = "--fast" ] && tests=tests/unit
black --check -q app tests alembic evals
ruff check -q app tests alembic evals
python -m pytest -q "$tests"
echo "gate: passed at $(git rev-parse HEAD)"
