"""Agent adapters: the single-file extension contract.

Adding support for another coding agent means adding ONE file to this package
that defines the four names below. Nothing else in alibi needs to change.

    NAME        str    unique lowercase id, e.g. "claude_code"
    SUMMARY     str    one line, shown by `alibi doctor`
    locate()    (explicit: str | None) -> list[Path]
                       where this agent stores its session transcripts.
                       Return [] when nothing is found (never raise).
    parse(path: Path) -> Session
                       read one transcript file into a Session.

A module that fails to import or raises during locate()/parse() is reported by
`alibi doctor` as broken. It never takes the whole run down.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# One exit-code parser for every adapter. The previous arrangement had a copy in
# each adapter file, and both searched the whole tool output for the FIRST
# `exit N` they could find — which happily picked the number out of a sentence
# like "see http://example.com/exit 42 docs".
#
# A real exit code is on its own line and ends that line. Anchoring to both ends,
# scanning only the tail, and taking the LAST match is what keeps a passing run
# from being read out of a log that merely mentions one.
#
# The separator is optional on purpose. Claude Code writes `Exit code 1` with no
# colon; requiring one silently stopped alibi reading the marker the real agent
# actually writes, which cost a true positive before it was noticed.
_EXIT_ANCHORED = re.compile(r"^[^\n]{0,40}?\bexit(?:\s+code)?\s*[:=]?\s*(\d{1,3})\s*$", re.I)
_TAIL_LINES = 25


def exit_code_from_output(body: str) -> Optional[int]:
    """Read an exit code out of a tool result, or None when there is not one.

    None is load-bearing: a claim that depends on an exit code we could not read
    becomes UNVERIFIED rather than a guess. Anchored lines that are not exit
    codes — URLs, prose, diff output — are ignored.
    """
    if not body:
        return None
    found = None
    for line in body.splitlines()[-_TAIL_LINES:]:
        m = _EXIT_ANCHORED.match(line.strip())
        if m:
            try:
                found = int(m.group(1))
            except ValueError:
                pass
    return found


@dataclass
class ToolCall:
    """One tool invocation the agent made, with the exit code we observed."""

    name: str
    tool_input: dict = field(default_factory=dict)
    exit_code: Optional[int] = None
    output: str = ""
    # Structured, agent-authored success flag. Preferred over anything scraped
    # out of the output text: `is_error: true` is a field the agent set, not a
    # number a regex happened to find somewhere in a log.
    is_error: Optional[bool] = None


@dataclass
class Message:
    role: str  # "user" | "assistant"
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    timestamp: str = ""


@dataclass
class Session:
    """Everything alibi needs to know about one agent session."""

    agent: str
    path: Path
    cwd: str = ""
    started_at: str = ""
    ended_at: str = ""
    git_branch: str = ""
    messages: list[Message] = field(default_factory=list)
    # Lines alibi could not parse. A quiet zero here is how a malformed or
    # wrong-shaped transcript turns into "no claims found" — an audit that
    # silently audits nothing is worse than one that refuses to run.
    skipped_lines: int = 0

    @property
    def assistant_messages(self) -> list[Message]:
        return [m for m in self.messages if m.role == "assistant"]


_REQUIRED = ("NAME", "SUMMARY", "locate", "parse")


def _module_names() -> list[str]:
    return sorted(m.name for m in pkgutil.iter_modules(__path__))


def available() -> list[str]:
    """Adapter ids whose module imports cleanly and exposes the contract."""
    out = []
    for name in _module_names():
        mod = _try_import(name)
        if mod and all(hasattr(mod, attr) for attr in _REQUIRED):
            out.append(mod.NAME)
    return sorted(out)


def _try_import(name: str):
    try:
        return importlib.import_module(f"{__name__}.{name}")
    except Exception:
        return None


def load(agent_id: str):
    """Return the adapter module for `agent_id`, or None if it is not loadable."""
    for name in _module_names():
        mod = _try_import(name)
        if mod and getattr(mod, "NAME", None) == agent_id:
            if not all(hasattr(mod, attr) for attr in _REQUIRED):
                return None
            return mod
    return None


def locate_all(only: list[str] | None = None, explicit: str | None = None) -> list[tuple[str, list[Path]]]:
    """Find transcripts for every (or the requested) adapter.

    Returns [(agent_id, [transcript paths])]. An adapter that raises is skipped
    here and surfaced by `alibi doctor` instead, so one broken agent cannot
    stop the audit of the others.
    """
    wanted = set(only) if only else None
    found = []
    for name in _module_names():
        mod = _try_import(name)
        if not mod or not all(hasattr(mod, attr) for attr in _REQUIRED):
            continue
        if wanted and mod.NAME not in wanted:
            continue
        try:
            paths = list(mod.locate(explicit))
        except Exception:
            continue
        if paths:
            found.append((mod.NAME, paths))
    return found


def parse_session(agent_id: str, path: Path) -> Session | None:
    """Parse one transcript, or None when the adapter fails on this file."""
    mod = load(agent_id)
    if mod is None:
        return None
    try:
        return mod.parse(path)
    except Exception:
        return None
