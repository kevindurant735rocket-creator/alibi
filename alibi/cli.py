"""alibi — check what an AI coding agent said it did against what actually changed.

    alibi scan            audit recent sessions and print a table
    alibi scan --json     the same audit, machine readable, for CI
    alibi scan --receipt  a markdown block to paste into a pull request
    alibi doctor          which agents alibi can see transcripts for

Exit codes are the contract:
    0  alibi ran and nothing was contradicted
    1  at least one claim was contradicted
    2  alibi could not run — bad arguments, no transcripts, or it crashed

Note what is NOT in the contract: a crash exits 2, never 1. Exit 1 means alibi
looked at something and disagreed with the agent, and that must never be
something a bug can produce.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .agents import Message, Session, ToolCall, available, locate_all, parse_session
from .claims import extract_session
from .desccheck import collect_diff, verify_description
from .report_desc import render_description_json, render_description_receipt, render_description_terminal
from .groundtruth import collect
from .report import render_json, render_receipt, render_terminal
from .verify import CONTRADICTED, tally, verify_all

def _say(text: str = "", file=None) -> None:
    """Print something whose exit code must not depend on the console's code page.

    stdout and stderr are encoded differently on Windows, so the fallback looks
    up whichever stream it is actually writing to. Replacing a glyph is a fair
    trade; turning a caught contradiction into "could not run" is not.
    """
    stream = file or sys.stdout
    try:
        print(text, file=file)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "ascii"
        stream.write(text.encode(enc, errors="replace").decode(enc, errors="replace") + "\n")


EXIT_OK = 0
EXIT_CONTRADICTED = 1
EXIT_CANNOT_RUN = 2


def _load(args) -> tuple[list[Session], dict, int]:
    """Locate transcripts, parse them, snapshot each session's working tree.

    Returns (sessions, results, unreadable). `unreadable` counts transcripts
    that were located but could not be used. Dropping them silently is how an
    agent that changes its transcript format turns alibi into a rubber stamp
    that reports "nothing contradicted" forever.
    """
    wanted = args.agent or None
    located = locate_all(wanted, args.transcript)
    if not located:
        return [], {}, 0

    sessions: list[Session] = []
    # A path counts as unreadable only if NO adapter could read it. With an
    # explicit --transcript every adapter is offered the same file, and the ones
    # that do not speak its format would otherwise each report it as broken.
    parsed_paths: set = set()
    attempted: set = set()
    for agent_id, paths in located:
        for path in paths:
            # `--limit 0` means "no limit". Testing `if args.limit` treated 0 as
            # falsy and silently audited every transcript instead.
            if args.limit > 0 and len(sessions) >= args.limit:
                break
            attempted.add(path)
            session = parse_session(agent_id, path)
            if session is None or not session.messages:
                continue
            parsed_paths.add(path)
            sessions.append(session)

    unreadable = len(attempted - parsed_paths)

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
    return sessions, results, unreadable


def cmd_scan(args) -> int:
    sessions, results, unreadable = _load(args)
    if not sessions:
        print("alibi: no agent sessions found.", file=sys.stderr)
        print("       Run `alibi doctor` to see where alibi looked.", file=sys.stderr)
        return EXIT_CANNOT_RUN

    if args.json:
        _say(render_json(sessions, results, __version__, unreadable))
    elif args.receipt:
        _say(render_receipt(sessions, results, unreadable=unreadable))
    else:
        _say(render_terminal(sessions, results, color=args.color, width=args.width,
                              unreadable=unreadable))

    contradicted = sum(
        1 for r in results.values() for f in r["findings"] if f.verdict == CONTRADICTED
    )
    # A scan that could read nothing is not a clean scan. `--strict` makes that
    # loud, because the default has to stay usable across a machine where most
    # sessions are not in a repository at all.
    if args.strict and unreadable:
        print(f"alibi: {unreadable} transcript(s) could not be read", file=sys.stderr)
        return EXIT_CANNOT_RUN
    return EXIT_CONTRADICTED if (contradicted and not args.no_fail) else EXIT_OK


def cmd_check(args) -> int:
    """Check a written description — a PR body, a commit message — against the diff."""
    if args.file == "-":
        text = sys.stdin.read()
    else:
        path = Path(args.file)
        if not path.is_file():
            print(f"alibi: no such file: {path}", file=sys.stderr)
            return EXIT_CANNOT_RUN
        text = path.read_text(encoding="utf-8", errors="replace")

    diff = collect_diff(str(Path(args.repo or ".").resolve()), args.base, args.head)
    findings = verify_description(text, diff)

    if args.json:
        _say(render_description_json(text, findings, diff))
    elif args.receipt:
        _say(render_description_receipt(findings, diff))
    else:
        _say(render_description_terminal(text, findings, diff, args.color))

    contradicted = sum(1 for f in findings if f.verdict == CONTRADICTED)
    if args.strict and not diff.available:
        print(f"alibi: {diff.reason}", file=sys.stderr)
        return EXIT_CANNOT_RUN
    return EXIT_CONTRADICTED if (contradicted and not args.no_fail) else EXIT_OK


def cmd_doctor(args) -> int:
    _say("alibi doctor — which agents can alibi read right now\n")
    located = dict(locate_all(None, None))
    known = available()

    # An adapter that fails to import was previously dropped from the listing
    # entirely, so a broken adapter was indistinguishable from one that was never
    # written. A missing capability should be visible, not silent.
    from . import agents as pkg

    broken = []
    for module_name in pkg._module_names():
        mod = pkg._try_import(module_name)
        if mod is None:
            broken.append((module_name, "import failed"))
        elif not all(hasattr(mod, a) for a in ("NAME", "SUMMARY", "locate", "parse")):
            missing = [a for a in ("NAME", "SUMMARY", "locate", "parse") if not hasattr(mod, a)]
            broken.append((module_name, "missing " + ", ".join(missing)))

    for agent_id in known or (["(none found)"] if not broken else []):
        paths = located.get(agent_id, [])
        _say(f"  {agent_id:<14} {'OK' if paths else 'no transcripts':<14}   {len(paths)} file(s)")
    for module_name, why in broken:
        _say(f"  {module_name:<14} BROKEN         {why}", file=sys.stderr)

    if not known:
        print("  no working adapters loaded — is alibi/agents/ intact?", file=sys.stderr)
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
    scan.add_argument("--limit", type=int, default=20,
                      help="max sessions to audit (default 20; 0 means no limit)")
    scan.add_argument("--json", action="store_true", help="machine readable output for CI")
    scan.add_argument("--receipt", action="store_true", help="markdown block to paste into a pull request")
    scan.add_argument("--no-fail", action="store_true", help="always exit 0, even with contradictions")
    scan.add_argument("--strict", action="store_true",
                      help="exit 2 if any located transcript could not be read")
    scan.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    scan.add_argument("--width", type=int, default=0)
    scan.set_defaults(func=cmd_scan)

    check = sub.add_parser(
        "check",
        help="check a PR description or commit message against the actual diff",
    )
    check.add_argument("file", help="path to the description, or - for stdin")
    check.add_argument("--repo", help="repository to compare against (default: cwd)")
    check.add_argument("--base", help="git ref to diff against, e.g. main or a SHA (default: uncommitted changes)")
    check.add_argument("--head", help="the other end of the comparison, e.g. a SHA (default: the working tree with --base)")
    check.add_argument("--json", action="store_true")
    check.add_argument("--receipt", action="store_true")
    check.add_argument("--no-fail", action="store_true")
    check.add_argument("--strict", action="store_true", help="exit 2 when there is no diff to check")
    check.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    check.set_defaults(func=cmd_check)

    doctor = sub.add_parser("doctor", help="show which agents alibi can read")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # A crash must never come out as 1. Exit 1 means alibi checked something
        # and disagreed with the agent; a traceback means alibi broke, and in CI
        # those two look identical if the code is shared.
        print(f"alibi: crashed ({type(exc).__name__}: {exc})", file=sys.stderr)
        return EXIT_CANNOT_RUN


if __name__ == "__main__":
    raise SystemExit(main())
