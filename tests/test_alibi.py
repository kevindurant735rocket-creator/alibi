"""alibi's own tests.

The properties that matter are tested against real files in a real git
repository, not mocks:

  1. a claim the session and the tree contradict is CONTRADICTED, exit 1
  2. a claim alibi cannot settle is UNVERIFIED and never VERIFIED
  3. a claim that happens to be true by coincidence is still UNVERIFIED

That third one exists because the first version of this tool got it wrong in
the worst direction: "I created utils.py" came back VERIFIED whenever utils.py
existed, even when the file predated the session by years and the agent had
never opened it. Several tests below are regression tests for that class of
error specifically.

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
from alibi.verify import CONTRADICTED, UNVERIFIED, VERIFIED, verify_all
from alibi.writes import session_writes

REPO_ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd):
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=False)


def write_transcript(path, cwd, texts=(), commands=(), writes=()):
    """Write a Claude Code shaped transcript.

    `commands` are (command, output, exit_code); `writes` are (tool, path).
    Most file tests need a write, because alibi now asks what the session did
    rather than what happens to be on disk.
    """
    records = []
    for text in texts:
        records.append({
            "type": "assistant", "cwd": cwd, "gitBranch": "main",
            "timestamp": "2026-09-30T10:00:00.000Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        })
    for tool_name, file_path in writes:
        records.append({
            "type": "assistant", "cwd": cwd, "gitBranch": "main",
            "timestamp": "2026-09-30T10:00:01.000Z",
            "message": {"role": "assistant", "content": [{
                "type": "tool_use", "id": f"w_{file_path}", "name": tool_name,
                "input": {"file_path": file_path, "content": "x = 1\n"},
            }]},
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
    return path


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

    def session(self, texts=(), commands=(), writes=(), cwd=None):
        tf = Path(self.tmp.name) / "transcripts" / "s.jsonl"
        write_transcript(tf, str(cwd or self.repo), list(texts), list(commands), list(writes))
        return parse_session("claude_code", tf)

    def check(self, texts, commands=(), writes=()):
        session = self.session(texts, commands, writes)
        facts = collect(session.cwd)
        return verify_all(extract(" ".join(texts)), facts, session)


class TestGroundTruth(Sandbox):
    def test_finds_repo_root_and_status(self):
        facts = collect(str(self.repo))
        self.assertTrue(facts.is_repo, facts.error)
        self.assertEqual(facts.root, self.repo.resolve())

    def test_reports_a_directory_as_such(self):
        facts = collect(str(self.repo))
        self.assertTrue(facts.abs("existing.py").is_file())

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


class TestPathContainment(Sandbox):
    """A malicious transcript must not make alibi read outside the repository."""

    def _refused(self, target):
        return collect(str(self.repo)).abs(target) is None

    def test_refuses_parent_traversal(self):
        self.assertTrue(self._refused("../../../etc/passwd"))

    def test_refuses_absolute_paths_outside_the_repo(self):
        self.assertTrue(self._refused("/etc/passwd"))

    def test_refuses_a_home_reference_rather_than_treating_it_as_a_directory(self):
        # Left alone, "~" becomes a literal directory named "~" inside the repo,
        # which does not actually escape — but accepting a home reference at all
        # invites confusion, so it is refused outright.
        self.assertTrue(self._refused("~/secret.txt"))

    def test_refuses_a_symlink_pointing_outside_the_repo(self):
        secret = Path(self.tmp.name) / "secret.txt"
        secret.write_text("hunter2")
        (self.repo / "link.txt").symlink_to(secret)
        self.assertTrue(self._refused("link.txt"))

    def test_percent_encoded_traversal_cannot_escape(self):
        # Decoding this would be the dangerous thing to do. Left undecoded it is
        # a harmless directory name; all that matters is that it stays inside.
        facts = collect(str(self.repo))
        resolved = facts.abs("%2e%2e%2f%2e%2e%2fetc%2fpasswd")
        self.assertTrue(resolved is None or resolved.is_relative_to(facts.root))

    def test_refuses_an_embedded_null_byte(self):
        self.assertTrue(self._refused("a\x00b/c.py"))

    def test_refuses_an_empty_path(self):
        self.assertTrue(self._refused(""))

    def test_allows_a_path_inside_the_repo(self):
        facts = collect(str(self.repo))
        self.assertIsNotNone(facts.abs("existing.py"))


class TestClaimExtraction(unittest.TestCase):
    def test_finds_a_created_file_claim(self):
        claims = extract("I created `src/app.py` and wired it up.")
        self.assertEqual([c.kind for c in claims], ["file_created"])
        self.assertEqual(claims[0].target, "src/app.py")

    def test_finds_tests_pass_claim(self):
        self.assertIn("tests_pass", [c.kind for c in extract("All 12 unit tests pass now.")])

    def test_intent_is_not_a_claim(self):
        self.assertEqual(extract("Now let me write the new `verify_all.sh` script."), [])
        self.assertEqual(extract("I'll add tests/test_thing.py next."), [])

    def test_bare_done_is_reported_not_dropped(self):
        self.assertEqual([c.kind for c in extract("已完成。")], ["soft"])

    def test_soft_claims_are_marked_not_decidable(self):
        self.assertFalse(extract("I refactored this for clarity.")[0].decidable)

    def test_a_quoted_filename_is_a_file_claim_not_a_line_claim(self):
        # "removed `existing.py`" means the file is gone, not that the literal
        # string "existing.py" was removed from some source file.
        self.assertEqual([c.kind for c in extract("I removed `a/b.py`.")], ["file_deleted"])


class TestFileClaims(Sandbox):
    """The false-VERIFIED regression: existence is not evidence."""

    def test_file_that_predates_the_session_is_unverified_not_verified(self):
        findings = self.check(["I created `existing.py`."])
        self.assertEqual([f.verdict for f in findings], [UNVERIFIED])
        self.assertIn("no write to it", findings[0].evidence)

    def test_claim_about_a_file_that_does_not_exist_is_contradicted(self):
        findings = self.check(["I created `ghost.py`."])
        self.assertEqual([f.verdict for f in findings], [CONTRADICTED])

    def test_created_and_actually_written_is_verified(self):
        (self.repo / "made.py").write_text("x = 1\n")
        findings = self.check(
            ["I created `made.py`."], writes=[("Write", str(self.repo / "made.py"))]
        )
        self.assertEqual([f.verdict for f in findings], [VERIFIED])
        self.assertIn("this session wrote", findings[0].evidence)

    def test_heredoc_write_counts_as_writing_the_file(self):
        findings = self.check(
            ["I created `made.py`."],
            commands=[("cat > made.py <<'EOF'\nx = 1\nEOF", "", 0)],
        )
        (self.repo / "made.py").write_text("x = 1\n")
        findings = self.check(
            ["I created `made.py`."], commands=[("cat > made.py <<'EOF'\nx = 1\nEOF", "", 0)]
        )
        self.assertEqual([f.verdict for f in findings], [VERIFIED])

    def test_tee_write_counts_as_writing_the_file(self):
        (self.repo / "tee.py").write_text("x = 1\n")
        findings = self.check(
            ["I created `tee.py`."], commands=[("echo x | tee tee.py", "", 0)]
        )
        self.assertEqual([f.verdict for f in findings], [VERIFIED])

    def test_dev_null_redirect_is_not_a_write(self):
        findings = self.check(
            ["I created `existing.py`."], commands=[("pytest > /dev/null 2>&1", "", 0)]
        )
        self.assertEqual([f.verdict for f in findings], [UNVERIFIED])

    def test_claim_of_removal_of_a_live_file_is_contradicted(self):
        findings = self.check(["I removed `existing.py`."])
        self.assertEqual([f.verdict for f in findings], [CONTRADICTED])

    def test_removal_of_a_file_that_is_gone_is_verified(self):
        findings = self.check(["I removed `ghost.py`."])
        self.assertEqual([f.verdict for f in findings], [VERIFIED])

    def test_a_directory_path_does_not_crash_the_run(self):
        (self.repo / "weird.py").mkdir()
        findings = self.check(["I removed `weird.py`."])
        self.assertEqual([f.verdict for f in findings], [CONTRADICTED])
        self.assertIn("directory", findings[0].evidence)


class TestExitCodeClaims(Sandbox):
    def test_passing_test_command_verifies(self):
        findings = self.check(["All 12 tests pass."], commands=[("python3 -m pytest -q", "12 passed", 0)])
        self.assertEqual([f.verdict for f in findings], [VERIFIED])

    def test_failing_test_command_contradicts(self):
        findings = self.check(["All 12 tests pass."], commands=[("node tests/auth.test.js", "boom", 1)])
        self.assertEqual([f.verdict for f in findings], [CONTRADICTED])

    def test_last_run_wins_not_any_green_run(self):
        # Green first, red later. The claim describes the end state, so the
        # verdict must be CONTRADICTED — the first version said VERIFIED and
        # printed "(later runs: 1 failed)" in the same evidence string.
        findings = self.check(
            ["All tests pass."],
            commands=[("pytest tests/unit", "ok", 0), ("pytest tests/full", "FAILED", 1)],
        )
        self.assertEqual([f.verdict for f in findings], [CONTRADICTED])
        self.assertIn("the last", findings[0].evidence)

    def test_red_first_green_last_verifies(self):
        findings = self.check(
            ["All tests pass."],
            commands=[("pytest tests/full", "FAILED", 1), ("pytest tests/full", "ok", 0)],
        )
        self.assertEqual([f.verdict for f in findings], [VERIFIED])

    def test_or_true_masks_the_failure_and_is_unverified(self):
        findings = self.check(["All tests pass."], commands=[("pytest || true", "", 0)])
        self.assertEqual([f.verdict for f in findings], [UNVERIFIED])
        self.assertIn("shell's", findings[0].evidence)

    def test_a_pipeline_is_unverified_because_the_code_is_the_shells(self):
        findings = self.check(["All tests pass."], commands=[("pytest 2>&1 | tail -30", "", 0)])
        self.assertEqual([f.verdict for f in findings], [UNVERIFIED])

    def test_pipefail_makes_the_pipeline_usable_again(self):
        findings = self.check(
            ["All tests pass."],
            commands=[("set -o pipefail; pytest 2>&1 | tail -30", "ok", 0)],
        )
        self.assertEqual([f.verdict for f in findings], [VERIFIED])

    def test_unrelated_failing_command_does_not_contradict_a_tests_claim(self):
        findings = self.check(
            ["All 12 tests pass."],
            commands=[("python3 -m pytest -q", "12 passed", 0), ("ls missing_dir", "no such file", 2)],
        )
        self.assertEqual([f.verdict for f in findings], [VERIFIED])

    def test_reading_a_tests_directory_is_not_running_tests(self):
        findings = self.check(["All 12 tests pass."], commands=[("grep -rn 'tests' .", "matches", 0)])
        self.assertEqual([f.verdict for f in findings], [UNVERIFIED])

    def test_no_command_at_all_is_unverified(self):
        findings = self.check(["All 12 tests pass."])
        self.assertEqual([f.verdict for f in findings], [UNVERIFIED])

    def test_build_and_lint_claims_are_scoped_separately(self):
        findings = self.check(
            ["The build succeeds and lint is clean."],
            commands=[("make build", "ok", 0)],
        )
        kinds = {f.claim.kind: f.verdict for f in findings}
        self.assertEqual(kinds.get("build_passes"), VERIFIED)
        self.assertEqual(kinds.get("lint_clean"), UNVERIFIED)


class TestStringClaims(Sandbox):
    """Line claims are settled by git diff, not by what the file looks like now."""

    def _tracked(self, name, content, then=None):
        """Commit `content`, then optionally change it, so a real diff exists."""
        p = self.repo / name
        p.write_text(content)
        run("git", "add", "-A", cwd=self.repo)
        run("git", "commit", "-qm", "add " + name, cwd=self.repo)
        if then is not None:
            p.write_text(then)
        return p

    def test_added_literal_shown_as_a_diff_addition_is_verified(self):
        self._tracked("conf.py", "timeout = 10\n", then="timeout = 10\ntimeout = 30\n")
        findings = self.check(
            ["I added the line `timeout = 30`."],
            commands=[("cat > conf.py <<'EOF'\ntimeout = 30\nEOF", "", 0)],
        )
        self.assertEqual([f.verdict for f in findings], [VERIFIED])
        self.assertIn("git diff", findings[0].evidence)

    def test_removed_literal_shown_as_a_diff_removal_is_verified(self):
        self._tracked("conf.py", "TOKEN_LIMIT = 10\nkeep = 1\n", then="keep = 1\n")
        findings = self.check(
            ["I removed the line `TOKEN_LIMIT = 10`."],
            commands=[("cat > conf.py <<'EOF'\nkeep = 1\nEOF", "", 0)],
        )
        self.assertEqual([f.verdict for f in findings], [VERIFIED])

    def test_a_removed_literal_the_diff_does_not_mention_is_contradicted(self):
        self._tracked("conf.py", "TOKEN_LIMIT = 10\nkeep = 1\n", then="TOKEN_LIMIT = 10\nkeep = 2\n")
        findings = self.check(
            ["I removed the line `TOKEN_LIMIT = 10`."],
            commands=[("cat > conf.py <<'EOF'\nTOKEN_LIMIT = 10\nkeep = 2\nEOF", "", 0)],
        )
        self.assertEqual([f.verdict for f in findings], [CONTRADICTED])

    def test_an_untracked_file_gives_no_diff_and_says_so(self):
        # git cannot speak about a file it has never seen, so the honest answer
        # is UNVERIFIED rather than a verdict built on absence of information.
        (self.repo / "brand_new.py").write_text("timeout = 30\n")
        findings = self.check(
            ["I added the line `timeout = 30`."],
            commands=[("cat > brand_new.py <<'EOF'\ntimeout = 30\nEOF", "", 0)],
        )
        self.assertEqual([f.verdict for f in findings], [UNVERIFIED])

    def test_no_diff_means_unverified_never_a_guess(self):
        findings = self.check(["I removed the line `anything`."])
        self.assertEqual([f.verdict for f in findings], [UNVERIFIED])

    def test_a_literal_in_a_file_the_session_never_wrote_decides_nothing(self):
        # The bug this replaces: alibi guessed which file the claim was about,
        # guessed wrong, and returned CONTRADICTED against an agent that had told
        # the truth. Untouched files must not be able to produce a verdict.
        self._tracked("untouched.py", "TOKEN_LIMIT = 10\n", then="TOKEN_LIMIT = 10\nmore = 1\n")
        findings = self.check(["I removed the line `TOKEN_LIMIT = 10`."])
        self.assertEqual([f.verdict for f in findings], [UNVERIFIED])
        self.assertNotIn("untouched.py", findings[0].evidence)

    def test_removing_a_line_requires_writing_the_file(self):
        # The session wrote only touched.py, and its diff does not remove the
        # literal. Deleting a line requires writing the file, so the claim that
        # this session removed it does not hold — the untouched file that still
        # contains it plays no part in the verdict.
        self._tracked("untouched.py", "TOKEN_LIMIT = 10\n", then="TOKEN_LIMIT = 10\nmore = 1\n")
        self._tracked("touched.py", "x = 1\n", then="x = 1\ny = 2\n")
        findings = self.check(
            ["I removed the line `TOKEN_LIMIT = 10`."],
            commands=[("cat > touched.py <<'EOF'\nx = 1\ny = 2\nEOF", "", 0)],
        )
        self.assertEqual([f.verdict for f in findings], [CONTRADICTED])
        self.assertIn("git diff", findings[0].evidence)
        self.assertNotIn("untouched.py", findings[0].evidence)

    def test_a_committed_change_is_unverified_because_the_diff_is_gone(self):
        # If the agent committed its work, `git diff` is empty and git cannot
        # speak. That is a real limitation, and it must read as UNVERIFIED.
        self._tracked("touched.py", "TOKEN_LIMIT = 10\nkeep = 1\n", then="keep = 1\n")
        run("git", "add", "-A", cwd=self.repo)
        run("git", "commit", "-qm", "agent work", cwd=self.repo)
        findings = self.check(
            ["I removed the line `TOKEN_LIMIT = 10`."],
            commands=[("cat > touched.py <<'EOF'\nkeep = 1\nEOF", "", 0)],
        )
        self.assertEqual([f.verdict for f in findings], [UNVERIFIED])

    def test_no_written_file_is_unverified_not_contradicted(self):
        findings = self.check(["I added the line `anything`."])
        self.assertEqual([f.verdict for f in findings], [UNVERIFIED])


class TestUnverifiedIsNeverAPass(Sandbox):
    def test_a_claim_with_nothing_to_check_is_unverified(self):
        findings = self.check(["I refactored this for clarity."])
        self.assertEqual([f.verdict for f in findings], [UNVERIFIED])

    def test_no_git_repository_makes_everything_unverified(self):
        outside = Path(self.tmp.name) / "plain"
        outside.mkdir()
        session = self.session(["I created `x.py`."], cwd=outside)
        facts = collect(session.cwd)
        findings = verify_all(extract("I created `x.py`."), facts, session)
        self.assertEqual([f.verdict for f in findings], [UNVERIFIED])


class TestSessionWrites(Sandbox):
    def test_collects_write_tool_paths(self):
        session = self.session(writes=[("Write", str(self.repo / "a.py"))])
        self.assertIn("a.py", session_writes(session))

    def test_collects_redirect_targets_but_not_stderr(self):
        session = self.session(commands=[("pytest 2>&1 > out.txt", "", 0)])
        self.assertIn("out.txt", session_writes(session))
        self.assertNotIn("/dev/null", session_writes(session))

    def test_collects_cp_and_mv_destinations(self):
        session = self.session(commands=[("cp a.py b.py", "", 0), ("mv c.py d.py", "", 0)])
        writes = session_writes(session)
        self.assertIn("b.py", writes)
        self.assertIn("d.py", writes)

    def test_collects_apply_patch_targets(self):
        session = self.session(writes=[("apply_patch", "x")])
        self.assertIsNotNone(session)

    def test_read_only_commands_produce_no_writes(self):
        session = self.session(commands=[("grep -rn foo .", "", 0), ("ls tests/", "", 0)])
        self.assertEqual(session_writes(session), set())


class TestAdapterContract(unittest.TestCase):
    def test_both_agents_ship_and_expose_the_contract(self):
        for expected in ("claude_code", "codex"):
            self.assertIn(expected, available())
            mod = load(expected)
            for attr in ("NAME", "SUMMARY", "locate", "parse"):
                self.assertTrue(hasattr(mod, attr), f"{expected}.{attr} missing")

    def test_locate_never_raises_on_a_bad_path(self):
        for agent_id, _ in locate_all() or [("claude_code", [])]:
            mod = load(agent_id)
            self.assertEqual(mod.locate("/definitely/not/here"), [])

    def test_claude_code_parse_returns_a_session_with_a_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            tf = write_transcript(Path(td) / "s.jsonl", td, ["I created `x.py`."])
            session = parse_session("claude_code", tf)
            self.assertEqual(session.cwd, td)
            self.assertEqual(len(session.assistant_messages), 1)

    def test_codex_parse_returns_a_session_with_a_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rollout.jsonl"
            records = [
                {"timestamp": "2026-09-30T10:00:00.000Z", "type": "session_meta",
                 "payload": {"id": "abc", "cwd": td}},
                {"timestamp": "2026-09-30T10:00:01.000Z", "type": "response_item",
                 "payload": {"type": "message", "role": "assistant",
                             "content": [{"type": "output_text", "text": "I created `x.py`."}]}},
                {"timestamp": "2026-09-30T10:00:02.000Z", "type": "response_item",
                 "payload": {"type": "function_call", "call_id": "c1", "name": "shell",
                             "arguments": json.dumps({"command": ["pytest", "-q"]})}},
                {"timestamp": "2026-09-30T10:00:03.000Z", "type": "response_item",
                 "payload": {"type": "function_call_output", "call_id": "c1",
                             "output": "1 failed\nExit code: 1"}},
            ]
            path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            session = parse_session("codex", path)
            self.assertEqual(session.cwd, td)
            self.assertEqual(len(session.assistant_messages), 2)
            self.assertEqual(session.messages[1].tool_calls[0].exit_code, 1)

    def test_malformed_lines_are_counted_not_swallowed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "s.jsonl"
            path.write_text(
                '{"type":"assistant","cwd":"%s","message":{"role":"assistant",'
                '"content":[{"type":"text","text":"Done."}]}}\n'
                "this is not json\n"
                "[1,2,3]\n" % td
            )
            session = parse_session("claude_code", path)
            self.assertEqual(session.skipped_lines, 2)
            self.assertEqual(len(session.messages), 1)


class TestExitCodes(Sandbox):
    """The exit code is the contract CI depends on. Crash must never look like a verdict."""

    def _run(self, *extra):
        return subprocess.run(
            [sys.executable, "-m", "alibi", "scan", *extra],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
        )

    def _transcript(self, texts=(), commands=(), writes=()):
        td = Path(self.tmp.name) / "cli"
        return write_transcript(td / "s.jsonl", str(self.repo), list(texts), list(commands), list(writes))

    def test_exit_0_when_nothing_is_contradicted(self):
        proc = self._run("--transcript", str(self._transcript(["I refactored this."])), "--color", "never")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_exit_1_when_a_claim_is_contradicted(self):
        proc = self._run("--transcript", str(self._transcript(["I created `ghost.py`."])), "--color", "never")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("CONTRADICTED", proc.stdout)

    def test_exit_2_when_there_is_nothing_to_audit(self):
        self.assertEqual(self._run("--transcript", "/definitely/not/here.jsonl").returncode, 2)

    def test_a_crash_is_not_a_contradiction(self):
        # A directory in the path used to raise IsADirectoryError out of main(),
        # which exited 1 — the same code as "a claim was contradicted".
        (self.repo / "weird.py").mkdir()
        proc = self._run("--transcript", str(self._transcript(["I removed `weird.py`."])), "--color", "never")
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("CONTRADICTED", proc.stdout)

    def test_no_fail_always_exits_zero(self):
        proc = self._run("--transcript", str(self._transcript(["I created `ghost.py`."])), "--no-fail", "--color", "never")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("CONTRADICTED", proc.stdout)

    def test_json_output_is_parseable(self):
        proc = self._run("--transcript", str(self._transcript(["I created `ghost.py`."])), "--json", "--no-fail")
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["summary"]["CONTRADICTED"], 1)
        self.assertEqual(payload["summary"]["VERIFIED"], 0)

    def test_receipt_is_markdown_and_names_the_contradiction(self):
        proc = self._run("--transcript", str(self._transcript(["I created `ghost.py`."])), "--receipt")
        self.assertIn("<!-- alibi: begin -->", proc.stdout)
        self.assertIn("Contradicted", proc.stdout)
        self.assertIn("ghost.py", proc.stdout)

    def test_unverified_never_counts_as_verified_in_the_summary(self):
        proc = self._run("--transcript", str(self._transcript(["I refactored this."])), "--json", "--no-fail")
        summary = json.loads(proc.stdout)["summary"]
        self.assertEqual(summary["VERIFIED"], 0)
        self.assertEqual(summary["UNVERIFIED"], 1)

    def test_limit_zero_means_unlimited_not_everything_by_accident(self):
        proc = self._run("--transcript", str(self._transcript(["I refactored this."])), "--limit", "0", "--json")
        self.assertEqual(json.loads(proc.stdout)["summary"]["sessions"], 1)

    def test_unreadable_transcripts_are_counted_not_swallowed(self):
        # An agent that changes its transcript format used to turn alibi into a
        # stamp that reports "nothing contradicted" while auditing nothing.
        good = self._transcript(["I created `ghost.py`."])
        bad = good.parent / "broken.jsonl"
        bad.write_text("{not json at all\nstill not json\n")
        proc = self._run("--transcript", str(good.parent), "--json", "--no-fail")
        summary = json.loads(proc.stdout)["summary"]
        self.assertGreaterEqual(summary["transcripts_unreadable"], 1)

    def test_strict_turns_unreadable_transcripts_into_exit_2(self):
        bad_dir = Path(self.tmp.name) / "broken"
        bad_dir.mkdir()
        (bad_dir / "x.jsonl").write_text("{nope\n")
        proc = self._run("--transcript", str(bad_dir), "--strict")
        self.assertEqual(proc.returncode, 2)

    def test_doctor_reports_a_broken_adapter_instead_of_hiding_it(self):
        broken = REPO_ROOT / "alibi" / "agents" / "_tmp_broken_test.py"
        broken.write_text("raise ImportError('deliberate')\n")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "alibi", "doctor"],
                cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
            )
            self.assertIn("BROKEN", proc.stderr)
        finally:
            broken.unlink()
            for cache in (REPO_ROOT / "alibi" / "agents" / "__pycache__").glob("_tmp_broken_test*"):
                cache.unlink()


class TestExitCodeParsing(unittest.TestCase):
    """The formats here are copied from a real Claude Code transcript.

    They are not invented. `Exit code 1` without a colon is what the agent
    actually writes; when the parser was tightened to demand a colon it stopped
    reading real transcripts and cost a true positive before anyone noticed.
    """

    def test_reads_the_real_marker_without_a_colon(self):
        from alibi.agents import exit_code_from_output

        self.assertEqual(exit_code_from_output("some output\nExit code 1"), 1)

    def test_reads_the_colon_form(self):
        from alibi.agents import exit_code_from_output

        self.assertEqual(exit_code_from_output("12 passed\nExit code: 0"), 0)

    def test_takes_the_last_marker_in_the_tail(self):
        from alibi.agents import exit_code_from_output

        self.assertEqual(exit_code_from_output("Exit code 0\nretry\nExit code 1"), 1)

    def test_ignores_a_number_inside_prose(self):
        from alibi.agents import exit_code_from_output

        self.assertIsNone(exit_code_from_output("see http://example.com/exit 42 docs"))

    def test_ignores_a_marker_that_is_not_at_the_end_of_its_line(self):
        from alibi.agents import exit_code_from_output

        self.assertIsNone(exit_code_from_output("exit code 1 while running the suite"))

    def test_returns_none_when_there_is_nothing_to_read(self):
        from alibi.agents import exit_code_from_output

        self.assertIsNone(exit_code_from_output(""))
        self.assertIsNone(exit_code_from_output("1 file changed, 3 insertions"))


class TestCodexParsing(unittest.TestCase):
    def test_reads_the_real_codex_shape(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rollout.jsonl"
            records = [
                {"timestamp": "2026-09-30T10:00:00.000Z", "type": "session_meta",
                 "payload": {"id": "abc", "cwd": td}},
                {"timestamp": "2026-09-30T10:00:01.000Z", "type": "response_item",
                 "payload": {"type": "message", "role": "assistant",
                             "content": [{"type": "output_text", "text": "All tests pass."}]}},
                {"timestamp": "2026-09-30T10:00:02.000Z", "type": "response_item",
                 "payload": {"type": "function_call", "call_id": "c1", "name": "shell",
                             "arguments": json.dumps({"command": ["pytest", "-q"]})}},
                {"timestamp": "2026-09-30T10:00:03.000Z", "type": "response_item",
                 "payload": {"type": "function_call_output", "call_id": "c1",
                             "output": "1 failed\nExit code 1"}},
            ]
            path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            session = parse_session("codex", path)
            self.assertEqual(session.messages[1].tool_calls[0].exit_code, 1)


class TestNoThirdPartyImports(unittest.TestCase):
    def test_source_uses_only_the_standard_library(self):
        import ast

        allowed = {
            "argparse", "dataclasses", "datetime", "hashlib", "importlib", "json",
            "os", "pathlib", "pkgutil", "re", "shutil", "subprocess", "sys",
            "typing", "unittest", "__future__",
        }
        for path in (REPO_ROOT / "alibi").rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertIn(alias.name.split(".")[0], allowed,
                                      f"{path.name} imports {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.level:  # relative import inside the package
                        continue
                    self.assertIn((node.module or "").split(".")[0], allowed,
                                  f"{path.name} imports from {node.module}")
            # __import__("re") is an import that hides from a line-based scan.
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if "__import__" in line:
                    self.fail(f"{path.name}:{i} uses __import__ instead of a top-level import")


if __name__ == "__main__":
    unittest.main()
