# Your agent needs an alibi.

**alibi** reads the session transcripts your coding agent already leaves on disk,
pulls out every sentence where it said it finished something, and checks each of
those sentences against your git working tree.

It does not call a model. It does not guess. A claim it cannot settle
mechanically comes back `UNVERIFIED` — and `UNVERIFIED` is not a pass.

```console
$ alibi scan
  ~/.claude/projects/-Users-you/api-gateway/01J8F2….jsonl
  agent=claude_code  cwd=~/src/api-gateway  branch=fix/oauth

    1. CONTRADICTED file_created
       said     I created `middleware/rate_limit.py` with the sliding window.
       tree     middleware/rate_limit.py does not exist in the working tree

    2. CONTRADICTED tests_pass
       said     All 12 tests pass.
       tree     the test command `python3 -m pytest -q tests/` exited 1,
                and no test command in this session exited 0

    3. UNVERIFIED   soft
       said     I refactored the internals for clarity.
       tree     the claim names no path, literal or command, so there is
                nothing mechanical to check

  ✓ 0 verified   ✗ 2 contradicted   ? 1 unverified   (alibi judges the diff, not the intent)

  3 claims across 1 sessions: 0 verified, 2 contradicted, 1 unverified
```

Real run, real output. `examples/demo.sh` rebuilds that transcript in a
throwaway repo so you can watch it happen:

```console
$ bash examples/demo.sh
```

---

If alibi ever caught a claim you believe was true, **please open an issue with
the transcript**. A false accusation is worse than no tool at all, and the only
way I find out is if you tell me.

## Install

```console
git clone https://github.com/<you>/alibi && cd alibi
python3 -m alibi doctor      # 3.9+, no dependencies, no account, no network
```

Or run it without cloning:

```console
uvx --from alibi-cli alibi scan        # coming: PyPI
```

Then wire it into CI, where a contradicted claim should fail the build:

```yaml
# .github/workflows/claims.yml
- run: alibi scan --json --limit 50
  # exits 1 when any claim is contradicted
```

## What it is not

Read this part before you trust it.

- **alibi does not judge intent.** "I refactored this for clarity" has no
  mechanical truth value, so it is reported `UNVERIFIED`. alibi will never
  upgrade it to a pass, however confident it looks.
- **alibi does not know whether the work was good.** A file can exist, the tests
  can pass, and the change can still be wrong. alibi checks the tree, not merit.
- **alibi does not read your code's behaviour.** It compares what exists against
  what was said. It does not run your tests for you; it reads exit codes your
  agent already recorded.
- **alibi does not work outside git.** Without a repository there is no ground
  truth, so every claim becomes `UNVERIFIED` rather than being guessed at.
- **alibi is not a linter, a formatter, or a reviewer.** It does not touch a
  single file. It reads transcripts and `git status`, and it exits with a code.
- **alibi is not an agent hook.** It runs after the fact, on transcripts that
  already exist. That is deliberate: hooks break on every agent upgrade, and an
  audit you can run any time is worth more than an audit that stops running.

## Measured, on 734 real sessions

These numbers are from one run of `alibi scan --json` over 734 real sessions on
one developer's machine — 154 Claude Code transcripts and 702 Codex rollouts.
Reproduce them yourself; the red row is real and it stays red.

| | count |
|---|---|
| sessions read | 734 |
| completion claims found | 257 |
| **verified** | 1 |
| **contradicted** | 1 |
| **unverified** | 255 |

**Read that table before you get excited.** alibi found exactly one contradicted
claim in 734 sessions, and it is a true positive:

> agent said *"All 12 tests pass"* — the session recorded
> `node tests/auth.test.js 2>&1` exiting 1.

255 of 257 claims are `UNVERIFIED`, and that is the honest answer, not a bug to
be fixed by loosening the rules. Most completion sentences — *"已完成"*,
*"全部通过"*, *"Done."* — name no path, no literal and no command. There is
nothing to check them against. Reporting them as `UNVERIFIED` is the only
truthful thing to do.

Here is the same table with the first version of the rules, before the bugs
below were found. **The left column is where this project started:**

| | first run | now |
|---|---|---|
| claims | 257 | 257 |
| contradicted | **17** | 1 |
| of those, actually true | **0** | 1 |

Seventeen accusations, zero of them correct. That is what "a tool that catches
the AI lying" looks like when nobody checks the tool. Both of those red rows
are fixed, and the fixes are the interesting part of this repository:

- *Any nonzero exit in the session contradicted a "tests pass" claim.* A
  `ls missing_dir` returning 1 says nothing about the test suite. Exit codes are
  now scoped to the kind of command the claim is about.
- *`grep -rn 'tests' .` counted as running the tests.* Reading a directory is
  not running a suite. Read-only commands are now excluded outright.
- *Heredoc bodies and `python3 -c "…"` payloads were scanned for evidence.* A
  quoted filename inside a shell string is not a command that ran. Evidence is
  now taken only from the invocation itself.
- *`Now let me write the new verify_all.sh` was read as a completed action.* A
  plan is not a claim. Intent is now dropped before any rule runs.

If you use alibi, that table is the part worth remembering.

## How it decides

Only seven claim shapes have a mechanical truth value. Everything else is
`UNVERIFIED` by construction, because a rule that guesses is worse than no rule.

| claim | settled by |
|---|---|
| "I created `x.py`" | does `x.py` exist in the working tree |
| "I removed `x.py`" | is `x.py` gone |
| "I added the line `foo`" | is `foo` present in the files the session touched |
| "I removed the line `foo`" | is `foo` gone from those files |
| "All tests pass" | the exit code of a **test** command in that session |
| "The build succeeds" | the exit code of a **build** command |
| "Lint is clean" | the exit code of a **lint** command |

Exit codes are the contract:

| code | meaning |
|---|---|
| 0 | nothing was contradicted |
| 1 | at least one claim was contradicted |
| 2 | alibi could not run — bad arguments, no transcripts, no repository |

## Adding an agent

One file. Nothing else changes.

```bash
cp alibi/agents/claude_code.py alibi/agents/<your_agent>.py
```

Define four names and alibi discovers it:

```python
NAME     = "my_agent"
SUMMARY  = "My Agent — ~/.my_agent/sessions/**"

def locate(explicit=None) -> list[Path]: ...
def parse(path: Path) -> Session: ...
```

Run `python3 -m alibi doctor` and it shows up. Adapters that fail to import or
raise during `locate()` are reported as broken; they never take down an audit
of the other agents.

## Your data

alibi reads files that already exist on your disk and writes nothing. There is
no account, no API key, no telemetry and no network call of any kind. The
source is small enough to read in one sitting — `alibi/verify.py` is where the
judgement lives and it is the file to argue with.

## Questions

**Why not just use the agent's own summary?**
Because that summary is the thing under test.

**Why not an LLM judge?**
It would cost money, be non-deterministic, need an API key, and — worse — it
would be another system making an unverifiable claim about your code.

**Why `UNVERIFIED` instead of pass or fail?**
Because a third state is the honest one, and collapsing it into "pass" is how
these tools end up lying more often than the agents do.

**Does it work on my agent?**
`alibi doctor` lists what it can currently read. Adding another agent is one
file, and contributions are welcome.

## License

MIT. See [LICENSE](LICENSE).
