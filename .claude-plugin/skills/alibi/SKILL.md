---
name: alibi
description: Audit what an AI coding agent claimed it finished, using alibi, which reads the session transcripts the agent already left on disk and checks each completion claim against the git working tree. Use this when the user wants to audit, verify or fact-check an AI agent's completion claims, asks whether an agent's "done" claims are true, wants a claim-check receipt pasted into a pull request, wants a CI job that fails when an agent contradicted itself, or asks how to add support for a new coding agent's transcripts. Triggers on "did the agent actually do that", "verify the agent's claims", "check what the agent said it did", "claim check", "alibi scan", "alibi receipt", "audit my agent sessions".
---

# alibi

Your agent needs an alibi.

alibi reads the session transcripts a coding agent already leaves on disk, pulls
out every sentence where the agent said it finished something, and checks each
of those sentences against the git working tree.

It does not call a model, does not need an account or an API key, and makes no
network call. It runs on the Python standard library plus the `git` binary.

## When to use this

- Someone wants to know whether an agent's "done" statements are backed by the
  tree.
- A pull request needs a claim-check receipt.
- A CI pipeline should fail when an agent's completion claim is contradicted.
- A user asks what an agent actually changed, or whether it actually ran the
  tests it said it ran.
- A user wants alibi to support a coding agent it does not know yet.

Do not use it to judge whether the work was good, whether the code is
readable, or whether a change is correct. alibi compares the tree against the
sentences; it says nothing about merit.

## How to run it

```bash
python3 -m alibi scan            # audit recent sessions, print a table
python3 -m alibi scan --json     # machine-readable, for CI
python3 -m alibi scan --receipt  # a markdown block to paste into a PR
python3 -m alibi doctor          # which agents alibi can read right now
```

Useful flags on `scan`: `--agent <id>` (repeatable), `--transcript <path>`,
`--limit N` (default 20), `--no-fail` (always exit 0).

```yaml
# .github/workflows/claims.yml
- run: python3 -m alibi scan --json --limit 50
  # exits 1 when any claim is contradicted
```

If the tool is not on `PATH`, run it from a clone with `python3 -m alibi`.
`bash examples/demo.sh` rebuilds a planted contradiction in a throwaway repo
if the user wants to see the output before trusting it.

## Exit codes

| code | meaning | what to do |
|---|---|---|
| 0 | nothing was contradicted | unverified claims may still be listed; they are not passes |
| 1 | at least one claim was contradicted | read the evidence lines, this is the real finding |
| 2 | alibi could not run | bad arguments, no transcripts found, or the session's cwd is not a git repo |

## Verdicts

- `VERIFIED` — the working tree agrees with what the agent said.
- `CONTRADICTED` — the working tree disagrees. This is the interesting one.
- `UNVERIFIED` — alibi could not settle it mechanically.

## The semantics that matter most

**`UNVERIFIED` is not a pass.** It is not a pass, not a partial pass, and it is
never counted in the verified total or in the exit code. It means the claim
named no path, no literal and no command, so there was nothing mechanical to
check. Most real completion sentences — "Done.", "已完成", "All done" — land
here. When reporting results, say "unverified", never "fine" or "checked".

**Report the counts honestly.** On a real run over 734 sessions alibi found 257
claims: 1 verified, 1 contradicted, 255 unverified. Do not present a clean
scan as proof the work is correct. It means no claim was mechanically falsified
in the scope that was checked.

**alibi judges the diff, not the intent.** "I refactored this for clarity" has
no mechanical truth value and stays `UNVERIFIED` no matter how plausible it
sounds.

## How to read the output

Each finding is a pair of lines:

```
  1. CONTRADICTED tests_pass
     said     All 12 tests pass.
     tree     the test command `python3 -m pytest -q tests/` exited 1,
              and no test command in this session exited 0
```

`say` is the agent's sentence. `tree` is what the working tree says. The
verdict is always the one produced by `alibi/verify.py`.

When a session has unparseable lines, alibi prints a warning and the
`--receipt` includes the count. Report it: a transcript alibi could not fully
read is an audit with a hole in it.

## Adding another agent

One file in `alibi/agents/` defining `NAME`, `SUMMARY`, `locate(explicit)` and
`parse(path)`. `locate()` must never raise, and `parse()` must set
`session.skipped_lines` for anything it cannot read. Copy
`alibi/agents/claude_code.py` as the template, then confirm the new adapter
appears in `python3 -m alibi doctor` and document it under `docs/agents/`.

Full contract and per-agent format notes: `docs/agents/README.md`,
`docs/agents/claude_code.md`, `docs/agents/codex.md`.
