"""Check a written description against what the diff actually contains.

The session audit asks "did the agent do what it said?". A pull request asks
the same question about the same kind of claim, and it is asked far more often:
every team using a coding agent merges text an agent wrote, describing changes
nobody re-reads.

Same mechanism, much larger surface. A description is just claims plus a diff to
check them against, and the diff is the strongest evidence available — better
than a session log, because it is the artifact being reviewed.

What it settles, mechanically:

    "adds tests/foo_test.py"        a path the diff adds
    "removes the legacy branch"      a hunk that deletes it
    "fixes the timeout to 30s"       a removed `timeout = 60` and an added `timeout = 30`
    "no behaviour change"            the diff touches only tests or docs

What it does not settle is intent, quality, or whether the change is right. A
description that passes here has been checked for consistency with the diff and
nothing more.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .claims import Claim, extract
from .groundtruth import RepoFacts
from .verify import CONTRADICTED, UNVERIFIED, VERIFIED, Finding

_TEST_PATH = ("test", "spec", "tests/", "spec/")
_DOC_PATH = ("readme", "docs/", "changelog", "license", ".md")


@dataclass
class DiffFacts:
    """What the diff actually says, which is stronger than any session log."""

    root: Path | None = None
    available: bool = False
    reason: str = ""
    added_paths: set[str] = field(default_factory=set)
    removed_paths: set[str] = field(default_factory=set)
    added_lines: set[str] = field(default_factory=set)
    removed_lines: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)


def _git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                              text=True, timeout=30)
    except FileNotFoundError:
        return 127, "", "git not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", "git timed out"
    return proc.returncode, proc.stdout, proc.stderr


def _parse_diff(raw: str, facts: DiffFacts) -> None:
    current = ""
    for line in raw.splitlines():
        if line.startswith("diff --git "):
            parts = line.split(" b/")
            current = parts[-1].strip() if len(parts) > 1 else ""
            if current:
                facts.files.add(current)
            facts.added_paths.discard(current)
            facts.removed_paths.discard(current)
            continue
        if line.startswith("new file mode") or line.startswith("--- /dev/null"):
            if current:
                facts.added_paths.add(current)
            continue
        if line.startswith("deleted file mode") or line.startswith("+++ /dev/null"):
            if current:
                facts.removed_paths.add(current)
            continue
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path and path != "/dev/null" and current and path not in facts.removed_paths:
                facts.added_paths.add(current)
            continue
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("+"):
            facts.added_lines.add(line[1:].strip())
        elif line.startswith("-"):
            facts.removed_lines.add(line[1:].strip())


def collect_diff(cwd: str, base: str | None = None) -> DiffFacts:
    """Read the pending diff: `git diff base...HEAD` when a base is given, else the working tree.

    Untracked files are handled explicitly. `git diff HEAD` does not include
    them, so a brand-new source file — the single most common thing a PR adds —
    would have looked absent, and alibi would have contradicted a description
    for saying it created it.
    """
    facts = DiffFacts()
    if not cwd:
        facts.reason = "no directory given"
        return facts
    start = Path(cwd).expanduser()
    if not start.is_dir():
        facts.reason = f"not a directory: {start}"
        return facts

    rc, root, err = _git(["rev-parse", "--show-toplevel"], start)
    if rc != 0:
        facts.reason = err.strip() or "not a git repository"
        return facts
    facts.root = Path(root.strip()).resolve()

    args = ["diff", "-U0", f"{base}...HEAD"] if base else ["diff", "-U0", "HEAD"]
    rc, out, err = _git(args, facts.root)
    if rc != 0:
        facts.reason = err.strip() or "git diff failed"
        return facts
    _parse_diff(out, facts)

    rc, untracked, _ = _git(["ls-files", "--others", "--exclude-standard"], facts.root)
    for rel in untracked.split("\n"):
        rel = rel.strip()
        if not rel:
            continue
        path = facts.root / rel
        if not path.is_file():
            continue
        facts.files.add(rel)
        facts.added_paths.add(rel)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        facts.added_lines.update(line.strip() for line in text.splitlines()[:400])

    facts.available = bool(facts.files or facts.added_lines or facts.removed_lines)
    if not facts.available:
        facts.reason = "the working tree has no changes to check against"
    return facts


def _rel(path: str) -> str:
    return path.lstrip("./")


def verify_description(text: str, diff: DiffFacts) -> list[Finding]:
    """Check every claim in a written description against the diff."""
    findings: list[Finding] = []
    for claim in extract(text):
        findings.append(_judge(claim, diff))
    return findings


def _judge(claim: Claim, diff: DiffFacts) -> Finding:
    if claim.kind == "soft":
        return Finding(claim, UNVERIFIED,
                       "the sentence names no path, literal or command, so the diff cannot settle it")

    if not diff.available:
        return Finding(claim, UNVERIFIED, diff.reason or "no diff available")

    if claim.kind == "file_created":
        name = _rel(claim.target)
        if any(_rel(p) == name or _rel(p).endswith("/" + name) for p in diff.added_paths):
            return Finding(claim, VERIFIED, f"the diff adds {name}")
        if any(_rel(p) == name for p in diff.files):
            return Finding(claim, CONTRADICTED,
                           f"the diff touches {name} but does not add it")
        return Finding(claim, CONTRADICTED, f"the diff does not add {name}")

    if claim.kind == "file_deleted":
        name = _rel(claim.target)
        if name in {_rel(p) for p in diff.removed_paths}:
            return Finding(claim, VERIFIED, f"the diff deletes {name}")
        if name in {_rel(p) for p in diff.files}:
            return Finding(claim, CONTRADICTED, f"the diff touches {name} but does not delete it")
        return Finding(claim, CONTRADICTED, f"the diff does not delete {name}")

    literal = claim.target.strip()
    if claim.kind == "string_added":
        if any(literal in line for line in diff.added_lines):
            return Finding(claim, VERIFIED, f"the diff adds a line containing {literal!r}")
        return Finding(claim, CONTRADICTED, f"no added line contains {literal!r}")

    if claim.kind == "string_removed":
        if any(literal in line for line in diff.removed_lines):
            return Finding(claim, VERIFIED, f"the diff removes a line containing {literal!r}")
        return Finding(claim, CONTRADICTED, f"no removed line contains {literal!r}")

    if claim.kind in ("tests_pass", "build_passes", "lint_clean"):
        # A description claiming tests pass is not about a command in the diff;
        # whether they passed is CI's business. Saying so beats a guess.
        return Finding(claim, UNVERIFIED,
                       "whether the suite passed is a CI fact, not something a diff can show")

    return Finding(claim, UNVERIFIED, f"no rule covers claim kind {claim.kind!r}")
