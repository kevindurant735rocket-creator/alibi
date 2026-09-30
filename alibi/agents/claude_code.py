"""Adapter for Claude Code.

Transcripts: ~/.claude/projects/<mangled-cwd>/<session-id>.jsonl
One JSON object per line; `type` selects the record kind.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import Message, Session, ToolCall

NAME = "claude_code"
SUMMARY = "Claude Code — ~/.claude/projects/**/*.jsonl"

_ROOT = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"


def locate(explicit: str | None = None) -> list[Path]:
    """All Claude Code transcripts, newest first."""
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return [p]
        if p.is_dir():
            return sorted(p.rglob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
        return []
    if not _ROOT.is_dir():
        return []
    files = [f for f in _ROOT.rglob("*.jsonl") if f.is_file()]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files


def _blocks(message: dict) -> list[dict]:
    content = message.get("content")
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _tool_calls(blocks: list[dict]) -> list[ToolCall]:
    calls = []
    for b in blocks:
        if b.get("type") != "tool_use":
            continue
        raw = b.get("input")
        calls.append(
            ToolCall(
                name=str(b.get("name", "")),
                tool_input=raw if isinstance(raw, dict) else {},
            )
        )
    return calls


def parse(path: Path) -> Session:
    session = Session(agent=NAME, path=Path(path))
    # tool_use_id -> ToolCall, so a following tool_result can attach its exit code
    pending: dict[str, ToolCall] = {}
    first_ts = last_ts = ""

    for line in Path(path).read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            session.skipped_lines += 1
            continue
        if not isinstance(rec, dict):
            session.skipped_lines += 1
            continue

        kind = rec.get("type")
        ts = rec.get("timestamp") or ""
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        if rec.get("cwd"):
            session.cwd = rec["cwd"]
        if rec.get("gitBranch"):
            session.git_branch = rec["gitBranch"]

        if kind == "assistant":
            blocks = _blocks(rec.get("message") or {})
            text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
            calls = _tool_calls(blocks)
            for c in calls:
                block_id = next(
                    (b.get("id") for b in blocks if b.get("type") == "tool_use" and b.get("input") is c.tool_input),
                    None,
                )
                if block_id:
                    pending[block_id] = c
            if text or calls:
                session.messages.append(Message(role="assistant", text=text, tool_calls=calls, timestamp=ts))

        elif kind == "user":
            blocks = _blocks(rec.get("message") or {})
            text = "\n".join(b.get("content", "") for b in blocks if isinstance(b.get("content"), str)).strip()
            # tool_result records carry the exit status Claude saw
            for b in blocks:
                if b.get("type") == "tool_result":
                    call = pending.get(b.get("tool_use_id"))
                    if call is None:
                        continue
                    body = b.get("content")
                    body = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
                    call.output = body
                    code = _exit_code_from_output(body)
                    if code is not None:
                        call.exit_code = code
            if text and not blocks:
                session.messages.append(Message(role="user", text=text, timestamp=ts))

    session.started_at = first_ts
    session.ended_at = last_ts
    return session


_EXIT_RE = __import__("re").compile(r"exit(?:ed with)?(?:\s+code)?\s*[:=]?\s*(\d+)", __import__("re").I)


def _exit_code_from_output(body: str) -> int | None:
    """Read an exit code out of a tool result.

    Claude Code writes it as `Exit code: N`; the shell tool's own failures also
    surface as `<command> failed with exit code N`. When neither marker is
    present we do NOT guess: exit_code stays None and any claim that depends on
    it becomes UNVERIFIED rather than VERIFIED.
    """
    if not body:
        return None
    m = _EXIT_RE.search(body)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None
