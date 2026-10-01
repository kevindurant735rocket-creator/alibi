"""Rendering for `alibi check` — a description checked against a diff.

Same three verdicts as the session audit, because the promise is the same: a
sentence the tool cannot settle is reported, never counted as agreeing.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from .verify import CONTRADICTED, UNVERIFIED, VERIFIED, Finding, tally

_MARK = {VERIFIED: "✓", CONTRADICTED: "✗", UNVERIFIED: "?"}


def _safe_mark(verdict: str) -> str:
    """The verdict glyph, or an ASCII stand-in when the console cannot encode it."""
    mark = _MARK[verdict]
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        mark.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return {"VERIFIED": "ok", "CONTRADICTED": "XX", "UNVERIFIED": "??"}[verdict]
    return mark


def _safe_print(text: str = "") -> None:
    """Print, surviving a console that cannot encode what we want to show.

    The verdict marks are U+2713 and U+2717. On a Windows console with the
    default code page, printing one raises UnicodeEncodeError — and a
    UnicodeEncodeError inside cmd_scan propagates to main(), which exits 2. That
    is a crash wearing the exit code that means "could not run", on the one
    platform where a wrong exit code silently turns a caught contradiction into a
    green build. Losing a glyph is a fair trade for a correct exit code.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))

_COLOR = {VERIFIED: "\033[32m", CONTRADICTED: "\033[31m", UNVERIFIED: "\033[33m"}
_RESET = "\033[0m"
_DIM = "\033[2m"


def _color_on(force: str) -> bool:
    return True if force == "always" else (False if force == "never" else sys.stdout.isatty())


def _truncate(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: max(0, width - 1)] + "..."


def _headline(diff, color: bool) -> list[str]:
    if not diff.available:
        return [f"  {_DIM if color else ''}nothing to check: {diff.reason}{_RESET if color else ''}", ""]
    scope = f"comparing {diff.base} to {diff.head}" if diff.base else "uncommitted changes"
    return [
        f"  diff: {len(diff.files)} file(s), {len(diff.added_lines)} line(s) added, "
        f"{len(diff.removed_lines)} removed  {scope}"
    ]


def render_description_terminal(text: str, findings: list[Finding], diff, color: str = "auto") -> str:
    on = _color_on(color)
    width = 96
    out: list[str] = [""]
    out += _headline(diff, on)
    out.append("")

    if not findings:
        out.append("  (this description makes no claim alibi can check)")
    for i, f in enumerate(findings, 1):
        verdict = f"{_COLOR[f.verdict]}{f.verdict:<12}{_RESET}" if on else f"{f.verdict:<12}"
        out.append(f"  {i:>3}. {verdict} {f.claim.kind}")
        out.append(f"       said     {_truncate(f.claim.text, width - 14)}")
        out.append(f"       diff     {_truncate(f.evidence, width - 14)}")

    counts = tally(findings)
    out.append("")
    out.append(
        f"  {_safe_mark(VERIFIED)} {counts[VERIFIED]} verified   "
        f"{_safe_mark(CONTRADICTED)} {counts[CONTRADICTED]} contradicted   "
        f"{_safe_mark(UNVERIFIED)} {counts[UNVERIFIED]} unverified"
    )
    return "\n".join(out)


def render_description_json(text: str, findings: list[Finding], diff) -> str:
    return json.dumps({
        "tool": "alibi",
        "mode": "check",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "diff": {
            "available": diff.available,
            "reason": diff.reason,
            "files": sorted(diff.files),
            "added_paths": sorted(diff.added_paths),
            "removed_paths": sorted(diff.removed_paths),
        },
        "summary": {"claims": len(findings), **tally(findings)},
        "claims": [
            {"kind": f.claim.kind, "claim": f.claim.text, "target": f.claim.target,
             "verdict": f.verdict, "evidence": f.evidence}
            for f in findings
        ],
    }, indent=2, ensure_ascii=False)


def render_description_receipt(findings: list[Finding], diff) -> str:
    counts = tally(findings)
    lines = ["<!-- alibi: begin -->", "", "### Claim check", ""]
    if not diff.available:
        lines.append(f"_alibi had nothing to check this against: {diff.reason}_")
        lines.append("")
        lines.append("<!-- alibi: end -->")
        return "\n".join(lines)

    lines.append(f"`alibi` compared this description against "
                 f"{len(diff.files)} changed file(s) in the diff.")
    lines.append("")
    lines.append("| | count |")
    lines.append("|---|---|")
    lines.append(f"| verified | {counts[VERIFIED]} |")
    lines.append(f"| contradicted | {counts[CONTRADICTED]} |")
    lines.append(f"| unverified | {counts[UNVERIFIED]} |")
    lines.append("")

    bad = [f for f in findings if f.verdict == CONTRADICTED]
    if bad:
        lines.append("**Contradicted — the diff does not say what this description says.**")
        lines.append("")
        for f in bad:
            lines.append(f"- `{f.claim.kind}` — _{_md(f.claim.text)}_")
            lines.append(f"  - {f.evidence}")
        lines.append("")
    else:
        lines.append("Nothing in this description was contradicted by the diff.")
        lines.append("")

    unknown = [f for f in findings if f.verdict == UNVERIFIED]
    if unknown:
        lines.append("<sub>Unverified sentences are not passes.</sub>")
        lines.append("")

    lines.append("<sub>alibi checks this description against the diff. It does not "
                 "judge whether the change is any good.</sub>")
    lines.append("")
    lines.append("<!-- alibi: end -->")
    return "\n".join(lines)


def _md(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _safe_mark(verdict: str) -> str:
    """The verdict glyph, or an ASCII stand-in when the console cannot encode it."""
    mark = _MARK[verdict]
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        mark.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return {"VERIFIED": "ok", "CONTRADICTED": "XX", "UNVERIFIED": "??"}[verdict]
    return mark


def _safe_print(text: str = "") -> None:
    """Print, surviving a console that cannot encode what we want to show.

    The verdict marks are U+2713 and U+2717. On a Windows console with the
    default code page, printing one raises UnicodeEncodeError — and a
    UnicodeEncodeError inside cmd_scan propagates to main(), which exits 2. That
    is a crash wearing the exit code that means "could not run", on the one
    platform where a wrong exit code silently turns a caught contradiction into a
    green build. Losing a glyph is a fair trade for a correct exit code.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))
