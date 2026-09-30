#!/usr/bin/env bash
# Reproduce the self-audit: alibi checking a pull request description
# against a real diff, in a throwaway repository.
#
#   bash docs/self_audit.sh
#
# It builds a small repo, writes a description with three true claims and one
# false one, and prints what alibi makes of each. Nothing here touches your own
# files or git configuration.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python

DEMO=$(mktemp -d)
trap 'find "$DEMO" -mindepth 1 -delete 2>/dev/null; rmdir "$DEMO" 2>/dev/null' EXIT
REPO="$DEMO/repo"
mkdir -p "$REPO"

git -C "$REPO" init -q
git -C "$REPO" config user.email demo@example.com
git -C "$REPO" config user.name "alibi self-audit"
printf 'def verify(claim, facts, session):\n    return None\n' > "$REPO/alibi_verify.py"
git -C "$REPO" add -A
git -C "$REPO" commit -qm "initial"

# The change under review: one new file, nothing removed.
printf 'this line exists so alibi has something real to check\n' > "$REPO/demo_check.py"

# The description. Three claims are true of this diff; one is not.
cat > "$DEMO/PR_DESCRIPTION.md" <<'MD'
# Add a self-audit demo fixture

I created `demo_check.py` to give the self-audit a real diff to check.
I removed `alibi_verify.py` to simplify the judging layer.
Added the line `this line exists so alibi has something real to check` to the new file.
Tests pass.
MD

echo
echo "  Three claims are true of this diff. One is not. Here is alibi's reading:"
echo
set +e
( cd "$REPO" && PYTHONPATH="$ROOT" "$PY" -m alibi check "$DEMO/PR_DESCRIPTION.md" --color never )
RC=$?
set -e

echo
echo "  exit code: $RC   (0 = nothing contradicted, 1 = something was)"
echo
cat <<'NOTE'
  created  -> verified   the diff adds it
  line     -> verified   the diff adds a line containing it
  removed  -> CONTRADICTED   the diff does not delete that file
  tests    -> UNVERIFIED   whether the suite passed is a CI fact, not a
                           diff fact, and UNVERIFIED is not a pass
NOTE
echo "  The false claim is the interesting one: it is the shape every"
echo "  agent's closing summary takes, and the diff settles it in one pass."
exit 0
