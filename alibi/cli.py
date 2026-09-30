"""alibi — check what an AI coding agent said it did against what actually changed.

    alibi scan            audit recent sessions and print a table
    alibi scan --json     the same audit, machine readable, for CI
    alibi scan --receipt  a markdown block to paste into a pull request
    alibi doctor          which agents alibi can see transcripts for

Exit codes are the contract:
    0  no claim was contradicted
    1  at least one claim was contradicted
    2  alibi could not run (bad arguments, no transcripts, not a git repo)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .agents import Message, Session, ToolCall, available, locate_all, parse_session
from .claims import extract_session
from .groundtruth import collect
from .report import render_json, render_receipt, render_terminal
from .verify import CONTRADICTED, tally, verify_all

EXIT_OK = 0
EXIT_CONTRADICTED = 1
EXIT_CANNOT_RUN = 2


def _load(args) -> tuple[list[Session], dict]:
    """Locate transcripts, parse them, snapshot each session's working tree."""
    wanted = args.agent or None
    located = locate_all(wanted, args.transcript)
    if not located:
        return [], {}

    sessions: list[Session] = []
    for agent_id, paths in located:
        for path in paths:
            if args.limit and len(sessions) >= args.limit:
                break
            session = parse_session(agent_id, path)
            if session is None or not session.messages:
                continue
            sessions.append(session)

    results: dict = {}
    # Many sessions share a working directory. Snapshot each repository once —
    # a scan over a few hundred sessions otherwise spawns git twice per session
    # and the audit takes long enough that people stop running it.
    facts_cache: dict[str, object] = {}
    for session in sessions:
        key = session.cwd or ""
        if key not in facts_cache:
            facts_cache[key] = collect(session.cwd)
        facts = facts_cache[key]
        results[session.path] = {
            "repo": str(facts.root) if facts.root else None,
            "repo_error": facts.error,
            "findings": verify_all(extract_session(session), facts, session),
        }
    return sessions, results


def cmd_scan(args) -> int:
    sessions, results = _load(args)
    if not sessions:
        print("alibi: no agent sessions found.", file=sys.stderr)
        print("       Run `alibi doctor` to see where alibi looked.", file=sys.stderr)
        return EXIT_CANNOT_RUN

    if args.json:
        print(render_json(sessions, results, __version__))
    elif args.receipt:
        print(render_receipt(sessions, results))
    else:
        print(render_terminal(sessions, results, color=args.color, width=args.width))

    contradicted = sum(
        1 for r in results.values() for f in r["findings"] if f.verdict == CONTRADICTED
    )
    return EXIT_CONTRADICTED if (contradicted and not args.no_fail) else EXIT_OK


def cmd_doctor(args) -> int:
    print("alibi doctor — which agents can alibi read right now\n")
    located = dict(locate_all(None, None))
    known = available()
    for agent_id in known or ["(none found)"]:
        paths = located.get(agent_id, [])
        print(f"  {agent_id:<14} {'OK' if paths else 'no transcripts'}   {len(paths)} file(s)")
    if not known:
        print("  no adapters loaded — is alibi/agents/ intact?", file=sys.stderr)
        return EXIT_CANNOT_RUN
    print("\n  Adding an agent: drop one file in alibi/agents/ defining")
    print("  NAME, SUMMARY, locate() and parse(). Nothing else changes.")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alibi",
        description="Check what an AI coding agent said it did against what actually changed.",
    )
    parser.add_argument("--version", action="version", version=f"alibi {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="audit agent sessions against the working tree")
    scan.add_argument("--agent", action="append", help="only this agent (repeatable)")
    scan.add_argument("--transcript", help="audit this transcript file or directory instead of the default locations")
    scan.add_argument("--limit", type=int, default=20, help="max sessions to audit (default 20)")
    scan.add_argument("--json", action="store_true", help="machine readable output for CI")
    scan.add_argument("--receipt", action="store_true", help="markdown block to paste into a pull request")
    scan.add_argument("--no-fail", action="store_true", help="always exit 0, even with contradictions")
    scan.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    scan.add_argument("--width", type=int, default=0)
    scan.set_defaults(func=cmd_scan)

    doctor = sub.add_parser("doctor", help="show which agents alibi can read")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
