"""Rendering: terminal table, machine JSON, and a receipt you can paste in a PR.

The receipt is the artifact alibi is meant to leave behind. It is deliberately
plain markdown — no HTML, no badges — so it survives being pasted into a pull
request description, a review comment or a chat message.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone

from .verify import CONTRADICTED, UNVERIFIED, VERIFIED, Finding, tally

_MARK = {VERIFIED: "✓", CONTRADICTED: "✗", UNVERIFIED: "?"}
_COLOR = {VERIFIED: "\033[32m", CONTRADICTED: "\033[31m", UNVERIFIED: "\033[33m"}
_RESET = "\033[0m"
_DIM = "\033[2m"


def _use_color(force: str) -> bool:
    if force == "always":
        return True
    if force == "never":
        return False
    return shutil.get_terminal_size().columns > 0 and __import__("sys").stdout.isatty()


def _truncate(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


def render_terminal(sessions: list, results: dict, color: str = "auto", width: int = 0) -> str:
    """One block per session, then a total line."""
    use_color = _use_color(color)
    total_width = width or min(shutil.get_terminal_size().columns, 110)
    out: list[str] = []

    for session in sessions:
        findings: list[Finding] = results[session.path]["findings"]
        counts = tally(findings)
        rel = _short(session.path)
        out.append("")
        if session.skipped_lines:
            out.append(f"  {_DIM if use_color else ''}warning: skipped "
                       f"{session.skipped_lines} unparseable line(s) in this transcript{_RESET if use_color else ''}")
        out.append(f"  {_truncate(rel, total_width - 20)}")
        out.append(f"  {_DIM if use_color else ''}agent={session.agent}  cwd={_truncate(session.cwd or '?', 44)}{_RESET if use_color else ''}")

        if not findings:
            out.append("  (no completion claims found in this session)")
            continue

        claim_w = max(24, min(52, total_width - 44))
        for i, finding in enumerate(findings, 1):
            mark = _MARK[finding.verdict]
            head = f"  {i:>3}. {finding.verdict:<12} {finding.claim.kind:<14}"
            if use_color:
                head = f"  {i:>3}. {_COLOR[finding.verdict]}{finding.verdict:<12}{_RESET} {finding.claim.kind:<14}"
            out.append(head)
            out.append(f"       said     {_truncate(finding.claim.text, claim_w)}")
            out.append(f"       tree     {_truncate(finding.evidence, claim_w + 16)}")

        summary = (
            f"  {_MARK[VERIFIED]} {counts[VERIFIED]} verified   "
            f"{_MARK[CONTRADICTED]} {counts[CONTRADICTED]} contradicted   "
            f"{_MARK[UNVERIFIED]} {counts[UNVERIFIED]} unverified"
        )
        if use_color:
            summary += (
                f"   {_DIM}(alibi judges the diff, not the intent){_RESET}"
            )
        else:
            summary += "   (alibi judges the diff, not the intent)"
        out.append(summary)

    all_findings = [f for r in results.values() for f in r["findings"]]
    counts = tally(all_findings)
    out.append("")
    out.append(f"  {len(all_findings)} claims across {len(sessions)} sessions: "
               f"{counts[VERIFIED]} verified, {counts[CONTRADICTED]} contradicted, "
               f"{counts[UNVERIFIED]} unverified")
    return "\n".join(out)


def _short(path) -> str:
    try:
        return str(path).replace(str(__import__("pathlib").Path.home()), "~")
    except Exception:
        return str(path)


def render_json(sessions: list, results: dict, version: str = "0.1.0") -> str:
    payload = {
        "tool": "alibi",
        "version": version,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {
            "sessions": len(sessions),
            "claims": sum(len(r["findings"]) for r in results.values()),
            **tally([f for r in results.values() for f in r["findings"]]),
        },
        "sessions": [
            {
                "agent": session.agent,
                "transcript": str(session.path),
                "cwd": session.cwd,
                "started_at": session.started_at,
                "ended_at": session.ended_at,
                "repository": results[session.path].get("repo"),
                "repository_error": results[session.path].get("repo_error", ""),
                "unparsed_lines": session.skipped_lines,
                "claims": [
                    {
                        "kind": f.claim.kind,
                        "claim": f.claim.text,
                        "target": f.claim.target,
                        "verdict": f.verdict,
                        "evidence": f.evidence,
                    }
                    for f in results[session.path]["findings"]
                ],
            }
            for session in sessions
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def render_receipt(sessions: list, results: dict, limit_per_session: int = 20) -> str:
    """Markdown block meant to be pasted under a pull request description."""
    all_findings = [f for r in results.values() for f in r["findings"]]
    counts = tally(all_findings)
    lines: list[str] = []
    lines.append("<!-- alibi: begin -->")
    lines.append("")
    lines.append("### Claim check")
    lines.append("")
    unparsed = sum(s.skipped_lines for s in sessions)
    lines.append(f"`alibi` read {len(sessions)} agent session(s) and checked "
                 f"{len(all_findings)} completion claim(s) against the working tree.")
    if unparsed:
        lines.append("")
        lines.append(f"> **{unparsed} line(s) in these transcripts could not be parsed "
                     f"and were skipped.**")
    lines.append("")
    lines.append("| | count |")
    lines.append("|---|---|")
    lines.append(f"| verified | {counts[VERIFIED]} |")
    lines.append(f"| contradicted | {counts[CONTRADICTED]} |")
    lines.append(f"| unverified | {counts[UNVERIFIED]} |")
    lines.append("")

    contradicted = [f for f in all_findings if f.verdict == CONTRADICTED]
    if contradicted:
        lines.append("**Contradicted — the tree does not say what the agent said.**")
        lines.append("")
        for f in contradicted[:limit_per_session]:
            lines.append(f"- `{f.claim.kind}` — agent said: _{_md(f.claim.text)}_")
            lines.append(f"  - tree: {f.evidence}")
        lines.append("")

    unverified = [f for f in all_findings if f.verdict == UNVERIFIED]
    if unverified:
        lines.append("**Unverified — alibi could not settle these mechanically.** They are not passes.")
        lines.append("")
        for f in unverified[:limit_per_session]:
            lines.append(f"- `{f.claim.kind}` — _{_md(f.claim.text)}_ — {f.evidence}")
        lines.append("")

    if not contradicted:
        lines.append("No claim was contradicted. Unverified claims above were not counted as passes.")
        lines.append("")

    lines.append("<sub>alibi checks what changed on disk, not what the agent meant. "
                 "Claims it cannot decide are reported, never assumed.</sub>")
    lines.append("")
    lines.append("<!-- alibi: end -->")
    return "\n".join(lines)


def _md(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")
