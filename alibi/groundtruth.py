"""Ground truth: what the working tree actually says.

Everything alibi concludes is checked against this module and never against the
agent's own account of itself. It shells out to `git` only; there is no model
call anywhere in alibi.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RepoFacts:
    """A snapshot of one git working tree, taken once and reused by every rule."""

    root: Path | None = None
    is_repo: bool = False
    status: dict[str, str] = field(default_factory=dict)  # path -> porcelain status
    untracked: set[str] = field(default_factory=set)
    deleted: set[str] = field(default_factory=set)
    error: str = ""

    def abs(self, rel: str) -> Path | None:
        """Resolve a path the agent mentioned, refusing to escape the repo.

        A claim about `../../etc/passwd` is not evidence about this repository,
        so it resolves to None and the caller must report UNVERIFIED rather than
        reading a file the agent had no business touching.
        """
        if self.root is None:
            return None
        candidate = (self.root / rel).resolve()
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError:
            return None
        return candidate

    def exists(self, rel: str) -> bool:
        p = self.abs(rel)
        return bool(p and p.exists())

    def read(self, rel: str) -> str | None:
        p = self.abs(rel)
        if not p or not p.is_file():
            return None
        try:
            return p.read_text(errors="replace")
        except OSError:
            return None


def _git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return 127, "", "git not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", "git timed out after 30s"
    return proc.returncode, proc.stdout, proc.stderr


def collect(cwd: str) -> RepoFacts:
    """Read the git state of `cwd`. Never raises; failures land in `.error`."""
    facts = RepoFacts()
    if not cwd:
        facts.error = "session recorded no working directory"
        return facts
    start = Path(cwd).expanduser()
    if not start.is_dir():
        facts.error = f"working directory does not exist: {start}"
        return facts

    rc, out, err = _git(["rev-parse", "--show-toplevel"], start)
    if rc != 0:
        facts.error = err.strip() or "not inside a git repository"
        return facts
    facts.is_repo = True
    facts.root = Path(out.strip()).resolve()

    rc, out, _ = _git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], facts.root)
    if rc == 0:
        for entry in out.split("\0"):
            if len(entry) < 4:
                continue
            code, rel = entry[:2], entry[3:]
            if code == "??":
                facts.untracked.add(rel)
            else:
                facts.status[rel] = code
                if "D" in code:
                    facts.deleted.add(rel)
    return facts
