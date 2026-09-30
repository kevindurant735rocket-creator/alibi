# OpenAI Codex

Adapter source: [`alibi/agents/codex.py`](../../alibi/agents/codex.py).
Adapter id: `codex`.

## 1. Where the data lives

```
~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-<timestamp>-<uuid>.jsonl
```

`locate()` does `rglob("*.jsonl")` under `$CODEX_HOME/sessions`, falling back to
`~/.codex/sessions` when unset:

```python
_ROOT = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"
```

The tree is partitioned by session start date, so the date in the filename is
also the date in the path. That is convenient for a human looking for a
transcript and irrelevant to the adapter — `rglob` does not care, and
`--transcript <file>` is the precise way to point alibi at one rollout.

As with Claude Code, the result is sorted by `st_mtime`, newest first, because
`cli.py` applies `--limit` while iterating. An unsorted `locate()` audits the
oldest rollouts on the machine and the user sees a scan that looks like it ran
and found nothing.

`CODEX_HOME` is honoured so the adapter can be pointed at a fixture directory
in tests without reading anyone's real sessions.

## 2. What a record looks like

The envelope is **three levels deep**, and that is the whole difference from
Claude Code. Every line is an object of the form:

```json
{"timestamp": "2026-08-27T12:14:54.000Z", "type": "response_item", "payload": {...}}
```

`type` is the *outer* record kind, and `payload.type` is the item kind inside
it. Two nested `type` keys is the first thing that trips people up. The outer
kinds seen in real rollouts: `session_meta`, `turn_context`, `response_item`,
`event_msg`, `world_state`, `token_usage_record`, `compacted`,
`inter_agent_communication_metadata`.

The `cwd` lives in `payload.cwd` of the very first `session_meta` record, and
is repeated on each `turn_context`:

```json
{"timestamp": "...", "type": "session_meta",
 "payload": {"id": "01a0e82b-…", "cwd": "/Users/you/src/api-gateway",
             "cli_version": "…", "originator": "codex_cli_rs", "model_provider": "openai"}}
```

`turn_context` also carries `cwd`, and the adapter takes whichever it reads
last — a rollout that changes working directory mid-session settles on its
final `cwd`, same as the Claude Code adapter. If neither is present,
`session.cwd` is `""`, no repository is found, and every claim in the session
comes back `UNVERIFIED`.

Assistant prose is a `response_item` whose payload is a message:

```json
{"timestamp": "...", "type": "response_item",
 "payload": {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "All 12 tests pass."}]}}
```

`role` is `"assistant"`, `"user"` or `"developer"`. Only `assistant` messages
become claims — a `developer` message is prompt boilerplate, and reading it as
an agent claim would manufacture contradictions out of the system prompt. The
adapter checks the role explicitly rather than assuming.

Tool calls and their results are two separate `response_item` records paired by
`call_id`:

```json
{"type": "response_item", "payload": {"type": "function_call",
  "name": "exec_command", "call_id": "call_function_3ggwvdb2rpf2_1",
  "arguments": "{\"cmd\":\"python3 -m pytest -q tests/\"}"}}

{"type": "response_item", "payload": {"type": "function_call_output",
  "call_id": "call_function_3ggwvdb2rpf2_1",
  "output": "Chunk ID: bceb22\nWall time: 0.39 seconds\nProcess exited with code 1\nOutput:\nFAILED tests/test_api.py"}}
```

Note `arguments` is a **JSON string**, not an object — it has to be decoded a
second time. And note the `output` is a wrapper carrying `Chunk ID`, `Wall
time` and `Process exited with code N` before the real output; the exit status
is prose embedded in a chunk envelope, not a field.

`output` is usually a string but is sometimes an array of content blocks (both
occur in real rollouts), so the adapter JSON-encodes the non-string form before
scanning it for the exit code.

## 3. Traps we worked around

**The double `type`.** The first implementation looked for
`payload.type == "message"` and stopped there, which works, but it is easy to
write the check against the outer `type` instead and get zero messages from
every rollout — a silent zero-claim scan that exits 0 and looks like a clean
result. The adapter deliberately reads both: the outer `type` only to pick up
`session_meta` and `turn_context` for `cwd`, and `payload.type` for the item
kind.

**`event_msg` duplicates `response_item`.** Both carry the same material, and
`event_msg` wraps each item in a `payload.item` with a different `payload.type`
(`item_completed`, `token_count`, `task_started`, …). Reading both would
double-count every message. The adapter only dispatches on `payload.type`
values it recognises, so the `event_msg` shapes fall through untouched.

