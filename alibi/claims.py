"""Claim extraction: what did the agent say it did?

This module only *finds* sentences. It never judges them. Judging lives in
verify.py and is allowed exactly one answer per rule.

Two tiers come out of here:

  decidable   a sentence that names a concrete artifact (a path, a literal
              string, a command) — verify.py can settle it against the tree
  soft        a sentence that asserts a change but names no artifact — there
              is nothing to check, so it is surfaced as UNVERIFIED rather than
              quietly counted as a pass

The second tier matters as much as the first. An agent that says "I refactored
this for clarity" has made a claim. alibi reports that it cannot check it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Claim:
    kind: str  # file_created | file_deleted | string_added | string_removed
    #         | tests_pass | build_passes | lint_clean | soft
    text: str  # the sentence the agent said
    target: str = ""  # the path or literal string, when the claim names one
    line: int = 0  # 1-based index within the assistant text, for reporting

    @property
    def decidable(self) -> bool:
        return self.kind != "soft"


# A path-looking token: foo.py  src/a/b.ts  ./x  /abs/path  "some dir/file"
_PATH = r"[`\"'‘“]?(?:\.{0,2}/)?[\w.\-]+(?:/[\w.\-]+)+\.[A-Za-z0-9]+[`\"'’”]?|[\w.\-]+\.(?:py|js|ts|tsx|jsx|go|rs|rb|java|kt|swift|c|h|cc|cpp|hpp|md|txt|json|ya?ml|toml|sh|zsh|cfg|ini|html|css|sql)[`\"'’”]?"
_QUOTED = r"[`\"'‘“]([^`\"'‘’“”\n]{2,120})[`\"'‘’”]"
_FILELIKE = re.compile(r"^[\w.\-/]+\.(?:py|js|ts|tsx|jsx|mjs|cjs|go|rs|rb|java|kt|swift|c|h|cc|cpp|hpp|md|txt|json|ya?ml|toml|sh|zsh|cfg|ini|html|css|sql)$", re.I)

# Ordered: first match wins, so the specific literal forms must precede the
# generic file verbs.
_RULES: list[tuple[str, re.Pattern]] = [
    ("string_removed", re.compile(
        r"(?:removed|deleted|dropped|took out)\s+(?:the\s+)?(?:line|import|statement|block|entry|call)?\s*"
        r"(?:[`\"'‘“])([^`\"'‘’“”\n]{2,120})[`\"'’”]", re.I)),
    ("string_added", re.compile(
        r"(?:added|inserted|introduced|appended)\s+(?:the\s+)?(?:line|import|statement|block|entry|call)?\s*"
        r"(?:[`\"'‘“])([^`\"'‘’“”\n]{2,120})[`\"'’”]", re.I)),
    ("file_deleted", re.compile(
        r"(?:removed|deleted|dropped)\s+(?:the\s+)?(?:file\s+)?(?:[`\"'‘“])?(" + _PATH + r")(?:[`\"'’”])?", re.I)),
    ("file_created", re.compile(
        r"(?:created|added|new|wrote|introduced)\s+(?:the\s+)?(?:new\s+)?(?:file\s+)?(?:[`\"'‘“])?("
        + _PATH + r")(?:[`\"'’”])?", re.I)),
    ("tests_pass", re.compile(
        r"(?:all\s+)?(?:unit\s+|integration\s+|e2e\s+)?tests?\s+(?:now\s+)?(?:pass|passed|passing|are\s+green|succeed)"
        r"|tests?\s+[:：]\s*\d+\s*/\s*\d+\s*(?:通过|passed|pass)"
        r"|(?:全部|所有)\s*测试\s*(?:已)?通过"
        r"|\d+\s*/\s*\d+\s*(?:单元测试|测试|tests?)\s*(?:通过|passed)", re.I)),
    ("build_passes", re.compile(
        r"build\s+(?:now\s+)?(?:succeeds|passes|passes\s+cleanly|compiles|is\s+green)"
        r"|(?:编译|构建)\s*(?:已)?通过", re.I)),
    ("lint_clean", re.compile(
        r"(?:lint|typecheck|type-check|ruff|flake8|mypy)\s+(?:now\s+)?(?:passes|is\s+clean|clean)"
        r"|no\s+(?:lint|type)\s+errors"
        r"|(?:代码)?(?:格式|静态检查)\s*(?:已)?通过", re.I)),
]

# Claims we can see but cannot settle. Reported, never assumed true.
_SOFT = re.compile(
    r"(?:i\s+(?:have\s+)?|we\s+(?:have\s+)?|已(?:经)?|已经)"
    r"(?:refactored|improved|optimi[sz]ed|cleaned\s+up|simplified|enhanced|hardened|documented|"
    r"重构|优化|改进|简化|清理|完善|加固)"
    r"|(?:is|are)\s+(?:now\s+)?(?:more\s+)?(?:readable|maintainable|robust|efficient|performant|secure)",
    re.I)

# A sentence that announces finished work but names no artifact. This is the
# single most common shape in real transcripts (已完成 / 全部完成 / Done), and
# dropping it would hide exactly the claims a reader cannot check by eye.
_BARE_DONE = re.compile(
    r"^(?:已(?:经)?(?:全部)?完成(?:了)?|全部完成(?:了)?|搞定了?|已(?:经)?(?:修复|添加|更新|删除|创建|移除|实现|处理)"
    r"(?:了)?|完成了?(?:所有|全部)|全部(?:已)?(?:通过|就绪)"
    r"|(?:that'?s|all)\s+(?:done|set|fixed)|done|finished|completed)\b",
    re.I)

# ...and the same announcement in the middle of a longer sentence.
_DONE_INLINE = re.compile(
    r"(?:已(?:经)?(?:全部)?完成(?:了)?|全部完成(?:了)?|搞定了?|"
    r"(?:that'?s|all)\s+(?:done|set|fixed)|task\s+complete[d]?)\b",
    re.I)

# Future / intent / plan. "Now let me write verify_all.sh" is not a claim that
# anything was written, and treating it as one is the fastest way to turn a
# verifier into a liar. Anything in here is dropped before any rule runs.
_INTENT = re.compile(
    r"\b(?:let'?s|let\s+me|i'?ll|i\s+will|i'?m\s+going\s+to|going\s+to|now\s+i|next,|next\s+i|"
    r"we'?ll|we\s+will|plan\s+to|about\s+to|should\s+i|want\s+to|need\s+to|will\s+now)\b"
    r"|(?:让我|我来|我先|接下来|下一步|打算|准备|将要|即将|会先|先来|计划)",
    re.I)

# Lines that only restate a heading, a plan, or a question are not claims.
_SKIP_LINE = re.compile(r"^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|\||>|\`\`\`)|^\s*$")


def _split_sentences(text: str) -> list[str]:
    flat = " ".join(text.split())
    if not flat:
        return []
    parts = re.split(r"(?<=[.!?。！？])\s+|(?<=[\u4e00-\u9fff])[，、；](?=[^\s])", flat)
    return [p.strip() for p in parts if p and p.strip()]


def _clean(value: str) -> str:
    return value.strip().strip("`\"'‘’“”")


def extract(text: str) -> list[Claim]:
    """Pull claim candidates out of one assistant message."""
    found: list[Claim] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, sentence: str, target: str = "") -> None:
        key = (kind, target or sentence[:60])
        if key in seen:
            return
        seen.add(key)
        found.append(Claim(kind=kind, text=sentence[:240], target=target))

    for sentence in _split_sentences(text):
        if len(sentence) < 4 or _SKIP_LINE.match(sentence):
            continue
        # Intent is not a claim. Drop it before any rule can fire.
        if _INTENT.search(sentence):
            continue

        matched_any = False
        for kind, pattern in _RULES:
            m = pattern.search(sentence)
            if not m:
                continue
            target = _clean(m.group(1)) if m.groups() else ""
            if kind in ("file_created", "file_deleted", "string_added", "string_removed") and not target:
                continue
            # A quoted token that ends in a source extension is a file, not a
            # line of code. "I removed `existing.py`" means the file is gone,
            # and reading it as a string search silently downgraded a real
            # contradiction into an unverifiable shrug.
            if kind == "string_removed" and _FILELIKE.match(target):
                kind = "file_deleted"
            elif kind == "string_added" and _FILELIKE.match(target):
                kind = "file_created"
            add(kind, sentence, target)
            matched_any = True

        # One sentence can assert more than one thing: "the build succeeds and
        # lint is clean" is two claims, and reporting only the first would hide
        # whichever one has no evidence behind it.
        if not matched_any and (_BARE_DONE.search(sentence) or _SOFT.search(sentence)):
            add("soft", sentence)

    if _SOFT.search(" ".join(text.split())) and not _INTENT.search(text):
        snippet = next(
            (s for s in _split_sentences(text) if _SOFT.search(s) and not _INTENT.search(s)),
            "",
        )
        if snippet:
            add("soft", snippet)
    return found


def extract_session(session) -> list[Claim]:
    """All claims across a session's assistant messages, in order."""
    claims: list[Claim] = []
    for message in session.assistant_messages:
        if not message.text:
            continue
        for claim in extract(message.text):
            claims.append(claim)
    return claims
