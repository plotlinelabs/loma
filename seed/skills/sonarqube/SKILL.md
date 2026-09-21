---
name: sonarqube
description: Read a pull request's SonarQube quality gate (verdict, failed conditions, new issues with file:line) and clear it by fixing the issues. Use when someone asks about Sonar issues on a PR, and after opening a PR in a repo that has a Sonar gate.
user-invocable: true
---

# SonarQube Quality Gate

A repo can require a SonarQube quality gate on every PR. The gate usually fails on *any* new issue
(any severity), unreviewed security hotspots, new duplication, or low coverage on new code. This skill
reads the gate result CI already produced for a PR, and walks you through clearing it.

The tool reads CI's own analysis, so its verdict is the one that blocks the merge. Never run your own
scanner or a local Sonar instead: it wouldn't have the server's accepted issues or baseline, and would
disagree with CI.

---

## Is the repo gated?

```bash
python3 -c "import json,os; print('\n'.join(json.loads(os.environ.get('SONAR_PROJECTS','{}'))))"
```

Only repos listed there are gated. For any other repo, skip this skill.

---

## Read a PR's gate

```bash
python3 tools/sonarqube.py pr --repo <owner>/<repo> --pr <number>           # current result
python3 tools/sonarqube.py pr --repo <owner>/<repo> --pr <number> --wait    # wait for CI (≤100 s per call)
```

Exit code: `0` passed · `1` failed · `2` unknown (no analysis, or a config problem) · `3` CI still running.

**Waiting for CI:** a gate run takes about 5–15 minutes. Each `--wait` call returns within ~100 seconds,
so it never trips your shell tool's time limit. While it exits `3` ("PENDING"), run the exact same
command again. Stop after 20 tries (about 30 minutes) and report the gate as still running.

Output, per Sonar project the PR touches:

```
[server] ERROR
  x new_violations: 2 (fails when GT 0)
  src/services/foo.ts:56  [typescript:S6544] Expected non-Promise value in a boolean conditional.
  src/services/foo.ts:350 [typescript:S3776] Refactor this function to reduce its Cognitive Complexity from 18 to the 15 allowed.
```

- **"no analysis for this PR"** means the scan never ran, almost always because tests or lint failed
  before it. Read the failing CI job, fix that first, push, then re-check.
- **"CI gate check: failure" with every project OK** also means a non-Sonar job failed (tests, lint).
- `python3 tools/sonarqube.py check` verifies connectivity and the token.

---

## Clear the gate (after you pushed code to a PR)

1. `python3 tools/sonarqube.py pr --repo <owner>/<repo> --pr <number> --wait`, repeated while it exits `3`
2. If it passed (exit 0): done. Say so in your summary, e.g. "SonarQube gate: passed".
3. If it failed (exit 1): fix **every** listed issue in the files and lines shown. Common rules:
   - `S3776` cognitive complexity: extract helpers or return early. Don't just move code around.
   - `S107` too many parameters: pass a struct/object.
   - `S7781` prefer `replaceAll`: and use a string argument, not a plain regex like `/_/g`, or the fix raises a new S7781.
   - Duplication: move the repeated code into one shared helper instead of copying it.
   - Coverage on new code: add tests for the new lines. The gate measures lines you added or changed.
   - A pre-existing issue on a line you edited counts as new. Fix it too.
4. Commit and push to the same branch; CI re-runs on push. Go back to step 1.
5. **Stop after 2 fix rounds.** Each round waits on a full CI run (~10 min) and agent runs are time-boxed
   (~30 min). If the gate still fails, report the remaining issues and what you tried; if CI is still
   running when you must stop, say the gate is still running and link the PR.

### Never
- Mark issues as Accepted / False Positive in Sonar, or ask for the gate to be widened. That's a human
  decision; mention it as an option in your summary if an issue really isn't actionable.
- Add suppression comments (`NOSONAR`, `// eslint-disable`) to get past the gate.
- Claim the gate passed when the tool returned `2` (unknown) or `3` (still running). Say so instead.
- Raise `--timeout` above 100: long single commands get killed by the shell tool. Re-run instead.

---

## Answering "what are the Sonar issues on PR #N?"

Run the tool without `--wait` (or with it, if CI is still running), then summarise:
verdict per project, the failed conditions, and the issues grouped by file. Offer to fix them.
