# Agent adapters

Adding support for another coding agent means adding **one file** to
`alibi/agents/`. Nothing else in alibi changes — not the loader, not the
verifier, not the CLI.

That promise is what makes the extension surface auditable: the only way to
teach alibi a new transcript format is to write code a human can read, and that
code is a file in a package, next to the two adapters that already ship.

## The contract

Every module in `alibi/agents/` that defines these four names is picked up
automatically by `pkgutil.iter_modules`:

| name | signature | contract |
|---|---|---|
| `NAME` | `str` | unique lowercase id, e.g. `"claude_code"`. This is what `--agent` and the JSON output use. |
| `SUMMARY` | `str` | one line, shown by `alibi doctor`. Usually the product name and the glob path. |
| `locate(explicit)` | `(explicit: str \| None) -> list[Path]` | where this agent stores its transcripts. Return `[]` when nothing is found. **Never raise.** |
| `parse(path)` | `(path: Path) -> Session` | read one transcript file into a `Session`. |

Import the data classes from the package rather than redefining them:

```python
from . import Message, Session, ToolCall
```

## What you return

`Session` (`alibi/agents/__init__.py`):

| field | what alibi does with it |
|---|---|
| `agent` | echoed in the table and the JSON |
| `path` | identifies the transcript; the JSON keys findings by it |
| `cwd` | **the important one** — the repository to check claims against. Empty means every claim becomes `UNVERIFIED`. |
| `started_at` / `ended_at` | reported only |
| `git_branch` | reported only |
| `messages` | assistant messages are the source of claims; tool calls are the evidence |
| `skipped_lines` | how many lines you could not parse. Nonzero prints a warning and shows up in the receipt. |

`Message` is `role`, `text`, `tool_calls`, `timestamp`. Only
`role="assistant"` messages are read for claims. `ToolCall` is `name`,
`tool_input` (dict), `exit_code` (`int | None`) and `output`.

Only `tool_input["command"]` (or `"cmd"`) and `tool_input["file_path"]` (or
`"path"`, `"notebook_path"`, `"filePath"`) are consulted downstream, and only
from tool calls whose `name` is in `verify._COMMAND_TOOLS` for exit-code
claims. Map your agent's names onto that set, or translate the tool name in
`parse()` — see `docs/agents/codex.md` for the mapping problem in practice.

## Three rules that are not negotiable

1. **`locate()` must never raise.** Return `[]`. A missing transcript directory
   is the normal case on most machines, and an adapter that throws there takes
   the audit of every other agent down with it. The loader swallows exceptions
   and `alibi doctor` reports the adapter as broken.
2. **Count what you could not parse.** Increment `session.skipped_lines` for
   every line that fails to decode or is not an object. A transcript that
   silently parses to nothing is indistinguishable from a clean session, and
   that is precisely how an audit ends up auditing nothing while reporting
   success. The warning line and the receipt footnote both exist for this.
3. **Do not invent verdicts.** The adapter's only job is to describe what the
   agent said and what its tools returned. If you find yourself wanting to
   decide whether a claim is true, that belongs in `alibi/verify.py`, under the
   rule that anything not mechanically decidable is `UNVERIFIED`.

## The procedure

1. Copy `alibi/agents/claude_code.py` to `alibi/agents/<your_agent>.py`.
2. Point `_ROOT` at the real directory, honouring the environment variable that
   agent uses to relocate its config (Claude Code: `CLAUDE_CONFIG_DIR`; Codex:
   `CODEX_HOME`). An adapter that ignores it cannot be tested against a fixture
   directory.
3. Sort results newest-first by `st_mtime` — `cli.py` applies `--limit` to the
   list as it stands, so a badly ordered `locate()` silently audits the oldest
   sessions instead of the recent ones.
4. Accept an `explicit` argument that is a file or a directory. `--transcript`
   passes straight through to it, and it is the only way to test.
5. Pair every tool call with its result before returning. An unpaired call has
   no `exit_code`, and any claim that depends on it becomes `UNVERIFIED` — not
   `VERIFIED`.
6. Run `python3 -m alibi doctor` and confirm your agent appears with a file
   count.
7. Add a test to `tests/test_alibi.py` that plants a small transcript in the
   agent's real format and asserts the `Session` it produces.
8. Write `docs/agents/<your_agent>.md` with the four sections used by
   `claude_code.md` and `codex.md`: where the data lives, what a record looks
   like, what traps you worked around, and how to write an isomorphic adapter.

## Per-agent pages

- [`claude_code.md`](claude_code.md) — `~/.claude/projects/<mangled-cwd>/<session-id>.jsonl`
- [`codex.md`](codex.md) — `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`

## What every adapter gets wrong first

Every one of these was found by running the tool over real transcripts, not by
reading the source. They are listed on the per-agent pages with the specifics;
the short version is that the traps are all about **silence**:

- Pretty-printed JSONL parses to nothing, one object per line or it is not
  JSONL.
- A transcript shape that quietly changes yields zero claims and exit 0, which
  reads exactly like success.
- An exit code you failed to extract is `None`, and `None` must mean
  `UNVERIFIED`, never "probably fine".
- A `cwd` you failed to carry across means no repository, which means every
  claim is `UNVERIFIED` and the run is nearly worthless.
