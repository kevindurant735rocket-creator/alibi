#!/usr/bin/env bash
# Everything CI runs, in one command. Exits nonzero on the first failure.
set -euo pipefail
cd "$(dirname "$0")/.."

# Windows' git-bash usually ships `python` and not `python3`, so the obvious
# spelling of this script fails there for a reason that has nothing to do with
# the code under test.
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python

fail=0
step() { printf '\n=== %s\n' "$1"; }

step "1/5 unit tests"
"$PY" -m unittest discover -s tests 2>&1 | tail -4

step "2/5 standard library only"
if "$PY" - <<'PY'
import sys, pathlib
# sys.stdlib_module_names is authoritative per interpreter, so this check does
# not rot into a hand-kept allowlist that quietly falls behind.
stdlib = set(sys.stdlib_module_names) | {"__future__"}
bad = []
for path in pathlib.Path("alibi").rglob("*.py"):
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not (line.startswith("import ") or line.startswith("from ")):
            continue
        if line.startswith("from ."):
            continue
        if "__import__" in line:
            bad.append(f"{path}:{lineno}: __import__ hides an import from this check")
            continue
        module = line.split()[1].split(".")[0]
        if module not in stdlib:
            bad.append(f"{path}:{lineno}: {module}")
if bad:
    print("non-stdlib imports:\n  " + "\n  ".join(bad))
    sys.exit(1)
print(f"all imports are standard library (python {sys.version.split()[0]})")
PY
then :; else fail=1; fi

step "3/5 no network calls in source"
if grep -rnE '\b(urllib\.request|http\.client|socket|requests|urlopen|fetch\()' alibi/ --include='*.py'; then
  echo "FAIL: source contains a network call; alibi must work offline"
  fail=1
else
  echo "no network primitives found in alibi/"
fi

step "4/5 demo transcript"
# Not `demo.sh | grep -q …`: under pipefail, grep -q closes the pipe on its
# first match, the demo dies of SIGPIPE, and the pipeline reports 141 — a
# failure that looks exactly like a missing catch.
demo_out=$(bash examples/demo.sh 2>&1)
if printf '%s' "$demo_out" | grep -q "exit code: 1" \
   && printf '%s' "$demo_out" | grep -q "CONTRADICTED"; then
  echo "demo contradicted a planted claim and exited 1, as required"
else
  echo "FAIL: demo did not catch the planted lie"
  printf '%s\n' "$demo_out" | tail -20
  fail=1
fi

step "5/5 CLI surface"
"$PY" -m alibi --version
"$PY" -m alibi doctor >/dev/null && echo "doctor ok"
"$PY" -m alibi scan --help >/dev/null && echo "scan --help ok"
if "$PY" -m alibi scan --transcript /definitely/not/here.jsonl >/dev/null 2>&1; then
  echo "FAIL: scanning a missing transcript should exit 2"
  fail=1
else
  echo "missing transcript exits 2, as documented"
fi

# A crash must not be able to wear the exit code that means "contradicted".
# That is the one failure mode this tool cannot have, so it is pinned here.
crash_probe=$(mktemp -d)
trap 'rm -rf "$crash_probe"' EXIT
mkdir -p "$crash_probe/repo" "$crash_probe/s"
( cd "$crash_probe/repo" && git init -q \
  && git config user.email t@e.c && git config user.name t \
  && echo x > f.py && git add -A && git commit -qm i )
mkdir -p "$crash_probe/repo/weird.py"
"$PY" - "$crash_probe/repo" "$crash_probe/s" <<'PY'
import json, os, sys
repo, out = sys.argv[1], sys.argv[2]
rec = {"type": "assistant", "cwd": repo,
       "message": {"role": "assistant", "content": [
           {"type": "text", "text": "I removed `weird.py`."}]}}
with open(os.path.join(out, "a.jsonl"), "w") as fh:
    fh.write(json.dumps(rec) + "\n")
PY
crash_out=$("$PY" -m alibi scan --transcript "$crash_probe/s" --color never 2>&1) && crash_rc=0 || crash_rc=$?
if printf '%s' "$crash_out" | grep -q "Traceback"; then
  echo "FAIL: a crash escaped to the user instead of being reported"
  fail=1
else
  echo "a directory-shaped path is handled, no traceback (exit $crash_rc)"
fi

printf '\n'
if [ "$fail" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "CHECKS FAILED"
fi
exit "$fail"
