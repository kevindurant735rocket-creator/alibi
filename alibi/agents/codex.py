"""Adapter for OpenAI Codex.

Transcripts: ~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-*.jsonl
Outer records are {timestamp, type, payload}; `payload.type` selects the item.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import Message, Session, ToolCall, exit_code_from_output

NAME = "codex"
SUMMARY = "OpenAI Codex — ~/.codex/sessions/**/*.jsonl"

_ROOT = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"



def locate(explicit: str | None = None) -> list[Path]:
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


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                for key in ("text", "output_text", "input_text"):
                    if isinstance(block.get(key), str):
                        parts.append(block[key])
        return "\n".join(parts)
    return ""


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def parse(path: Path) -> Session:
    session = Session(agent=NAME, path=Path(path))
    first_ts = last_ts = ""
    # call_id -> ToolCall, so function_call_output can attach the exit code
    pending: dict[str, ToolCall] = {}

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

        ts = rec.get("timestamp") or ""
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        outer = rec.get("type")
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            continue

        if outer == "session_meta":
            session.cwd = payload.get("cwd") or session.cwd
            continue
        if outer == "turn_context":
            session.cwd = payload.get("cwd") or session.cwd
            continue

        kind = payload.get("type")
        if kind == "message":
            if payload.get("role") != "assistant":
                continue
            text = _text_of(payload.get("content")).strip()
            if text:
                session.messages.append(Message(role="assistant", text=text, timestamp=ts))

        elif kind == "function_call":
            call = ToolCall(
                name=str(payload.get("name", "")),
                tool_input=_parse_args(payload.get("arguments")),
            )
            cid = payload.get("call_id")
            if cid:
                pending[cid] = call
            session.messages.append(Message(role="assistant", text="", tool_calls=[call], timestamp=ts))

        elif kind == "function_call_output":
            cid = payload.get("call_id")
            call = pending.get(cid)
            if call is None:
                continue
            body = payload.get("output")
            if isinstance(body, dict):
                body = json.dumps(body, ensure_ascii=False)
            body = body if isinstance(body, str) else ""
            call.output = body
            call.exit_code = exit_code_from_output(body)

    session.started_at = first_ts
    session.ended_at = last_ts
    return session
