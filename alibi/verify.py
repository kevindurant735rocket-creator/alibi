"""Verification: the only place a verdict is produced.

Three verdicts, and the third one is the point of this tool:

    VERIFIED      the session and the working tree agree with what was said
    CONTRADICTED  they disagree
    UNVERIFIED    alibi cannot settle it mechanically

The rule that governs every function below: **absence of evidence is never
evidence of presence.** A claim we cannot check is reported as UNVERIFIED, and
UNVERIFIED never silently becomes a pass. If a future change makes that
possible, this module is where it will happen, and it is meant to be obvious.

The second rule, learned the hard way: **a coincidence is not evidence.** The
first version asked "does utils.py exist?" and marked "I created utils.py"
VERIFIED even when that file had been committed long before the session and the
agent had never opened it. A file claim is settled by what THIS SESSION wrote
(see writes.py), never by what happens to be on disk.
"""

from __future__ import annotations

import collections
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .claims import Claim
from .groundtruth import RepoFacts
from .writes import resolve_claimed, session_writes

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

# Only something that can actually execute a suite counts as evidence about it.
# `docker cp /tmp/ws_full_test.py robocup_sim:/tmp/` copies a file whose name
# contains "test"; it runs nothing. Two of the first fifteen contradictions were
# this — a copy command reported as a failed test run.
_NOT_A_RUNNER = re.compile(
    r"^(?:docker|podman|kubectl|helm|npm\s+run\s+(?:docs|serve|start|dev)|"
    r"pip|pip3|brew|apt|apt-get|yum|dnf|git|gh|scp|rsync|tar|unzip|zip|"
    r"mkdir|rmdir|ln|mv|cp|chmod|chown|kill|pkill|systemctl|ssh|scp)$",
    re.I,
)

# When a session was working inside a container, a file the agent says it wrote
# may live in that container rather than on this filesystem. Absence here stops
# being evidence of anything.
_CONTAINER_HINT = re.compile(r"\b(?:docker|podman|kubectl|k8s|container|image|"
                             r"compose|dockerfile|exit\s+\d+\s+from)\b", re.I)


def _used_containers(session) -> bool:
    for message in session.messages:
        for call in message.tool_calls:
            blob = " ".join(str(v) for v in call.tool_input.values())
            if _CONTAINER_HINT.search(blob):
                return True
    return False

# A chained or piped command without pipefail reports the shell's status, not the
# runner's. `pytest || true` and `pytest 2>&1 | tail -30` both exit 0 no matter
# what pytest did, and agents write both constantly.
_MASKED = re.compile(r"\|\||\||;|&\s*$|set\s+\+e")
_PIPEFAIL = re.compile(r"set\s+-o\s+pipefail|set\s+-C")


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
        # Nor can a command that only moves files around.
        if _NOT_A_RUNNER.match(_leading_verb(cmd)):
            continue
        matched.append((call, invoked))
    return matched


