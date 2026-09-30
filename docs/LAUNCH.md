# Launch copy

Kept in the repository so it does not live only in a chat window. Use any of
these verbatim; every number in them was measured on real data and can be
reproduced with the commands shown.

---

## Hacker News  (needs an account with posting history; new accounts are refused)

Title (68 chars):

    Show HN: alibi – checks whether a PR description matches the actual diff

Body:

    Every team using a coding agent merges text an agent wrote. The PR says it
    added a module, removed a branch, fixed a timeout. Nobody reads that text
    and the diff side by side.

    alibi reads both and tells you where they disagree. No model, no API key,
    no account, no network — Python stdlib and git.

      $ alibi check .github/PULL_REQUEST.md
        1. VERIFIED     I created `alibi/writes.py` …   → the diff adds alibi/writes.py
        2. CONTRADICTED I removed `alibi/verify.py` …   → touches it but does not delete it
        3. UNVERIFIED   Tests pass.                     → that is a CI fact, not a diff fact

    Three verdicts. The third is the point: UNVERIFIED is never quietly counted
    as a pass.

    There is also `alibi scan`, which audits a coding agent's session transcripts
    the same way. On 736 real sessions it found 288 completion claims: 15 verified,
    0 contradicted, 273 unverifiable — zero false accusations. The README
    documents the wrong answers it used to give, because for a verification tool
    the red rows are the product.

    MIT, zero dependencies, CI green on macOS/Ubuntu/Windows.
      https://github.com/kevindurant735rocket-creator/alibi

Post on a weekday, US Eastern, morning. Reply to every comment for the first
six hours: the decay after the first day is steep, and comment depth is the
difference between a one-day spike and a month of traffic.

---

## Reddit  (r/ClaudeAI, r/ChatGPTCoding, r/LocalLLaMA — no new account needed)

    I found out my coding agent lies more often than I expected, so I built a
    thing that checks.

    Not "lies" as in deception. It reports what it meant to do, not what it did.
    The closing summary of a session is generated from a model of the work, and
    the filesystem is not part of that model.

    So I wrote a CLI that reads the session transcript and the git diff and
    settles the difference mechanically. Three verdicts:

      VERIFIED      the diff says what the description says
      CONTRADICTED  it does not
      UNVERIFIED    it cannot be checked

    The third is the whole point. My first version collapsed "I could not check
    this" into "looks fine" and it produced 17 false accusations in 734 real
    sessions. Not one was right. Now every claim I can't settle is reported as
    UNVERIFIED and never counted as a pass.

      $ bash docs/self_audit.sh

    Runs in a throwaway repo, plants one false claim, prints the verdict.
    Python stdlib and git, no dependencies, MIT.
    https://github.com/kevindurant735rocket-creator/alibi

---

## V2EX / 掘金  (Chinese; no new account needed)

    我给自己做的 agent 写了个"不在场证明"工具

    agent 收工时会给一段漂亮的总结：加了哪个模块、删了哪个分支、测试全过。
    这段总结是唯一的记录，而它是从"它打算做什么"生成的，不是从文件系统读出来的。

    所以我写了个 CLI，把 session 记录和 git diff 放一起对 mechanically 核验，
    只给三种结论：VERIFIED（diff 说的和描述说的一致）、CONTRADICTED（不一致）、
    UNVERIFIED（查不了）。

    第三种才是重点。我第一版会把"查不了"算成"没问题"，
    结果在 736 个真实 session 上报了 17 条指控，真阳性 0 条。
    现在查不了的一律 UNVERIFIED，绝不当成通过。

      $ bash docs/self_audit.sh

    在临时仓库里跑，植入一条假声明，打印结论。纯标准库 + git，零依赖，MIT。
    https://github.com/kevindurant735rocket-creator/alibi

---

## The one number to lead with

> Over 736 real agent sessions, 288 completion claims: 15 verified,
> 0 contradicted, 273 unverifiable. Zero false accusations.

It is smaller than a launch post would want. It is also the number that makes
everything else on the page credible, and the red rows are what a verification
tool is for.