**`arguments` is a JSON string inside a JSON line.** Two levels of decoding.
A transcript line that looks like malformed JSON is not malformed — it is a
record whose `arguments` string just has not been decoded yet. `_parse_args`
returns `{}` on failure rather than raising, so one odd record costs you one
claim instead of the whole session.

**The exit code is buried in a chunk envelope.** There is no `exit_code` key
anywhere. It appears as a line inside `output`, and the wording is Codex's own
— `Process exited with code 0` — not Claude Code's `Exit code: 0`. The regex is
deliberately broader than either spelling:

```python
_EXIT_RE = re.compile(r"exit(?:ed with)?(?:\s+code)?\s*[:=]?\s*(\d+)", re.I)
```

When nothing matches, `exit_code` stays `None` and dependent claims become
`UNVERIFIED`. It is never treated as 0. Guessing here is how a tool ends up
reporting "verified" for a test run that never happened.

**The command key is `cmd`, not `command`.** `exec_command` takes
`{"cmd": "…", "workdir": "…", "yield_time_ms": …, "max_output_tokens": …}`.
`verify._command_of` reads `command` first and falls back to `cmd`, so both
agents' transcripts work through the same rule — but an adapter that copied the
Claude Code adapter's key assumption would silently find no command text and
mark every exit-code claim `UNVERIFIED`.

**Known gap, stated plainly: the tool name does not match.** `exec_command` is
not in `verify._COMMAND_TOOLS` (which holds `Bash`, `bash`, `Shell`, `shell`,
`run`, `exec`, `local_shell`, `container.exec`), so `_shell_calls` does not
currently pick Codex shell calls up. The result is not a wrong verdict — it is
that `tests_pass`, `build_passes` and `lint_clean` claims from Codex rollouts
resolve to `UNVERIFIED` with the honest reason "this session ran no test
command whose exit code is recorded". File, string and `soft` claims are
unaffected. The right fix is to add `exec_command` to that set in
`alibi/verify.py`, not to rename the tool in the adapter; the adapter's job is
to report the agent's own vocabulary. Until then, expect a lower verified count
on Codex than the transcripts appear to support.

**Count what you could not parse.** `skipped_lines` is incremented for any line
that fails `json.loads` or is not an object. Rollouts also contain
`compacted` and `inter_agent_communication_metadata` records whose payloads have
no `type`; those are skipped *silently* because they are well-formed records of
an uninteresting kind, which is different from a line that failed to decode.
Do not conflate the two.

## 4. Writing an isomorphic adapter

Start from `claude_code.py` — the control flow is the same, only the shape of
the data differs. What changes for a nested-envelope agent:

1. **Locate.** Same as everywhere: honour the relocation env var (`CODEX_HOME`
   here), `rglob` or `glob` for the real layout, sort by `st_mtime` newest
   first, accept a file or a directory for `explicit`, return `[]` on anything
   unexpected.
2. **Unwrap the envelope.** Read the outer `type` and the inner `payload.type`.
   Decide once, in a comment, which level owns which field, so the next reader
   does not have to re-derive it.
3. **Take `cwd` from the metadata record**, not from the message records, and
   keep taking it from every record that carries one. A rollout with no `cwd`
   produces a session with no repository and therefore no verdicts.
4. **Filter by role before treating text as a claim.** `role == "assistant"`
   only. Codex also emits `developer` and `user` messages, and system-prompt
   text is not an agent claim.
5. **Pair calls with results by `call_id`**, using a `pending` dict exactly as
   the Claude Code adapter does. The field name is the agent's; the pattern is
   ours.
6. **Decode a double-encoded payload** (`arguments` is a string) defensively:
   on failure return `{}` and keep going, do not raise. One weird record must
   not cost you the session.
7. **Regex the exit code out of the result body.** The wording is the agent's
   own, the position is unpredictable, and the body may be a string or a list.
   No match means `None`, and `None` means `UNVERIFIED`.
8. **Keep the agent's own tool names.** Do not normalise them in the adapter.
   `verify._COMMAND_TOOLS` is the shared vocabulary; if an agent uses different
   words, that is a change in the verifier, made in the open, where the blast
   radius of a wrong verdict is visible.
9. **Do not duplicate records.** If a format restates the same item under two
   record kinds, dispatch on only the one you have decided is authoritative.
10. **Test it.** Plant a rollout with `json.dumps` in a loop, assert `cwd`, the
    assistant message count, and the `exit_code` on a paired call. Then confirm
    `python3 -m alibi doctor` lists the adapter with a file count.

The full rules are in [`../README.md`](../README.md).
