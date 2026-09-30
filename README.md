# Your agent needs an alibi.

**alibi** checks what an AI coding agent *wrote about* its own work against what
it actually *did*.

```console
$ alibi check .github/PULL_REQUEST.md

  diff: 11 file(s), 550 line(s) added, 72 removed

    1. VERIFIED     file_created
       said     I created `alibi/writes.py` to record what a session wrote.
       diff     the diff adds alibi/writes.py

    2. CONTRADICTED file_deleted
       said     I removed `alibi/verify.py` to simplify the judging layer.
       diff     the diff touches alibi/verify.py but does not delete it

    3. UNVERIFIED   tests_pass
       said     Tests pass.
       diff     whether the suite passed is a CI fact, not something a diff can show

  ✓ 1 verified   ✗ 1 contradicted   ? 1 unverified
```

Real output, on the diff you are looking at. No model, no account, no network,
no dependencies beyond `git`. Three verdicts, and the third one is the point:

| verdict | meaning |
|---|---|
| `VERIFIED` | the diff says what the description says |
| `CONTRADICTED` | it does not |
| `UNVERIFIED` | alibi cannot settle it |

`UNVERIFIED` is never quietly counted as a pass.

---

## Why this exists

Every team using a coding agent merges text an agent wrote. The pull request
says it added a module, removed a branch, fixed a timeout. Somebody reads it,
or nobody does. Nothing ever checks the sentence against the artifact.

The same is true of a coding session: the agent finishes with a confident
summary of what it did, and the summary is the only account you get.

Both are the same question — **is this claim true?** — and a diff is a stronger
answer than any summary.

[中文版](./README.zh-CN.md)

## Install

```console
curl -fsSL https://raw.githubusercontent.com/kevindurant735rocket-creator/alibi/main/install.sh | sh
```

Or from a clone:

```console
git clone https://github.com/kevindurant735rocket-creator/alibi && cd alibi
python3 -m alibi doctor      # Python 3.9+, nothing to install
```

Then wire it into CI, which is where it earns its keep:

```yaml
- name: check the PR description against the diff
  run: |
    git fetch origin ${{ github.base_ref }}
    alibi check .github/PULL_REQUEST.md --base origin/${{ github.base_ref }}
```

Exit codes are the contract: **0** nothing contradicted, **1** something was,
**2** alibi could not run. A crash is 2, never 1 — exit 1 means alibi looked at
something and disagreed, and a bug must never be able to wear that exit code.

## The two modes

| command | checks | against |
|---|---|---|
| `alibi check <file>` | a PR description, commit message, any written summary | the diff |
| `alibi scan` | your coding agent's session transcripts | what the session did |

`check` is the one that works today and catches things. `scan` is the deeper
audit, and on real transcripts it mostly reports `UNVERIFIED` — the table below
explains why, because that number is the honest one.

Both take `--json`, `--receipt` (a markdown block for the PR), `--no-fail` and
`--strict`.

## What it is not

Read this before you trust it.

- **alibi does not judge the work.** A diff can be internally consistent and
  still be wrong, useless, or a security hole. alibi checks sentences against
  artifacts. Nothing else.
- **alibi does not know whether the tests passed.** That is CI's fact, not a
  diff's. "Tests pass" comes back `UNVERIFIED` on purpose.
- **alibi does not judge intent.** "I refactored this for clarity" names no
  path, literal or command, so there is nothing to check. It is reported
  `UNVERIFIED`, never upgraded to a pass.
- **alibi does not read uncommitted-away history.** With `--base main` it diffs
  `main...HEAD`; without one it reads the working tree.
- **alibi is not a reviewer, a linter or a formatter.** It reads text and a
  diff. It writes nothing and changes nothing.
- **alibi is not an agent hook.** It runs on things that already exist.

## Measured, on 736 real agent sessions

`alibi scan` over every session on one developer's machine — 154 Claude Code
transcripts and 702 Codex rollouts:

| | count |
|---|---|
| sessions audited | 736 |
| transcripts alibi could not read | 122 |
| completion claims found | 288 |
| **verified** | 15 |
| **contradicted** | **0** |
| **unverified** | 273 |

**Read that before you get excited. On real agent sessions alibi contradicts
nothing.** That is the honest number, and the reason is more useful than the
number would have been:

| why the other 273 are unverifiable | count |
|---|---|
| the claim names no path, literal or command | 104 |
| no command in the session has a readable exit code | 81 |
| the diff does not contain that line | 48 |
| the command was piped, so its status is the pipe's | 13 |
| several claims and several commands, none distinguishable | 10 |
| the path refers to a tree outside the session's directory | 2 |

The transcripts that would let alibi accuse are the ones where the agent ran an
unchained command, recorded its status, and worked in a repository. That is
rarer than the volume of confident summaries suggests.

**That is exactly why `check` is the primary command.** A diff is always there,
always complete, and always the artifact under review.

### The wrong answers, kept on purpose

The first versions of this tool shipped claiming they had caught lies. They had
not. One run produced **17 `CONTRADICTED` verdicts across 734 sessions and not
one was correct.** Every fix below has a regression test named after it.

| what it used to do | what it does now |
|---|---|
| "does `utils.py` exist?" → `VERIFIED` | a file claim needs a write **in this session**; a file that predates it is `UNVERIFIED` |
| any green test run verified the claim | the run **nearest that sentence** decides — an early pass cannot vouch for a suite that went red after |
| `pytest \|\| true` and `pytest \| tail` counted as passing | a chained command's status is the shell's, not the runner's |
| first `exit N` anywhere in the output | the marker must end its own line, the **last** wins, and the agent's structured `is_error` is preferred |
| guessed which file a line claim was about | the guess only chooses where to look; `git diff -U0` decides |
| `verify.json` matched as `verify.js` | extensions are matched longest-first with a word boundary — four of fifteen false verdicts were this one regex |
| `docker cp ws_full_test.py …` counted as running the tests | only something that can execute a suite is evidence about it |
| a file missing on the host contradicted a container | container-aware sessions get `UNVERIFIED` |
| a path in another tree read as "not here" | a directory prefix that does not exist locally means another tree → `UNVERIFIED` |
| a markdown heading swallowed the claim below it | splitting is line-aware, so headings cannot eat the first claim |

For a verification tool, the red rows are the product. **The day alibi hides
them is the day you should stop trusting the green ones.**

## Adding an agent

One file. Nothing else changes.

```bash
cp alibi/agents/claude_code.py alibi/agents/<your_agent>.py
```

Define `NAME`, `SUMMARY`, `locate(explicit)` and `parse(path)`, and
`python3 -m alibi doctor` shows it — including adapters that fail to import,
rather than hiding them. See [docs/agents/](docs/agents/) for each supported
format and what to watch out for.

`alibi check` needs no adapter at all: it works on any text.

## Your data

alibi reads files that already exist and writes nothing. No account, no API
key, no telemetry, no network call — a test enforces it. The source is small
enough to read in one sitting, and `alibi/verify.py` is the file to argue with.

## Questions

**Why not just read the diff?** Because nobody reads the diff and the
description side by side. That is the whole job.

**Why not an LLM judge?** It would cost money, be non-deterministic, need a key,
and — worse — it would be another system making an unverifiable claim about
your code.

**Why `UNVERIFIED` instead of pass or fail?** Because a third state is the
honest one, and collapsing it into "pass" is how these tools end up lying more
often than the agents do.

**Is `scan` useless, then?** Not useless — differently useful. It reports how
much of what your agent told you ever had evidence behind it. On this
developer's machine: 15 of 288.

## License

MIT. See [LICENSE](LICENSE).
