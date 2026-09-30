#!/usr/bin/env bash
# Reproduce the thing alibi exists for, on a repository you can read.
#
# Builds a throwaway git repo, writes an agent transcript into it in which the
# agent makes four claims, then audits that transcript and prints the exit
# code. Nothing here touches your own files or your git config.
set -euo pipefail
cd "$(dirname "$0")/.."

DEMO=$(mktemp -d)
trap 'rm -rf "$DEMO"' EXIT
REPO="$DEMO/repo"
SESSIONS="$DEMO/projects/demo"
mkdir -p "$REPO" "$SESSIONS"

git -C "$REPO" init -q
git -C "$REPO" config user.email demo@example.com
git -C "$REPO" config user.name "alibi demo"
printf 'def parse(raw):\n    return raw.split()\n' > "$REPO/parse.py"
git -C "$REPO" add -A
git -C "$REPO" commit -qm initial

# The transcript is generated rather than pasted as a heredoc: JSONL is one
# object per line, and a pretty-printed heredoc silently parses to nothing —
# exactly the kind of quiet failure alibi exists to catch.
python3 - "$REPO" "$SESSIONS" <<'PY'
import json, os, sys
repo, out = sys.argv[1], sys.argv[2]

def say(text):
    return {"type": "assistant", "cwd": repo, "gitBranch": "main",
            "timestamp": "2026-09-30T10:00:00.000Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}

def tool(args):
    return {"type": "assistant", "cwd": repo, "gitBranch": "main",
            "timestamp": "2026-09-30T10:00:09.000Z",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": args}]}}

def result(body, code):
    return {"type": "user", "cwd": repo, "gitBranch": "main",
            "timestamp": "2026-09-30T10:00:10.000Z",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": body + "\nExit code: " + str(code), "is_error": code != 0}]}}

records = [
    say("Looking at the parser now."),
    say("I created `parser.py` with the new tokenizer."),
    say("All 12 tests pass."),
    tool({"command": "python3 -m pytest -q tests/"}),
    result("FAILED tests/test_parser.py::test_split\n1 failed, 11 passed", 1),
    say("I refactored the internals for clarity."),
]
with open(os.path.join(out, "01J0000000000000000000000.jsonl"), "w") as fh:
    for record in records:
        fh.write(json.dumps(record) + "\n")
PY

echo
echo "  An agent made four claims. Here is what the working tree says about them:"
echo
set +e
python3 -m alibi scan --transcript "$SESSIONS" --color never --width 92
RC=$?
set -e

echo
echo "  exit code: $RC   (0 = nothing contradicted, 1 = a claim was contradicted)"
echo
cat <<'NOTE'
  parser.py was never created.
  The test command exited 1 while the agent said all 12 tests passed.
  "I refactored the internals for clarity" stays UNVERIFIED — there is
  nothing mechanical to check, and alibi will not pretend otherwise.
NOTE
exit 0
