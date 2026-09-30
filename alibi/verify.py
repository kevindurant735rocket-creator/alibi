"""Verification: the only place a verdict is produced.

Three verdicts, and the third one is the point of this tool:

    VERIFIED      the working tree agrees with what the agent said
    CONTRADICTED  the working tree disagrees
    UNVERIFIED    alibi cannot settle it mechanically

The rule that governs every function below: **absence of evidence is never
evidence of presence.** A claim we cannot check is reported as UNVERIFIED, and
UNVERIFIED never silently becomes a pass. If a future change makes that
possible, this module is where it will happen, and it is meant to be obvious.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .claims import Claim
from .groundtruth import RepoFacts

VERIFIED = "VERIFIED"
CONTRADICTED = "CONTRADICTED"
UNVERIFIED = "UNVERIFIED"

# Shell-ish tools whose exit code tells us whether a command worked.
_COMMAND_TOOLS = {"Bash", "bash", "Shell", "shell", "run", "exec", "local_shell", "container.exec"}


@dataclass
class Finding:
    claim: Claim
    verdict: str
    evidence: str

    @property
    def contradicted(self) -> bool:
        return self.verdict == CONTRADICTED


def _shell_calls(session) -> list:
    calls = []
    for message in session.messages:
        for call in message.tool_calls:
            if call.name in _COMMAND_TOOLS:
                calls.append(call)
    return calls


def _command_of(call) -> str:
    cmd = call.tool_input.get("command") or call.tool_input.get("cmd") or ""
    return " ".join(str(cmd).split())


# What each kind of claim is allowed to be settled by. A "tests pass" claim is
# evidence about the test command, not about whatever else the session ran.
#
# The first version of this module treated ANY nonzero exit anywhere in the
# session as proof the tests failed. On 734 real sessions that produced 17
# CONTRADICTED verdicts and every single one was wrong — `ls foo || true` and
# `cat bar` returning nonzero has nothing to do with whether tests passed.
# Scoping the command match is what makes the verdict mean anything.
_SCOPED = {
    "tests_pass": (
        r"(?:pytest|py\.test|tox|nox|jest|vitest|mocha|rspec|cucumber|phpunit|ctest|"
        r"go\s+test|cargo\s+test|swift\s+test|npm\s+(?:run\s+)?test\b|yarn\s+test\b|"
        r"pnpm\s+(?:run\s+)?test\b|bun\s+test\b|make\s+(?:test|check)\b|ninja\s+test\b|"
        # an interpreter actually executing a test file: node x.test.js, python test_x.py
        r"(?:node|python3?|bash|sh|ruby|perl)\s+\S*tests?\S*\.(?:py|js|ts|tsx|mjs|cjs|sh|go|rb)\b"
        r"|\./\S*tests?\S*(?:\.sh|\.py)\b)",
        "test",
    ),
    "build_passes": (
        r"(?:make(?!\s+test)|cmake|ninja|cargo\s+build|go\s+build|tsc\b|"
        r"npm\s+run\s+build\b|yarn\s+build\b|pnpm\s+(?:run\s+)?build\b|xcodebuild|swift\s+build|"
        r"gradle|mvn|dotnet\s+build|webpack|vite\s+build|next\s+build)",
        "build",
    ),
    "lint_clean": (
        r"(?:ruff|flake8|pylint|mypy|pyright|eslint|prettier|tsc\s+--noEmit|"
        r"clippy|rubocop|golangci-lint|shellcheck|hadolint|biome|oxlint|"
        r"npm\s+run\s+lint\b|yarn\s+lint\b|pnpm\s+(?:run\s+)?lint\b|make\s+lint\b)",
        "lint",
    ),
}

# Commands that can look test-related but cannot execute anything. `grep -r
# tests/ .` reads files; it is not evidence that a test suite ran, and treating
# its exit status as such is what produced five of the six bogus verdicts in the
# first full run over 734 real sessions.
_READ_ONLY = re.compile(
    r"^\s*(?:\w+=\S+\s+)*(?:sudo\s+)?(?:grep|egrep|fgrep|rg|ack|cat|bat|head|tail|less|more|"
    r"ls|dir|tree|find|fd|stat|file|wc|echo|printf|awk|sed|jq|curl|wget|diff|which|whoami|"
    r"pwd|du|df|open)\b",
    re.I,
)

# The same verbs as bare leading words, for the `cd X && grep …` shape.
_READ_ONLY_VERB = re.compile(
    r"^(?:grep|egrep|fgrep|rg|ack|cat|bat|head|tail|less|more|ls|dir|tree|find|fd|stat|"
    r"file|wc|echo|printf|awk|sed|jq|curl|wget|diff|which|whoami|pwd|du|df|open|cd)$",
    re.I,
)


def _leading_verb(cmd: str) -> str:
    """First real word of a command, skipping `cd X &&`, env assignments and pipes."""
    cleaned = re.sub(r"(?:^|[;&|]\s*)\s*(cd\s+[^\s;&|]+\s*&&\s*)+", "", cleaned_cmd(cmd))
    cleaned = re.sub(r"^\s*(?:\w+=\S+\s+)*", "", cleaned)
    parts = re.split(r"[;&|]", cleaned, maxsplit=1)
    first = parts[0].strip().split()
    return first[0] if first else ""


def cleaned_cmd(cmd: str) -> str:
    """Reduce a command to the part that actually gets executed.

    Heredoc bodies and quoted payloads carry arbitrary text — including the
    word "tests" and whole .py filenames — and matching against them produced
    two more wrong verdicts in the second full run. Only the invocation counts
    as evidence, so everything from a quote or heredoc marker onward is cut.
    """
    first_line = cmd.split("\n", 1)[0]
    first_line = re.sub(r"<<-?\s*['\"]?\w+", " ", first_line)
    cut = re.search(r"['\"]", first_line)
    if cut:
        first_line = first_line[: cut.start()]
    return " ".join(first_line.split())


def _scoped_calls(session, kind: str) -> list:
    """Shell calls whose command actually belongs to the thing the claim names."""
    entry = _SCOPED.get(kind)
    if not entry:
        return []
    pattern = entry[0]
    try:
        rx = re.compile(pattern, re.I)
    except Exception:
        # A malformed pattern must not take the audit down; it just means this
        # kind cannot be scoped, and the caller falls back to UNVERIFIED.
        return []
    matched = []
    for call in _shell_calls(session):
        cmd = _command_of(call)
        if not cmd:
            continue
        invoked = cleaned_cmd(cmd)
        if not invoked or not rx.search(invoked):
            continue
        # A command that only reads files cannot have run a test suite, whatever
        # its arguments happen to be named. Checked at the head of the command
        # and again after any `cd X &&` so a chained grep is still caught.
        if _READ_ONLY.match(invoked) or _READ_ONLY_VERB.match(_leading_verb(cmd)):
            continue
        matched.append((call, invoked))
    return matched


def _verify_scoped_exit_claim(claim: Claim, facts: RepoFacts, session) -> Finding:
    """Settle a claim about a command's outcome, using only that command's exit code.

    No matching command at all means UNVERIFIED. It never means VERIFIED.
    """
    pattern, noun = _SCOPED.get(claim.kind, (None, claim.kind))
    matched = _scoped_calls(session, claim.kind)
    if not matched:
        return Finding(
            claim,
            UNVERIFIED,
            f"this session ran no {noun} command whose exit code is recorded, "
            f"so the claim cannot be checked here",
        )

    zero = [(c, cmd) for c, cmd in matched if c.exit_code == 0]
    nonzero = [(c, cmd) for c, cmd in matched if c.exit_code not in (None, 0)]
    unknown = [cmd for c, cmd in matched if c.exit_code is None]

    if nonzero and not zero:
        call, cmd = nonzero[0]
        return Finding(
            claim,
            CONTRADICTED,
            f"the {noun} command `{cmd[:110]}` exited {call.exit_code}, "
            f"and no {noun} command in this session exited 0",
        )
    if zero:
        call, cmd = zero[-1]
        extra = f" (later runs: {len(nonzero)} failed)" if nonzero else ""
        return Finding(claim, VERIFIED, f"`{cmd[:110]}` exited 0{extra}")
    return Finding(
        claim,
        UNVERIFIED,
        f"the only {noun} command(s) in this session have no recorded exit code: "
        f"{unknown[0][:90]}",
    )


def _resolve(facts: RepoFacts, target: str) -> tuple[str | None, str]:
    """Turn a claimed path into an absolute one inside the repo, or explain why not."""
    if not target:
        return None, "claim names no path"
    p = facts.abs(target)
    if p is None:
        if facts.root is None:
            return None, facts.error or "no git repository to check against"
        return None, f"path {target!r} resolves outside the repository"
    return p, ""


def _verify_file_created(claim: Claim, facts: RepoFacts, session) -> Finding:
    path, why = _resolve(facts, claim.target)
    if path is None:
        return Finding(claim, UNVERIFIED, why)
    if path.exists():
        rel = path.relative_to(facts.root) if facts.root else path
        marker = ""
        if str(rel) in facts.untracked or str(rel) in facts.status:
            marker = " and it is in the working tree diff"
        return Finding(claim, VERIFIED, f"{rel} exists{marker}")
    return Finding(claim, CONTRADICTED, f"{claim.target} does not exist in the working tree")


def _verify_file_deleted(claim: Claim, facts: RepoFacts, session) -> Finding:
    path, why = _resolve(facts, claim.target)
    if path is None:
        return Finding(claim, UNVERIFIED, why)
    if not path.exists():
        return Finding(claim, VERIFIED, f"{claim.target} is gone")
    return Finding(
        claim,
        CONTRADICTED,
        f"{claim.target} still exists ({len(path.read_text(errors='replace').splitlines())} lines)",
    )


def _which_file(session, hint: str) -> str | None:
    """Guess which file a literal-string claim is about, from the paths the agent touched."""
    touched = []
    for message in session.messages:
        for call in message.tool_calls:
            for key in ("file_path", "path", "notebook_path", "filePath"):
                value = call.tool_input.get(key)
                if isinstance(value, str) and value:
                    touched.append(value)
    if not touched:
        return None
    tail = hint.lower()
    for path in touched:
        if tail and tail in path.lower():
            return path
    return touched[-1]


def _verify_string(claim: Claim, facts: RepoFacts, session, want_present: bool) -> Finding:
    literal = claim.target
    candidates = []
    hint = _which_file(session, literal)
    if hint:
        candidates.append(hint)
    candidates.extend(sorted(facts.status) + sorted(facts.untracked))

    searched = []
    for rel in candidates:
        p, _ = _resolve(facts, rel)
        if p is None or not p.is_file():
            continue
        content = facts.read(rel)
        if content is None:
            continue
        searched.append(rel)
        present = literal in content
        if present == want_present:
            verb = "is present in" if want_present else "is gone from"
            return Finding(claim, VERIFIED, f"{literal!r} {verb} {rel}")
    if not searched:
        return Finding(
            claim,
            UNVERIFIED,
            f"no readable file to check {literal!r} against; the session named no file path",
        )
    where = ", ".join(searched[:3]) + ("…" if len(searched) > 3 else "")
    state = "nowhere in" if want_present else "still present in"
    return Finding(claim, CONTRADICTED, f"{literal!r} was not found {state} {where}")


def verify(claim: Claim, facts: RepoFacts, session) -> Finding:
    """Judge one claim. This is the only function that returns a verdict."""
    if claim.kind == "soft":
        return Finding(
            claim,
            UNVERIFIED,
            "the claim names no path, literal or command, so there is nothing mechanical to check",
        )
    if not facts.is_repo:
        return Finding(claim, UNVERIFIED, facts.error or "no git repository at the session's cwd")

    if claim.kind == "file_created":
        return _verify_file_created(claim, facts, session)
    if claim.kind == "file_deleted":
        return _verify_file_deleted(claim, facts, session)
    if claim.kind == "string_added":
        return _verify_string(claim, facts, session, want_present=True)
    if claim.kind == "string_removed":
        return _verify_string(claim, facts, session, want_present=False)
    if claim.kind in _SCOPED:
        return _verify_scoped_exit_claim(claim, facts, session)
    return Finding(claim, UNVERIFIED, f"no rule covers claim kind {claim.kind!r}")


def verify_all(claims, facts: RepoFacts, session) -> list[Finding]:
    return [verify(c, facts, session) for c in claims]


def tally(findings: list[Finding]) -> dict[str, int]:
    counts = {VERIFIED: 0, CONTRADICTED: 0, UNVERIFIED: 0}
    for f in findings:
        counts[f.verdict] = counts.get(f.verdict, 0) + 1
    return counts
