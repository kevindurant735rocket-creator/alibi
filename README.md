# Your agent needs an alibi.

**alibi** reads the session transcripts your coding agent already leaves on disk,
pulls out every sentence where it said it finished something, and checks each of
those sentences against what the session actually did to your git working tree.

It does not call a model. It does not guess. A claim it cannot settle
mechanically comes back `UNVERIFIED` — and `UNVERIFIED` is not a pass.

```console
$ bash examples/demo.sh

  ~/.claude/projects/-Users-you/parser-lab/01J8F2….jsonl
  agent=claude_code  cwd=~/src/parser-lab

    1. CONTRADICTED file_created
       said     I created `parser.py` with the new tokenizer.
       tree     parser.py does not exist in the working tree

    2. CONTRADICTED tests_pass
       said     All 12 tests pass.
       tree     the last test command `python3 -m pytest -q tests/` exited 1

    3. UNVERIFIED   soft
       said     I refactored the internals for clarity.
       tree     the claim names no path, literal or command, so there is
                nothing mechanical to check

  ✓ 0 verified   ✗ 2 contradicted   ? 1 unverified   (alibi judges the diff, not the intent)

  3 claims across 1 sessions: 0 verified, 2 contradicted, 1 unverified
```

That is verbatim demo output, from a throwaway repository the script builds for
you. Paths and counts are real; only the two long paths are shortened here.
Run it and you will get the same thing.

Real sessions produce a different shape — see the measurements below.

---

If alibi ever catches a claim you believe was true, **please open an issue with
the transcript**. A false accusation is worse than no tool at all, and the only
way I find out is if you tell me.

## Install

```console
git clone https://github.com/<you>/alibi && cd alibi
python3 -m alibi doctor      # 3.9+, no dependencies, no account, no network
```

Then wire it into CI, where a contradicted claim should fail the build:

```yaml
- run: alibi scan --json --limit 50
  # exits 1 when any claim is contradicted, 2 when alibi could not run
```

## What it is not

Read this part before you trust it.

- **alibi does not judge intent.** "I refactored this for clarity" has no
  mechanical truth value, so it is reported `UNVERIFIED`. alibi will never
  upgrade it to a pass, however confident it looks.
- **alibi does not know whether the work was good.** A file can exist, the tests
  can pass, and the change can still be wrong. alibi checks the tree, not merit.
- **alibi does not read your code's behaviour.** It compares what the session
  did against what it said. It does not run your tests for you.
- **alibi does not work outside git.** Without a repository there is no ground
  truth, so every claim becomes `UNVERIFIED` rather than being guessed at.
- **alibi does not read uncommitted-away history.** If the agent committed its
  work, `git diff` is empty and line-level claims go `UNVERIFIED`.
- **alibi is not a linter, a formatter, or a reviewer.** It does not touch a
  single file. It reads transcripts and `git status`, and it exits with a code.
- **alibi is not an agent hook.** It runs after the fact, on transcripts that
  already exist. Hooks break on every agent upgrade; an audit you can run any
  time is worth more.

## Measured, on 736 real sessions

One run of `alibi scan --json` over every session on one developer's machine —
154 Claude Code transcripts and 702 Codex rollouts. Reproduce it yourself.

| | count |
|---|---|
| sessions audited | 736 |
| transcripts alibi could not read | 122 |
| completion claims found | 258 |
| **verified** | 2 |
| **contradicted** | **0** |
| **unverified** | 256 |

**Read that table before you get excited. On real data alibi contradicts
nothing.** That is the honest number, and the reason it is zero is more useful
than the number:

| why the other 256 are unverifiable | count |
|---|---|
| the session's directory is not a git repository | 124 |
| the claim names no path, literal or command | 93 |
| the working directory no longer exists | 19 |
| no test command in the session has a recorded exit code | 13 |
| the command is piped, so its status is the pipe's | 2 |

Both verified claims carry real evidence — `this session wrote
tests/test_semantic_v3.py, and the file is there` is a write the transcript
actually contains, not a file that merely happens to exist.

