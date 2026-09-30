# Claude Code

Adapter source: [`alibi/agents/claude_code.py`](../../alibi/agents/claude_code.py).
Adapter id: `claude_code`.

## 1. Where the data lives

```
~/.claude/projects/<mangled-cwd>/<session-id>.jsonl
```

`locate()` does `rglob("*.jsonl")` under `$CLAUDE_CONFIG_DIR/projects`, falling
back to `~/.claude/projects` when that variable is unset. The root is computed
once at import time:

```python
_ROOT = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"
```

Honouring `CLAUDE_CONFIG_DIR` is what makes the adapter testable: a fixture
directory can be pointed at without touching the developer's real transcripts.

The path component is a **mangled form of the working directory**, not a
project name. `/Users/you/src/api-gateway` becomes
`-Users-you-src-api-gateway` — slashes and other path separators become dashes,
so two different directories can in principle collide. Never parse a repository
identity out of it; the authoritative `cwd` is a field inside the records
themselves (see below). Directories are keyed this way because Claude Code
partitions sessions by project directory, and one project can have many session
files.

`locate()` sorts by `st_mtime`, newest first. That ordering is load-bearing:
`cli.py` applies `--limit` while iterating the list, so an unsorted `locate()`
audits the oldest sessions on the machine instead of the recent ones, and the
user sees "no claims found" with no indication anything was wrong.

When `explicit` is passed (from `--transcript`), a file is used as-is and a
directory is `rglob`ed the same way. Anything else returns `[]`.

## 2. What a record looks like

One JSON object per line, no outer array, no pretty-printing. `type` selects
the record kind and decides which fields matter. Observed values across real
transcripts: `assistant`, `user`, `system`, `attachment`, `cost-state`, and
several bookkeeping kinds the adapter ignores (`file-history-snapshot`,
`queue-operation`, `last-prompt`, `mode`, `permission-mode`, `ai-title`).

Records carry `cwd` and `gitBranch` at the top level. The adapter takes the
**last** value it sees, so a session that changed directory mid-run settles on
its final `cwd`. If no record has a `cwd`, `session.cwd` stays `""`,
`groundtruth.collect("")` reports "session recorded no working directory",
and every claim in that session comes back `UNVERIFIED`.

An assistant record:

```json
{"type": "assistant", "timestamp": "2026-09-30T10:00:00.000Z",
 "cwd": "/Users/you/src/api-gateway", "gitBranch": "main",
 "message": {"role": "assistant",
             "content": [{"type": "text", "text": "All 12 tests pass."}]}}
```

`message.content` is an **array of blocks**, not a string. Block types seen in
real transcripts: `text`, `tool_use`, `tool_result`, `thinking`. Only the
`text` blocks are read as the agent's speech; `thinking` blocks are ignored on
purpose, since a block of reasoning is not a claim the agent made to the user.

A tool call is a `tool_use` block with a `name`, an `input` dict and an `id`:

```json
{"type": "assistant", "message": {"role": "assistant", "content": [
  {"type": "tool_use", "id": "toolu_01", "name": "Bash",
   "input": {"command": "python3 -m pytest -q tests/"}}]}}
```

The result arrives later as a `tool_result` block on a **`user`** record,
carrying `tool_use_id` and a `content` field that is a **string** most of the
time and an **array of blocks** occasionally (both occur in real transcripts,
so the adapter JSON-encodes the array form before scanning it):

```json
{"type": "user", "message": {"role": "user", "content": [
  {"type": "tool_result", "tool_use_id": "toolu_01", "is_error": true,
   "content": "1 failed, 11 passed\nExit code: 1"}]}}
```

The pairing is what makes exit codes available: the adapter keeps a
`pending: dict[tool_use_id, ToolCall]` as it scans forward and attaches the
result body to the call when the id comes back. `is_error` is **not** read.
Claude Code does not set it consistently, and inferring an exit code from a
boolean would be a guess — the rule for this project is that a guess is
worse than nothing.

## 3. Traps we worked around

**Pretty-printed JSONL silently parses to nothing.** The format is one object
per line. A heredoc that wraps an object across several lines does not raise —
`json.loads` fails on each fragment, every fragment is counted in
`skipped_lines`, and the session ends up with zero messages. `examples/demo.sh`
generates its transcript with `json.dumps` in a loop for exactly this reason,
with a comment saying so. If you write a fixture, do the same.

