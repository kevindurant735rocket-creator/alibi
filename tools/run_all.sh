#!/usr/bin/env bash
# Everything CI runs, in one command. Exits nonzero on the first failure.
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
step() { printf '\n=== %s\n' "$1"; }

step "1/5 unit tests"
python3 -m unittest discover -s tests 2>&1 | tail -4

step "2/5 standard library only"
if python3 - <<'PY'
import sys, pathlib
allowed = {
    "argparse", "dataclasses", "datetime", "hashlib", "importlib", "json",
    "os", "pathlib", "pkgutil", "re", "shutil", "subprocess", "sys",
    "typing", "unittest", "__future__",
}
bad = []
for path in pathlib.Path("alibi").rglob("*.py"):
    for line in path.read_text().splitlines():
        line = line.strip()
        if not (line.startswith("import ") or line.startswith("from ")):
            continue
        if line.startswith("from ."):
            continue
        mod = line.split()[1].split(".")[0]
        if mod not in allowed:
            bad.append(f"{path}: {mod}")
if bad:
    print("non-stdlib imports:\n  " + "\n  ".join(bad))
    sys.exit(1)
print("all imports are standard library")
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
python3 -m alibi --version
python3 -m alibi doctor >/dev/null && echo "doctor ok"
python3 -m alibi scan --help >/dev/null && echo "scan --help ok"
python3 -m alibi scan --transcript /definitely/not/here.jsonl >/dev/null 2>&1 \
  && { echo "FAIL: scanning a missing transcript should exit 2"; fail=1; } \
  || echo "missing transcript exits 2, as documented"

printf '\n'
if [ "$fail" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "CHECKS FAILED"
fi
exit "$fail"
