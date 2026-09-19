#!/usr/bin/env bash
# Auto-fix the flake8 errors that fail CI's "python -m flake8 ." step.
#
#   E127 / E302 / E303 / E306  -> autopep8, limited to exactly those codes
#   F401 (unused imports)      -> autoflake, only on files flake8 flags
#   E501 (line too long)       -> reported, NOT auto-fixed (see below)
#
# Deliberately NOT black / isort: black would reformat the whole codebase (huge
# diff, and it can't shorten long comments/strings anyway), and isort would
# reorder the import sequences wsgi.py / app.py / tests/conftest.py depend on
# (see the E402 per-file-ignores in .flake8).
#
# E501 is not auto-fixed on purpose: autopep8 can only shorten code lines, and
# when it does it moves trailing comments onto their own line -- which detaches
# "# nosec B608" / "# noqa" markers from the statement they suppress and makes
# the Bandit / flake8 checks fail instead. Long strings and comments can't be
# shortened by any formatter. Remaining E501s are listed at the end.
#
# Only files flake8 currently reports are modified, so clean files are never
# touched.
#
# Usage (from anywhere inside the repo):
#   pip install -r requirements-dev.txt autoflake autopep8
#   bash scripts/fix_lint.sh --check    # dry run: show the diff, change nothing
#   bash scripts/fix_lint.sh            # apply (refuses on a dirty working tree)
#   bash scripts/fix_lint.sh --force    # apply even with uncommitted changes
set -euo pipefail

PY="${PYTHON:-python}"
MAX_LINE=160
FIX_CODES="E127,E302,E303,E306"

MODE=apply
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --check) MODE=check ;;
    --force) FORCE=1 ;;
    *) echo "unknown option: $arg (use --check or --force)"; exit 2 ;;
  esac
done

cd "$(git rev-parse --show-toplevel)"

for tool in flake8 autoflake autopep8; do
  "$PY" -m "$tool" --version >/dev/null 2>&1 || {
    echo "missing: $tool  ->  $PY -m pip install -r requirements-dev.txt autoflake autopep8"
    exit 2
  }
done

if [[ "$MODE" == apply && "$FORCE" -eq 0 && -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "Refusing to run: you have uncommitted changes, and this rewrites files in place."
  echo "Commit or stash first (or pass --force), then run this on its own branch."
  exit 2
fi

# Only files git knows about (tracked + untracked-but-not-ignored) are ever linted or
# rewritten -- never a local virtualenv (.venv*, venv/) or other ignored directory.
# This is also exactly the set CI sees in a fresh checkout.
py_files() { git ls-files -co --exclude-standard -z -- '*.py'; }
run_flake8() { py_files | xargs -0 "$PY" -m flake8 "$@" || true; }

files_for() {  # files flake8 flags for the given comma-separated codes
  run_flake8 --select="$1" --format='%(path)s' | sort -u
}

echo "== before: $(run_flake8 | wc -l | tr -d ' ') flake8 errors =="

# --- F401: unused imports ---------------------------------------------------
F401_FILES="$(files_for F401)"
if [[ -n "$F401_FILES" ]]; then
  # shellcheck disable=SC2086
  if [[ "$MODE" == check ]]; then
    echo "$F401_FILES" | xargs "$PY" -m autoflake --remove-all-unused-imports --ignore-init-module-imports --check-diff || true
  else
    echo "$F401_FILES" | xargs "$PY" -m autoflake --in-place --remove-all-unused-imports --ignore-init-module-imports
  fi
fi

# --- E127 / E30x ------------------------------------------------------------
FMT_FILES="$(files_for "$FIX_CODES")"
if [[ -n "$FMT_FILES" ]]; then
  if [[ "$MODE" == check ]]; then
    echo "$FMT_FILES" | xargs "$PY" -m autopep8 --diff --select="$FIX_CODES" --max-line-length="$MAX_LINE" || true
  else
    echo "$FMT_FILES" | xargs "$PY" -m autopep8 --in-place --select="$FIX_CODES" --max-line-length="$MAX_LINE"
  fi
fi

if [[ "$MODE" == check ]]; then
  echo "== dry run only: nothing was modified =="
  exit 0
fi

# --- verify -----------------------------------------------------------------
echo "== syntax check =="
py_files | xargs -0 "$PY" -m py_compile

REMAINING="$(run_flake8)"
echo "== after: $(printf '%s' "$REMAINING" | grep -c . || true) flake8 errors remain =="
if [[ -n "$REMAINING" ]]; then
  echo "$REMAINING"
  echo "-> these need a manual fix (long strings/comments, or lines carrying # nosec / # noqa)."
fi

echo "== changed files =="
git diff --stat
echo "Next: review 'git diff', run 'python -m pytest', then commit."
[[ -z "$REMAINING" ]]