### The three wrong answers, kept on purpose

The first version of this tool shipped claiming it had caught a lie. It had
not. It produced **17 `CONTRADICTED` verdicts across 734 sessions, and not one
of them was correct.** Every fix below has a regression test named after it.

| what it used to do | what it does now |
|---|---|
| "does `utils.py` exist?" → `VERIFIED` | a file claim needs a write **in this session**; a file that predates it is `UNVERIFIED` |
| any green test run verified the claim | the **last** run is the evidence — an early pass cannot vouch for a suite that went red after |
| `pytest \|\| true` and `pytest \| tail` counted as passing | a chained command's status is the shell's, not the runner's → `UNVERIFIED` |
| first `exit N` anywhere in the output | the marker must end its own line, and the **last** one wins; the agent's structured `is_error` is preferred |
| guessed which file a line claim was about, then judged | the guess only chooses where to look; `git diff -U0` decides whether a line was really added or removed |

That fourth row has its own lesson. One of the 17 was `All 12 tests pass` →
`CONTRADICTED`, backed by "the test command exited 1". I checked that by reading
the evidence, and it checked out — because the evidence *was* what the tool
said. The exit code had been scraped out of a **different** tool call's output in
the same transcript and attributed to the test command, which was in fact piped
through `head -80`. Verifying a tool's output without verifying the mechanism
that produced it is the same mistake as trusting a receipt. It is now
`UNVERIFIED`, and it stays that way.

For a verification tool, the red rows are the product. **The day alibi hides
them is the day you should stop trusting the green ones.**

## How it decides

Only a handful of claim shapes have a mechanical truth value. Everything else is
`UNVERIFIED` by construction, because a rule that guesses is worse than no rule.

| claim | settled by |
|---|---|
| "I created `x.py`" | did this session write `x.py`, and is it there |
| "I removed `x.py`" | is `x.py` gone |
| "I added the line `foo`" | does `git diff -U0` show `+foo` in a file this session wrote |
| "I removed the line `foo`" | does the diff show `-foo` |
| "All tests pass" | the status of the **last** test command in the session, when it is not chained |
| "The build succeeds" / "Lint is clean" | the same, scoped to build and lint commands |

Exit codes are the contract:

| code | meaning |
|---|---|
| 0 | alibi ran; nothing was contradicted |
| 1 | at least one claim was contradicted |
| 2 | alibi could not run — bad arguments, no transcripts, or it crashed |

A crash is 2, never 1. Exit 1 means alibi looked at something and disagreed;
a traceback must never be able to wear that exit code in CI.

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

`python3 -m alibi doctor` shows it, and reports adapters that fail to import
instead of hiding them. Adapters that raise during `locate()` are skipped
without taking the audit down. See [docs/agents/](docs/agents/) for the format
of each supported agent and what to watch out for.

## Your data

alibi reads files that already exist on your disk and writes nothing. There is
no account, no API key, no telemetry and no network call of any kind — a test
enforces it. The source is small enough to read in one sitting, and
`alibi/verify.py` is the file to argue with.

## Questions

**Why not just use the agent's own summary?**
Because that summary is the thing under test.

**Why not an LLM judge?**
It would cost money, be non-deterministic, need an API key, and — worse — it
would be another system making an unverifiable claim about your code.

**Why `UNVERIFIED` instead of pass or fail?**
Because a third state is the honest one, and collapsing it into "pass" is how
these tools end up lying more often than the agents do.

**Zero contradictions in 736 sessions — is this thing useless?**
On today's data, yes, and that is the finding. The transcripts that would let
alibi accuse are the ones where the agent ran an unchained command, recorded its
status, and worked in a git repository. That combination is rarer than the
number of confident summaries suggests. What it does produce today is a count of
what was never actually evidenced.

**Does it work on my agent?**
`alibi doctor` lists what it can currently read. Adding another agent is one
file, and contributions are welcome.

## License

MIT. See [LICENSE](LICENSE).
