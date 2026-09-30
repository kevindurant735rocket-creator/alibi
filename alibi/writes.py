"""What this session actually wrote.

The first version of alibi asked a weaker question than the one that matters.
"I created utils.py" was marked VERIFIED whenever `utils.py` existed on disk —
even if it had been committed two years before the session and the agent had
never opened it. That is a coincidence wearing the costume of evidence, and it
made VERIFIED the one verdict the tool could issue dishonestly.

So the ground truth for file claims is not the filesystem, it is the session:
which paths did these tool calls write to? A claim that names a path the
session never touched can no longer be verified, no matter what is on disk.

Detection is deliberately conservative. A write shape alibi does not recognise
means "unknown", and unknown flows to UNVERIFIED — never to VERIFIED.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Tools whose whole job is to write a file.
_WRITE_TOOLS = {
    "Write", "Edit", "MultiEdit", "Create", "NotebookEdit", "CreateFile",
    "WriteFile", "write", "edit", "str_replace_editor", "str_replace",
    "apply_patch", "apply-patch",
}

_PATH_KEYS = (
    "file_path", "filePath", "path", "notebook_path", "target_file",
    "file", "filename",
)

# Redirect targets that are plumbing, not files.
_REDIRECT_NOISE = {"/dev/null", "/dev/stdout", "/dev/stderr", "2>&1", "&1", "&2"}


def _clean(value: str) -> str:
    value = value.strip().strip("'\"")
    value = value.replace("$HOME", "~")
    return value.strip()


def _relative(cwd: str, path: str) -> str | None:
    """Normalise an observed path so it can be compared with a claimed one."""
    path = _clean(path)
    if not path:
        return None
    p = Path(path).expanduser()
    if p.is_absolute():
        try:
            p = p.relative_to(Path(cwd).expanduser())
        except ValueError:
            return None
    return str(p).lstrip("./") or None


# Embedded forms keep the inner group non-capturing: a nested capture inside an
# alternation makes findall() return three columns, which is how the first
# version of this file crashed on every `>` redirect it was meant to detect.
_QUOTED_C = r"""['"][^'"\n]{1,300}['"]"""
_TOK = r"(?:" + _QUOTED_C + r"|[^\s;&|]+)"

_REDIRECT = re.compile(r"(?:^|[;&|]\s*|\s)(>>?|>)\s*(" + _TOK + r")")
_HEREDOC = re.compile(r"<<-?\s*['\"]?\w+")
_TEE = re.compile(r"\btee\s+(?:-a\s+|--append\s+)?(" + _TOK + r")")
_CP = re.compile(r"\bcp\s+(?:-\w+\s+)*\S+\s+(" + _TOK + r")")
_MV = re.compile(r"\b(?:mv|ln)\s+(?:-\w+\s+)*\S+\s+(" + _TOK + r")")
_TOUCH = re.compile(r"\btouch\s+(" + _TOK + r")")
_APPLY_PATCH = re.compile(r"^\*\*\*\s+(?:Add|Update|Delete)\s+File:\s+(.+?)\s*$", re.M)
_SED_INPLACE = re.compile(r"\bsed\s+-i\S*\s+(?:'[^']*'|\"[^\"]*\"|\S+)\s+(" + _TOK + r")")


def _writes_in_command(command: str, cwd: str) -> set[str]:
    """Paths a shell command plausibly wrote. Only the invocation is inspected."""
    out: set[str] = set()

    for m in _APPLY_PATCH.finditer(command):
        rel = _relative(cwd, m.group(1))
        if rel:
            out.add(rel)

    # `cat > f <<'EOF'`, `python script > out.txt`, `>>` appends
    body = _HEREDOC.sub(" ", command)
    for _flag, target in _REDIRECT.findall(body):
        target = _clean(target)
        if target in _REDIRECT_NOISE or target.startswith("/dev/") or target.startswith("&"):
            continue
        rel = _relative(cwd, target)
        if rel:
            out.add(rel)

    for pattern in (_TEE, _CP, _MV, _TOUCH, _SED_INPLACE):
        for target in pattern.findall(body):
            rel = _relative(cwd, target)
            if rel:
                out.add(rel)

    return out


def session_writes(session) -> set[str]:
    """Every path this session's tool calls wrote, relative to the session cwd.

    Returns a set. An empty set means "alibi saw no write it recognises", which
    callers must treat as absence of evidence rather than as proof of absence.
    """
    cwd = session.cwd or ""
    found: set[str] = set()

    for message in session.messages:
        for call in message.tool_calls:
            for key in _PATH_KEYS:
                value = call.tool_input.get(key)
                if isinstance(value, str) and value:
                    rel = _relative(cwd, value)
                    if rel:
                        found.add(rel)

            for key in ("command", "cmd", "script", "patch", "input"):
                raw = call.tool_input.get(key)
                if not isinstance(raw, str) or not raw:
                    continue
                found |= _writes_in_command(raw, cwd)
                # A patch may arrive as structured content rather than text.
                if isinstance(call.tool_input.get("patch"), str):
                    found |= _writes_in_command(call.tool_input["patch"], cwd)
    return found


def wrote(session, rel_path: str, writes: set[str] | None = None) -> bool:
    """Did this session write `rel_path`? Comparison is on normalised paths."""
    writes = session_writes(session) if writes is None else writes
    if rel_path in writes:
        return True
    # tolerate ./ and trailing-slash differences on the caller's side
    alt = rel_path.lstrip("./")
    return alt in writes


def resolve_claimed(session, claimed: str, writes: set[str] | None = None) -> tuple[str | None, str]:
    """Match a path an agent named in prose against paths the session touched.

    Agents say "I created `run_all.sh`" while working in a subdirectory, and
    saying so plainly is normal — not deception. Resolving that against the
    session's working directory alone turns four of twelve sampled claims into
    false accusations, because the cwd was a parent of the real work.

    So a claimed path is matched against what the session actually wrote:

      1. exact match on the normalised relative path
      2. unique match on the file name alone

    Anything less than one of those is None. A guess is not a resolution, and
    resolution is what a verdict is allowed to rest on.
    """
    if not claimed:
        return None, "claim names no path"
    writes = session_writes(session) if writes is None else writes
    want = claimed.strip().lstrip("./")
    if not want:
        return None, "claim names no path"

    if want in writes:
        return want, "exact path match"

    # A claim that names a directory is talking about a specific place. Falling
    # back to a bare file-name match there would silently reinterpret
    # "other-tree/app.py" as "app.py", and vouched for a write to a different
    # path than the one the agent named.
    if "/" in want:
        return None, "the claim names a directory, and this session did not write that exact path"

    basename = os.path.basename(want)
    if not basename:
        return None, "claim names no file"
    same_name = sorted(w for w in writes if os.path.basename(w) == basename)
    if len(same_name) == 1:
        return same_name[0], "matched on file name; the session wrote exactly one file with that name"
    if len(same_name) > 1:
        return None, f"the session wrote {len(same_name)} files named {basename}; alibi cannot tell which one was meant"

    # Not written by this session. It may still exist from earlier work, which
    # is precisely the coincidence this tool must not mistake for evidence.
    return None, "this session contains no write to a file with that name"
