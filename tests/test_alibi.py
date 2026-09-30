"""alibi's own tests.

The two properties that matter are tested against real files in a real git
repository, not mocks:

  1. a claim the tree contradicts is reported CONTRADICTED and exits nonzero
  2. a claim alibi cannot settle is reported UNVERIFIED and never VERIFIED

Run with:  python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alibi.agents import available, load, locate_all, parse_session
from alibi.claims import extract
from alibi.groundtruth import collect
from alibi.verify import CONTRADICTED, UNVERIFIED, VERIFIED, verify, verify_all


def run(*args, cwd):
    return subprocess.run(
        args, cwd=str(cwd), capture_output=True, text=True, check=False
    )


def write_transcript(path: Path, cwd: str, texts: list[str], commands: list[tuple[str, str, int]]) -> None:
    """Write a Claude Code shaped transcript: assistant turns with tool calls."""
    records = []
    for text in texts:
        records.append({
            "type": "assistant", "cwd": cwd, "gitBranch": "main",
            "timestamp": "2026-09-30T10:00:00.000Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        })
    for cmd, out, code in commands:
        use_id = f"tool_{abs(hash(cmd))}"
        records.append({
            "type": "assistant", "cwd": cwd, "gitBranch": "main",
            "timestamp": "2026-09-30T10:00:01.000Z",
            "message": {"role": "assistant", "content": [{
                "type": "tool_use", "id": use_id, "name": "Bash", "input": {"command": cmd},
            }]},
        })
        records.append({
            "type": "user", "cwd": cwd, "gitBranch": "main",
            "timestamp": "2026-09-30T10:00:02.000Z",
            "message": {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": use_id,
                "content": f"{out}\nExit code: {code}", "is_error": code != 0,
            }]},
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        run("git", "init", "-q", cwd=self.repo)
        run("git", "config", "user.email", "t@example.com", cwd=self.repo)
        run("git", "config", "user.name", "t", cwd=self.repo)
        (self.repo / "existing.py").write_text("import os\nprint('hi')\n")
        run("git", "add", "-A", cwd=self.repo)
        run("git", "commit", "-qm", "init", cwd=self.repo)

    def tearDown(self):
        self.tmp.cleanup()


class TestGroundTruth(Sandbox):
    def test_finds_repo_root_and_status(self):
        facts = collect(str(self.repo))
        self.assertTrue(facts.is_repo, facts.error)
        self.assertEqual(facts.root, self.repo.resolve())

    def test_refuses_paths_outside_the_repo(self):
        facts = collect(str(self.repo))
        self.assertIsNone(facts.abs("../../../etc/passwd"))

    def test_reports_missing_directory_instead_of_raising(self):
        facts = collect(str(self.repo / "nope"))
        self.assertFalse(facts.is_repo)
        self.assertIn("does not exist", facts.error)

    def test_reports_non_repo_instead_of_raising(self):
        outside = Path(self.tmp.name) / "plain"
        outside.mkdir()
        facts = collect(str(outside))
        self.assertFalse(facts.is_repo)
        self.assertIn("git repository", facts.error)


class TestClaimExtraction(unittest.TestCase):
    def test_finds_a_created_file_claim(self):
        claims = extract("I created `src/app.py` and wired it up.")
        self.assertEqual([c.kind for c in claims], ["file_created"])
        self.assertEqual(claims[0].target, "src/app.py")

    def test_finds_tests_pass_claim(self):
        kinds = [c.kind for c in extract("All 12 unit tests pass now.")]
        self.assertIn("tests_pass", kinds)

    def test_intent_is_not_a_claim(self):
        # "Now let me write X" is a plan. Treating it as a completed action is
        # the fastest way to make a verifier lie.
        self.assertEqual(extract("Now let me write the new `verify_all.sh` script."), [])
        self.assertEqual(extract("I'll add tests/test_thing.py next."), [])

    def test_bare_done_is_reported_not_dropped(self):
        claims = extract("已完成。")
        self.assertEqual([c.kind for c in claims], ["soft"])

    def test_soft_claims_are_marked_not_decidable(self):
        self.assertFalse(extract("I refactored this for clarity.")[0].decidable)


class TestVerification(Sandbox):
    def _session(self, texts, commands=()):
        td = Path(self.tmp.name) / "transcripts"
        tf = td / "s.jsonl"
        write_transcript(tf, str(self.repo), list(texts), list(commands))
        return parse_session("claude_code", tf)

    def _check(self, texts, commands=()):
        session = self._session(texts, commands)
        facts = collect(session.cwd)
        return verify_all(extract(" ".join(texts)), facts, session)

    def test_true_claim_about_an_existing_file_is_verified(self):
        findings = self._check(["I created `existing.py`."])
        self.assertEqual([f.verdict for f in findings], [VERIFIED])

    def test_claim_about_a_file_that_does_not_exist_is_contradicted(self):
        findings = self._check(["I created `ghost.py`."])
        self.assertEqual([f.verdict for f in findings], [CONTRADICTED])

    def test_claim_of_removal_of_a_live_file_is_contradicted(self):
        findings = self._check(["I removed `existing.py`."])
        self.assertEqual([f.verdict for f in findings], [CONTRADICTED])

    def test_tests_pass_is_contradicted_by_a_failing_test_command(self):
        findings = self._check(
            ["All 12 tests pass."],
            [("node tests/auth.test.js", "AssertionError", 1)],
        )
        self.assertEqual([f.verdict for f in findings], [CONTRADICTED])

    def test_tests_pass_is_verified_by_a_passing_test_command(self):
        findings = self._check(
            ["All 12 tests pass."],
            [("python3 -m pytest -q", "12 passed", 0)],
        )
        self.assertEqual([f.verdict for f in findings], [VERIFIED])

    def test_unrelated_failing_command_does_not_contradict_a_tests_claim(self):
        # The bug that produced 17 wrong verdicts on the first real run:
        # `ls foo` exiting nonzero says nothing about the test suite.
        findings = self._check(
            ["All 12 tests pass."],
            [("python3 -m pytest -q", "12 passed", 0), ("ls missing_dir", "no such file", 2)],
        )
        self.assertEqual([f.verdict for f in findings], [VERIFIED])

    def test_reading_a_tests_directory_does_not_count_as_running_tests(self):
        findings = self._check(
            ["All 12 tests pass."],
            [("grep -rn 'tests' .", "matches", 0)],
        )
        self.assertEqual([f.verdict for f in findings], [UNVERIFIED])

    def test_a_claim_with_nothing_to_check_is_unverified_not_verified(self):
        findings = self._check(["I refactored this for clarity."])
        self.assertEqual([f.verdict for f in findings], [UNVERIFIED])

    def test_no_git_repository_makes_everything_unverified(self):
        outside = Path(self.tmp.name) / "plain"
        outside.mkdir()
        session = self._session(["I created `x.py`."])
        session.cwd = str(outside)
        facts = collect(session.cwd)
        findings = verify_all(extract("I created `x.py`."), facts, session)
        self.assertEqual([f.verdict for f in findings], [UNVERIFIED])


class TestAdapterContract(unittest.TestCase):
    def test_both_agents_ship_and_expose_the_contract(self):
        ids = available()
        for expected in ("claude_code", "codex"):
            self.assertIn(expected, ids)
            mod = load(expected)
            for attr in ("NAME", "SUMMARY", "locate", "parse"):
                self.assertTrue(hasattr(mod, attr), f"{expected}.{attr} missing")

    def test_locate_never_raises_on_a_bad_path(self):
        for agent_id, _ in locate_all() or [("claude_code", [])]:
            mod = load(agent_id)
            self.assertEqual(mod.locate("/definitely/not/here"), [])

    def test_parse_returns_a_session_with_a_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "s.jsonl"
            write_transcript(tf, td, ["I created `x.py`."], [])
            session = parse_session("claude_code", tf)
            self.assertEqual(session.cwd, td)
            self.assertEqual(len(session.assistant_messages), 1)


class TestExitCodes(Sandbox):
    """The exit code is the contract CI depends on."""

    def _run_cli(self, *extra):
        return subprocess.run(
            [sys.executable, "-m", "alibi", "scan", *extra],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True, text=True, check=False,
        )

    def test_exit_0_when_nothing_is_contradicted(self):
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "s.jsonl"
            write_transcript(tf, str(self.repo), ["I created `existing.py`."], [])
            proc = self._run_cli("--transcript", str(tf), "--color", "never")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("VERIFIED", proc.stdout)

    def test_exit_1_when_a_claim_is_contradicted(self):
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "s.jsonl"
            write_transcript(tf, str(self.repo), ["I created `ghost.py`."], [])
            proc = self._run_cli("--transcript", str(tf), "--color", "never")
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("CONTRADICTED", proc.stdout)

    def test_exit_2_when_there_is_nothing_to_audit(self):
        proc = self._run_cli("--transcript", "/definitely/not/here.jsonl")
        self.assertEqual(proc.returncode, 2)

    def test_json_output_is_parseable(self):
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "s.jsonl"
            write_transcript(tf, str(self.repo), ["I created `ghost.py`."], [])
            proc = self._run_cli("--transcript", str(tf), "--json", "--no-fail")
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["summary"]["CONTRADICTED"], 1)
            self.assertEqual(payload["summary"]["VERIFIED"], 0)

    def test_receipt_is_markdown_and_names_the_contradiction(self):
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "s.jsonl"
            write_transcript(tf, str(self.repo), ["I created `ghost.py`."], [])
            proc = self._run_cli("--transcript", str(tf), "--receipt")
            self.assertIn("<!-- alibi: begin -->", proc.stdout)
            self.assertIn("Contradicted", proc.stdout)
            self.assertIn("ghost.py", proc.stdout)

    def test_unverified_never_counts_as_verified_in_the_summary(self):
        with tempfile.TemporaryDirectory() as td:
            tf = Path(td) / "s.jsonl"
            write_transcript(tf, str(self.repo), ["I refactored this for clarity."], [])
            proc = self._run_cli("--transcript", str(tf), "--json", "--no-fail")
            summary = json.loads(proc.stdout)["summary"]
            self.assertEqual(summary["VERIFIED"], 0)
            self.assertEqual(summary["UNVERIFIED"], 1)


class TestNoThirdPartyImports(unittest.TestCase):
    def test_source_uses_only_the_standard_library(self):
        allowed = {
            "argparse", "dataclasses", "datetime", "importlib", "json", "os",
            "pathlib", "pkgutil", "re", "shutil", "subprocess", "sys",
            "tempfile", "unittest", "typing", "hashlib", "__future__",
        }
        root = Path(__file__).resolve().parents[1] / "alibi"
        for path in root.rglob("*.py"):
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line.startswith("import ") and not line.startswith("from "):
                    continue
                if line.startswith("from ."):
                    continue
                module = line.split()[1].split(".")[0]
                self.assertIn(
                    module, allowed, f"{path.name} imports non-stdlib module {module!r}"
                )


if __name__ == "__main__":
    unittest.main()