**Unparseable lines must be counted, not dropped.** `session.skipped_lines` is
incremented for a line that fails `json.loads` *and* for one that decodes to a
non-dict. A quiet zero is how a malformed or wrong-shaped transcript becomes
"no claims found" — an audit that silently audits nothing is worse than one
that refuses to run. The counter surfaces as a warning line in the terminal
output and as a bolded note inside `--receipt`.

**The exit code is prose, not a field.** There is no `exit_code` property. The
number appears inside the human-readable output body, and the wording varies:
Claude Code writes `Exit code: 1` on its own line, the shell tool's own
failures surface as `<command> failed with exit code 1`, and some records say
`exited with code 1`. So the adapter uses a regex rather than a field lookup:

```python
_EXIT_RE = re.compile(r"exit(?:ed with)?(?:\s+code)?\s*[:=]?\s*(\d+)", re.I)
```

When no marker matches, `exit_code` stays `None` and any claim that depends on
it becomes `UNVERIFIED`. It is never treated as 0. Bodies also contain the
command's own output, which can itself contain the word "exit" followed by
digits in an error message — the regex takes the first match in the body,
which is the marker line, because Claude Code appends it.

**Tool names are not normalised.** Real transcripts contain `Bash`, `Edit`,
`Read`, `Write` and a long tail of MCP tools (`mcp__<server>__<tool>`). Only
the names in `verify._COMMAND_TOOLS` count as commands whose exit code is
evidence, and `Bash` is the one that matters. An adapter that lowercased or
rewrote tool names would break the contract with the verifier; mapping belongs
in the tool layer, not the transcript layer.

**A user record that carries tool results is not user speech.** The adapter
only appends a `user` message when the record yielded text *and* no blocks.
Otherwise a tool result body could be read as a claim the user made. The
assistant side is the only source of claims anyway (`claims.extract_session`
walks `session.assistant_messages`).

**A thought is not a claim.** `thinking` blocks and `attachment` records are
skipped. An agent's private reasoning contains plenty of future-tense sentences
that look like claims, and `claims.py` drops intent before any rule runs — but
the cheapest place to not have the problem at all is not to read the block.

## 4. Writing an isomorphic adapter

`claude_code.py` is the template. For another agent whose transcripts are also
line-delimited JSON, the structure transfers almost unchanged:

```python
NAME = "my_agent"
SUMMARY = "My Agent — ~/.my_agent/sessions/**"

_ROOT = Path(os.environ.get("MY_AGENT_HOME", Path.home() / ".my_agent")) / "sessions"
```

1. **Find the records.** `rglob("*.jsonl")` if the format is JSONL, `glob` for
   a date-partitioned tree, whatever the agent actually does. Always sort by
   `st_mtime`, newest first.
2. **Honour the relocation variable.** If the agent has one, read it. Without
   it the adapter cannot be tested and `locate()` returns `[]` on a machine
   that has the data somewhere else.
3. **Carry `cwd` across.** It is the one field that decides whether a scan can
   produce a verdict at all. Never reconstruct it from the directory name.
4. **Buffer tool calls, then attach their results.** Keep a
   `pending: dict[result_id, ToolCall]` and fill in `output` and `exit_code`
   when the paired record arrives. A call whose result never arrives keeps
   `exit_code = None`, which is the correct, safe value.
5. **Extract the exit code with a regex over the whole body.** Assume no
   fixed field, no fixed key, and no fixed position. When nothing matches, do
   not default to 0.
6. **Count what you skipped.** `session.skipped_lines += 1` on every line that
   fails to decode or is not a dict. Do it in the same loop, before any other
   work, so no early `continue` can forget it.
7. **Never raise from `locate()`.** Return `[]` for a missing directory, a
   permission error, or anything else. The loader already defends against a
   raising adapter, but relying on that turns a working audit of the other
   agents into a partial one.
8. **Do not filter by claim-worthiness.** The adapter reports what the agent
   said; `claims.py` decides what is a claim. If you pre-filter here, the
   extraction rules and the adapter start drifting apart and the
   `soft`-claim tier starts disappearing.
9. **Test it.** Plant a transcript written by `json.dumps` in a loop, assert
   the `Session` has the right `cwd`, the right message count, and the right
   `exit_code` on a tool call. Then run `python3 -m alibi doctor` and confirm
   the adapter is listed with a file count.

The full rules are in [`../README.md`](../README.md) — three of them are not
negotiable, and two of those three exist because the quiet failure mode of a
transcript adapter looks exactly like success.
