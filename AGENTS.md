# AGENTS.md

Repository rules for coding agents working on alibi. Read this before you
change anything.

## What this project is

alibi reads the session transcripts an AI coding agent leaves on disk, pulls out
every sentence where the agent said it finished something, and checks each of
those sentences against the git working tree. It outputs `VERIFIED`,
`CONTRADICTED` or `UNVERIFIED` per claim, plus a CI exit code.

Python 3.9+, standard library only, plus the `git` binary. No model call, no
network, no telemetry, no third-party dependency. That is a promise the test
suite and CI both enforce, not a preference.

## The one rule you must not break

**Never let alibi accuse an agent it cannot prove.**

- A false `CONTRADICTED` is strictly worse than a missed one. It teaches people
  that alibi cries wolf, and once they learn that they stop reading it. The
  first version of these rules produced 17 contradictions across 734 real
  sessions and **not one of them was correct**. That number stays in the README
  on purpose.
- Anything not mechanically decidable returns `UNVERIFIED`. Not `VERIFIED`.
  Not `CONTRADICTED`. `UNVERIFIED`.
- `UNVERIFIED` must never be counted as a pass, in the terminal table, in the
  JSON summary, in the receipt, or in the exit code. If you write code that
  makes that possible, you have broken this rule.
- Absence of evidence is never evidence of presence. "No matching command was
  found" means the claim cannot be checked here — it does not mean the claim is
  true.
- Never widen a guard to make a verdict fire more often. If a rule cannot
  decide a case, narrow it or move the claim to the `soft` kind.

If you believe alibi returned a wrong verdict, open an issue with the
transcript. The burden of proof is on alibi, not on the reporter.

## Layout

```
alibi/
  __init__.py       version, package docstring
  __main__.py       `python3 -m alibi`
  cli.py            argparse, the scan/doctor commands, exit codes
  claims.py         claim extraction — finds sentences, never judges them
  verify.py         the only place a verdict is produced
  groundtruth.py    the git working tree snapshot every verdict is checked against
  report.py         terminal table, --json, --receipt
  agents/
    __init__.py     Session / Message / ToolCall + the adapter contract
    claude_code.py  reference adapter
    codex.py        OpenAI Codex adapter
tests/test_alibi.py the entire suite
tools/run_all.sh    everything CI runs, in one command
examples/demo.sh    a throwaway repo with a planted contradiction
docs/agents/        one page per supported agent, for adapter authors
```

Dependencies point one way: `cli` calls `claims`, `groundtruth` and `agents`;
`verify` consumes the `Claim` and `RepoFacts` those produce. `verify.py` does
not import `cli`, and no module imports anything outside the standard library.

## Running things

```bash
python3 -m unittest discover -s tests -v   # 28 tests, the whole suite
python3 -m alibi doctor                   # which agents can alibi read right now
python3 -m alibi scan --limit 5           # audit a handful of recent sessions
bash tools/run_all.sh                     # unit tests + stdlib + network + demo + CLI
bash examples/demo.sh                     # watch it catch a planted lie
```

Run `bash tools/run_all.sh` before you open a pull request. It is what CI
runs, so anything it does not cover, CI will not cover either.

## Adding an agent adapter

One file in `alibi/agents/`. Nothing else in the codebase changes — the loader
discovers modules with `pkgutil.iter_modules` and checks for four names.

```python
NAME     = "my_agent"          # unique lowercase id
SUMMARY  = "My Agent — ~/.my_agent/sessions/**"

def locate(explicit: str | None = None) -> list[Path]: ...
def parse(path: Path) -> Session: ...
```

Start by copying `alibi/agents/claude_code.py`. Two hard requirements:

- **`locate()` never raises.** Return `[]` when nothing is found. One broken
  adapter must not stop the audit of every other agent.
- **`parse()` counts what it could not read.** Increment
  `session.skipped_lines` for every unparseable line. A transcript that
  silently parses to nothing is indistinguishable from a clean session, and
  that is exactly how an audit ends up auditing nothing while reporting
  success.

Verify with `python3 -m alibi doctor`; your adapter should appear with a file
count. Add a test that plants a transcript in the agent's real format and
asserts the session parses. When it works, write the page
`docs/agents/<your_agent>.md` following the four-section shape used by
`docs/agents/claude_code.md`: where the data lives, what a record looks like,
what traps you had to work around, and how to write an isomorphic adapter.

## Adding a verdict rule

Rules live in `alibi/verify.py` and must be decidable without a model, against
something in the working tree. Before a pull request, add two tests: one that
plants a claim the rule must catch, and one it must leave alone. The second
test is the one that matters — a rule with no negative test is a rule that will
eventually accuse someone wrongly.

If your rule would need to understand what the agent *meant*, it does not
belong in `verify.py`. Add the phrasing to the `soft` claim kind in
`claims.py` so it is reported as `UNVERIFIED` instead.

## Style

- Match the surrounding code. The source is small and plain on purpose; it is
  meant to be readable in one sitting.
- **Comments explain why a rule is narrow, never what the line does.** If the
  code already says `scoped = _SCOPED.get(kind)`, do not write a comment about
  scoped lookups. Write down the failure that made the guard necessary and the
  number of wrong verdicts it caused, because that is the only thing that stops
  the next person from widening it back.
- Widen defensive guards rather than narrowing them. A `try` that catches one
  exception type is the same bug waiting to happen again; catch `Exception`
  where a crash would take down an audit.
- No new dependencies. If you think you need one, you are probably about to
  make a verdict non-mechanical.
- Do not add a network call, a telemetry call, or anything that writes outside
  a temp directory. CI greps for exactly this.

## Things that are already known and still true

- Most completion sentences name no path, no literal and no command. 255 of 257
  real claims are `UNVERIFIED`, and that is the correct answer. Do not "fix" it
  by loosening the rules.
- Exit codes are a public contract: `0` nothing contradicted, `1` at least one
  contradiction, `2` alibi could not run. `tools/run_all.sh` and CI both pin
  them.
