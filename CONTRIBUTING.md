# Contributing to alibi

## The one rule

**Never make alibi accuse an agent it cannot prove.**

A false `CONTRADICTED` is worse than a missing one. It teaches people that
alibi cries wolf, and once they learn that they stop reading it. The first
version of this tool contradicted 17 claims across 734 real sessions and not
one of them was correct; the README keeps that number on purpose.

If a rule cannot be settled by something mechanical, return `UNVERIFIED`. Not
`VERIFIED`. Not `CONTRADICTED`. `UNVERIFIED`.

## Setting up

```bash
git clone https://github.com/kevindurant735rocket-creator/alibi && cd alibi
python3 -m unittest discover -s tests -v
python3 -m alibi doctor
bash docs/self_audit.sh      # the demo the README shows
```

Python 3.9+, standard library only. A test enforces the zero-dependency rule —
if your import is not in the standard library, the suite will tell you.

## Adding an agent adapter

One file in `alibi/agents/`. Copy `claude_code.py` and define:

| name | what it is |
|---|---|
| `NAME` | unique lowercase id, e.g. `"my_agent"` |
| `SUMMARY` | one line, shown by `alibi doctor` |
| `locate(explicit)` | where this agent stores transcripts; return `[]` when none found, never raise |
| `parse(path)` | read one transcript into a `Session` |

Then run `alibi doctor` and confirm your agent appears.

Two rules for adapters:

- **Never raise out of `locate()`.** Return an empty list instead. One broken
  agent must not stop the audit of the others.
- **Count what you could not parse.** Set `session.skipped_lines`. A transcript
  that silently parses to nothing looks identical to a clean session, and that
  is how an audit ends up auditing nothing while reporting success.

## Adding a rule

Rules live in `alibi/verify.py` and must be decidable without a model. Before
opening a pull request, add a test that plants a claim the rule should catch and
one it should leave alone.

If your rule would need to understand what the agent *meant*, it does not
belong here. Add it to the `soft` claim kind instead, so it is reported as
`UNVERIFIED`.

## Reporting a wrong verdict

Open an issue with the transcript. This is the most valuable report you can
file and it will be treated as a bug, not a disagreement — the burden of proof
is on alibi, not on you.

## Style

Match the surrounding code. Comments explain why a rule is narrow, never what
the line does. If a guard can be widened to stop crashing on more input, widen
it — a defensive `except` that only catches one exception type is the same bug
waiting to happen again.