def _verify_scoped_exit_claim(claim: Claim, facts: RepoFacts, session, ambiguous: bool = False) -> Finding:
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
    if ambiguous:
        # A session can say "all 5 sequential tests pass" and later "the
        # browser-like test passed" while running two different scripts. With
        # more than one of each, alibi cannot tell which run the sentence was
        # about, and handing one command's result to the other sentence is the
        # false accusation this tool exists to avoid.
        return Finding(
            claim,
            UNVERIFIED,
            f"this session makes several {noun} claims and ran several {noun} commands, "
            f"so alibi cannot tell which run this sentence was about",
        )

    # A shell that absorbed the failure reports its own status, not the runner's.
    # `pytest || true` and `pytest 2>&1 | tail -30` both exit 0 whatever pytest
    # did, and those are two of the shapes agents write most often. Without
    # pipefail the exit code is simply not evidence about the test suite.
    masked = [
        (c, cmd) for c, cmd in matched
        if _MASKED.search(cmd) and not _PIPEFAIL.search(cmd)
    ]
    if masked:
        call, cmd = masked[-1]
        return Finding(
            claim,
            UNVERIFIED,
            f"`{cmd[:100]}` is piped or chained without pipefail, so its exit "
            f"{call.exit_code} is the shell's, not the {noun}'s",
        )

    # "All tests pass" describes the end state — but of the run that claim is
    # about, not of every run in the session. Choosing the session's last test
    # command handed one test's result to a claim about a different test, so the
    # command nearest the claim wins: the last one before it, else the first
    # one after it.
    if claim.at:
        before = [(c, cmd) for c, cmd in matched if c.at <= claim.at]
        after = [(c, cmd) for c, cmd in matched if c.at > claim.at]
        chosen = before[-1] if before else (after[0] if after else matched[-1])
    else:
        chosen = matched[-1]
    call, cmd = chosen

    if call.exit_code is None:
        # No exit code in the output. The agent's own structured flag is a
        # better source than anything scraped from text, but only when the
        # command is not chained — otherwise it describes the chain.
        if call.is_error is True:
            return Finding(claim, CONTRADICTED, f"the {noun} command nearest that claim, `{cmd[:100]}`, was recorded as an error")
        if call.is_error is False:
            return Finding(claim, VERIFIED, f"the {noun} command nearest that claim, `{cmd[:100]}`, succeeded (no exit code recorded)")
        return Finding(
            claim,
            UNVERIFIED,
            f"the {noun} command nearest that claim, `{cmd[:100]}`, has neither an exit code nor a status flag",
        )

    if call.exit_code == 0:
        earlier = " (an earlier run in this session failed)" if any(
            c.exit_code not in (None, 0) for c, _ in matched[:-1]
        ) else ""
        return Finding(claim, VERIFIED, f"the {noun} command nearest that claim, `{cmd[:100]}`, exited 0{earlier}")
    return Finding(
        claim,
        CONTRADICTED,
        f"the {noun} command nearest that claim, `{cmd[:100]}`, exited {call.exit_code}",
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


def _exists_elsewhere(facts: RepoFacts, claimed: str, depth_limit: int = 4) -> str | None:
    """Is there a file with this name somewhere else under the session's directory?

    An agent that says "I added run_all.sh" while its cwd is a phase directory's
    parent may well mean the one three levels down. Its absence from the working
    directory is then not evidence of anything, and reporting it as a
    contradiction would accuse a session of not doing something it plainly did.
    """
    base = facts.root or facts.base
    if base is None or not claimed:
        return None
    name = os.path.basename(claimed.lstrip("./"))
    if not name or "." not in name:
        return None
    base_depth = len(base.parts)
    try:
        for root, dirs, files in os.walk(base):
            if len(Path(root).parts) - base_depth >= depth_limit:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__")]
            if name in files:
                return os.path.relpath(os.path.join(root, name), base)
    except OSError:
        return None
    return None


def _verify_file_created(claim: Claim, facts: RepoFacts, session) -> Finding:
    # Existence is checked first, on the best path available. A claim naming a
    # file that is simply not there is a contradiction whether or not the
    # session happened to write something by that name.
    resolved, how = resolve_claimed(session, claim.target)
    check = facts.abs(resolved) if resolved else facts.abs(claim.target)
    if check is None:
        return Finding(claim, UNVERIFIED, f"{claim.target} does not resolve inside the session's directory")

    if not check.exists():
        if _used_containers(session):
            # A session working in a container says it wrote files that live in
            # that container. Their absence here says nothing about whether it
            # did — three of the first contradictions were exactly this.
            return Finding(
                claim,
                UNVERIFIED,
                f"{claim.target} is not on this filesystem, and this session worked with "
                f"containers, so alibi cannot tell whether it was written inside one",
            )
        # "I created `phase16-ux-desktop/app.py` in the true tree" — a path with
        # a directory prefix whose directory does not exist under the session's
        # own directory is a claim about a different tree entirely. Reporting it
        # as a contradiction would accuse a session of not doing something it
        # plainly did, somewhere else.
        prefix = os.path.dirname(claim.target.lstrip("./"))
        # `resolve()` happily returns a path that does not exist, so the test
        # is existence, not resolvability — the first cut checked the wrong one
        # and the guard never fired.
        prefix_dir = facts.abs(prefix) if prefix else None
        if prefix and (prefix_dir is None or not prefix_dir.is_dir()):
            return Finding(
                claim,
                UNVERIFIED,
                f"the directory `{prefix}` does not exist under the session's directory, so "
                f"{claim.target} refers to some other tree alibi cannot see",
            )
        elsewhere = _exists_elsewhere(facts, claim.target)
        if elsewhere:
            return Finding(
                claim,
                UNVERIFIED,
                f"{claim.target} is not in the session's working directory, but a file with that "
                f"name exists at {elsewhere}; the agent may be working from a subdirectory",
            )
        return Finding(claim, CONTRADICTED, f"{claim.target} does not exist in the session's directory")

    if resolved:
        marker = ""
        if facts.is_repo and (resolved in facts.untracked or resolved in facts.status):
            marker = ", and it shows up in the working tree diff"
        elif not facts.is_repo:
            marker = " (filesystem evidence only: not a git repository)"
        return Finding(claim, VERIFIED, f"this session wrote {resolved} {how}{marker}, and the file is there")

    # It exists, but this session never wrote it. The file may predate the
    # session entirely, and "it exists" is not evidence that this session put
    # it there.
    return Finding(
        claim,
        UNVERIFIED,
        f"{claim.target} exists, but this session contains no write to it, so alibi "
        f"cannot tell whether this session created it or found it already there",
    )


def _verify_file_deleted(claim: Claim, facts: RepoFacts, session) -> Finding:
    resolved, how = resolve_claimed(session, claim.target)
    check = facts.abs(resolved) if resolved else facts.abs(claim.target)
    if check is None:
        return Finding(claim, UNVERIFIED, f"{claim.target} does not resolve inside the session's directory")
    if not check.exists():
        return Finding(claim, VERIFIED, f"{claim.target} is gone")

    # Describe the path without reading it. It may be a directory, and the
    # uncaught IsADirectoryError used to exit 1 — the exact code alibi uses for
    # "a claim was contradicted". A crash must never look like an accusation.
    kind = "directory" if check.is_dir() else "file"
    suffix = f" (matched to {resolved} {how})" if resolved else ""
    return Finding(claim, CONTRADICTED, f"{claim.target}{suffix} still exists as a {kind}")


def _which_file(session, hint: str) -> str | None:
    """Best guess at which file a literal-string claim is about.

    Deliberately advisory. A guess that produces a verdict is how alibi accused
    an honest agent: it really did delete TOKEN_LIMIT from parser.py, alibi
    guessed an unrelated notes.log, and the truthful claim came back
    CONTRADICTED. A guess only decides where to look — never what to conclude.
    """
    touched = []
    for message in session.messages:
        for call in message.tool_calls:
            for key in ("file_path", "path", "notebook_path", "filePath"):
                value = call.tool_input.get(key)
                if isinstance(value, str) and value:
                    touched.append(value)
    tail = (hint or "").lower()
    if not touched or not tail:
        return None
    # Substring matching put "return", "self" and "test" into unrelated files.
    # Require the literal to sit on a word boundary inside the file name.
    rx = re.compile(r"(?:^|[^a-z0-9])" + re.escape(tail) + r"(?:[^a-z0-9]|$)", re.I)
    for path in touched:
        if rx.search(Path(path).name):
            return path
    return None


def _verify_string(claim: Claim, facts: RepoFacts, session, want_present: bool) -> Finding:
    """Was a literal line added or removed, according to git?

    The question "is the literal in the file now" is not evidence in either
    direction. For an added line it is satisfied by a file that always had it;
    for a removed line it is satisfied by any file that never had it. Both
    produced a VERIFIED that meant nothing. `git diff -U0` says which lines
    actually changed, so that is what this asks.
    """
    literal = claim.target.strip()
    writes = session_writes(session)

    candidates = list(writes)
    hint = _which_file(session, literal)
    if hint:
        candidates.insert(0, hint.lstrip("./"))

    saw_diff = False
    for rel in candidates:
        rel = rel.lstrip("./")
        if facts.abs(rel) is None:
            continue
        changed = facts.changed_lines(rel)
        if not changed:
            continue
        added, removed = changed
        saw_diff = True
        if want_present and literal in added:
            return Finding(claim, VERIFIED, f"git diff shows `{literal}` added to {rel}")
        if (not want_present) and literal in removed:
            return Finding(claim, VERIFIED, f"git diff shows `{literal}` removed from {rel}")

    if saw_diff:
        verb = "added" if want_present else "removed"
        return Finding(
            claim,
            CONTRADICTED,
            f"git diff shows no `{literal}` line being {verb} in any file this session wrote",
        )
    return Finding(
        claim,
        UNVERIFIED,
        f"no uncommitted diff for a file this session wrote mentions {literal!r}, "
        f"so there is nothing to settle it against",
    )


def verify(claim: Claim, facts: RepoFacts, session, ambiguous_kinds: set | None = None) -> Finding:
    """Judge one claim. This is the only function that returns a verdict."""
    if claim.kind == "soft":
        return Finding(
            claim,
            UNVERIFIED,
            "the claim names no path, literal or command, so there is nothing mechanical to check",
        )
    if claim.kind == "file_created":
        return _verify_file_created(claim, facts, session)
    if claim.kind == "file_deleted":
        return _verify_file_deleted(claim, facts, session)
    if claim.kind == "string_added":
        return _verify_string(claim, facts, session, want_present=True)
    if claim.kind == "string_removed":
        return _verify_string(claim, facts, session, want_present=False)
    if claim.kind in _SCOPED:
        return _verify_scoped_exit_claim(
            claim, facts, session, bool(ambiguous_kinds and claim.kind in ambiguous_kinds)
        )
    return Finding(claim, UNVERIFIED, f"no rule covers claim kind {claim.kind!r}")


def _ambiguous_kinds(claims, session) -> set:
    """Command-scoped kinds where the sentence cannot be tied to a run.

    The signal is two claims landing on the same command. A session that says
    "all 5 sequential tests pass" and "the browser-like test passed" while
    running one script is describing two different things, and handing that one
    script's exit code to both sentences would be a guess dressed as a verdict.
    """
    kinds = collections.Counter(c.kind for c in claims if c.kind in _SCOPED)
    out = set()
    for kind, how_many in kinds.items():
        if how_many < 2:
            continue
        matched = _scoped_calls(session, kind)
        if not matched:
            continue
        # Pair every claim of this kind with the command nearest to it, then ask
        # whether any command ended up claimed by more than one sentence.
        pairing = collections.Counter()
        for c in claims:
            if c.kind != kind:
                continue
            if c.at:
                before = [x for x in matched if x[0].at <= c.at]
                after = [x for x in matched if x[0].at > c.at]
                chosen = (before[-1] if before else (after[0] if after else matched[-1]))
            else:
                chosen = matched[-1]
            pairing[chosen[0].at] += 1
        if any(v > 1 for v in pairing.values()):
            out.add(kind)
    return out


def verify_all(claims, facts: RepoFacts, session) -> list[Finding]:
    """Verify every claim. One bad claim must not take down the batch.

    A crash used to propagate to main(), which exited 1 — the code alibi uses
    for "a claim was contradicted". In CI that turns a bug into a false
    accusation, which is the one failure mode this tool must never have.
    """
    findings = []
    ambiguous = _ambiguous_kinds(claims, session)
    for claim in claims:
        try:
            findings.append(verify(claim, facts, session, ambiguous))
        except Exception as exc:
            findings.append(Finding(
                claim, UNVERIFIED,
                f"alibi could not check this claim ({type(exc).__name__}: {exc})",
            ))
    return findings


def tally(findings: list[Finding]) -> dict[str, int]:
    counts = {VERIFIED: 0, CONTRADICTED: 0, UNVERIFIED: 0}
    for f in findings:
        counts[f.verdict] = counts.get(f.verdict, 0) + 1
    return counts
