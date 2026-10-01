# The self-audit

alibi checking a pull request description against a real diff, in a throwaway
repository. Reproduce it:

```console
$ bash docs/self_audit.sh
```

```console
  Three claims are true of this diff. One is not. Here is alibi's reading:

    1. VERIFIED     file_created
       said     I created `demo_check.py` to give the self-audit a real diff to check.
       diff     the diff adds demo_check.py

    2. CONTRADICTED file_deleted
       said     I removed `alibi_verify.py` to simplify the judging layer.
       diff     the diff does not delete alibi_verify.py

    3. VERIFIED     string_added
       said     Added the line `this line exists so alibi has something real to check` to the new...
       diff     the diff adds a line containing 'this line exists so alibi has something real to to...'

    4. UNVERIFIED   tests_pass
       said     Tests pass.
       diff     whether the suite passed is a CI fact, not something a diff can show

  ✓ 2 verified   ✗ 1 contradicted   ? 1 unverified
  exit code: 1
```

## Why this is the demo worth having

The false claim is the interesting one, and it is the shape almost every
agent's closing summary takes: *"I did X"* where the diff plainly shows
something else. It is not a lie in the sense anyone means when they say an agent
hallucinated — agents are not lying, they are reporting from a model of what
they meant to do rather than from the filesystem. The gap between the two is
what a review has to catch, and it is mechanical to catch.

The fourth claim matters just as much. "Tests pass" is the sentence an agent
writes most often and the one a diff can say least about. alibi returns
`UNVERIFIED` rather than `VERIFIED`, because a tool that passed that sentence
off would be making exactly the kind of unfounded claim it exists to detect.

## What this does not show

It is one synthetic diff, and it shows the mechanism working. It is not
evidence about how often agent descriptions are wrong. The honest number for
that is in the README, and it is smaller than you would hope: over 736 real
agent sessions, `alibi scan` found 288 completion claims and contradicted none
of them, because 273 named no path, literal or command that could be checked.

## Two more, found by running the tool on its own repository

Both were found by pointing `alibi check` at this project's own commit messages
and watching it report success.

**`--base <sha>` measured nothing.** The implementation built
`git diff base...HEAD`, which is the merge-base form. That is right for a
branch name and silently meaningless for a commit: given a SHA it compared the
merge base of a commit with HEAD, produced an empty diff, and returned
UNVERIFIED for every claim — that is, *agreement it had not found*. The
two-dot form (`git diff a..b`) says what it means: these two trees, against each
other. A verifier that cannot tell "nothing is wrong" from "I looked at
nothing" has the one failure mode this project cannot have.

**The header lied.** The diff summary printed "uncommitted changes"
unconditionally — a hardcoded string that was true for exactly one of the three
ways `alibi check` can read a diff. It now names the two trees it compared.

