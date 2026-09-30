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
    """A snapshot of the working tree a session ran in.

    git is the strongest ground truth available, but it is not the only one. A
    session that ran in a home directory or a scratch folder has no repository,
    and 124 of 258 claims on real transcripts were in exactly that situation.
    Refusing to look there would mean refusing to work where a lot of agent
    work actually happens.

    So a directory without git is still usable: existence and mtime are weaker
    evidence than a diff, but they are evidence. What changes is which verdicts
    are reachable, not whether the tool shows up at all.
    """

    root: Path | None = None
    is_repo: bool = False
    status: dict[str, str] = field(default_factory=dict)  # path -> porcelain status
    untracked: set[str] = field(default_factory=set)
    deleted: set[str] = field(default_factory=set)
    error: str = ""
    # The directory the session ran in, whether or not it is a repository.
    base: Path | None = None

    def abs(self, rel: str) -> Path | None:
        """Resolve a path the agent mentioned, refusing to escape the base dir.

        A claim about `../../etc/passwd` is not evidence about this directory,
        so it resolves to None and the caller must report UNVERIFIED rather than
        reading a file the agent had no business touching.
        """
        base = self.root or self.base
        if base is None:
            return None
        if not rel or "\x00" in rel:
            return None
        # A leading ~ is a home reference, not a path inside the directory.
        # Left alone it would silently become a literal directory named "~".
        if rel.startswith("~"):
            return None
        try:
            candidate = (base / rel).resolve()
            candidate.relative_to(base.resolve())
        except (ValueError, OSError):
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

    def changed_lines(self, rel: str) -> tuple[set[str], set[str]] | None:
        """(added, removed) line contents from `git diff -U0` for one path.

        This is the only honest way to settle a "I added/removed the line X"
        claim. Checking whether X is present in the file now is the mirror of
        the file_created bug: "removed the line TOKEN_LIMIT" came back VERIFIED
        because TOKEN_LIMIT happened not to be in the one file the session
        touched, which says nothing about whether the agent removed it.

        Returns None when git cannot speak — not a repo, untracked path, or the
        change has already been committed — and the caller must then say
        UNVERIFIED rather than guess.
        """
        if self.root is None:
            return None
        p = self.abs(rel)
        if p is None:
            return None
        rc, out, _ = _git(["diff", "-U0", "--", str(p)], self.root)
        if rc != 0 or not out.strip():
            return None
        added: set[str] = set()
        removed: set[str] = set()
        for line in out.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                added.add(line[1:].strip())
            elif line.startswith("-"):
                removed.add(line[1:].strip())
        return (added, removed)


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
    """Read the state of `cwd`. Never raises; failures land in `.error`."""
    facts = RepoFacts()
    if not cwd:
        facts.error = "session recorded no working directory"
        return facts
    start = Path(cwd).expanduser()
    if not start.is_dir():
        facts.error = f"working directory does not exist: {start}"
        return facts

    facts.base = start.resolve()

    rc, out, err = _git(["rev-parse", "--show-toplevel"], start)
    if rc != 0:
        # No repository here. Not a dead end — a directory is still a place
        # files live, and the session's own write records are still evidence.
        facts.error = "no git repository here; falling back to filesystem evidence"
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
