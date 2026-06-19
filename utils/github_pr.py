"""Shared utility: clone a repo, run Claude CLI on it, push changes, create a PR.

Usage:
    from utils.github_pr import clone_and_run_claude

    result = await clone_and_run_claude(
        repo="example-org/example-repo",
        prompt="Integrate these learnings into the skill files...",
        branch_prefix="fix/graduate-learnings",
        pr_title="fix: graduate 5 org learnings",
        pr_body="## Learnt\n...",
    )
    # result = {"pr_url": "https://...", "pr_number": 42, "branch": "fix/...", "files_changed": [...]}
"""

import asyncio
import json
import logging
import os
import shutil
import uuid

logger = logging.getLogger(__name__)


async def _run(cmd: list[str], cwd: str, env: dict | None = None, timeout: int = 120) -> tuple[str, str, int]:
    """Run a subprocess, return (stdout, stderr, returncode)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return stdout.decode().strip(), stderr.decode().strip(), proc.returncode


async def clone_and_run_claude(
    repo: str,
    prompt: str,
    branch_prefix: str,
    pr_title: str,
    pr_body: str,
    model: str = "claude-opus-4-8",
    max_turns: int = 100,
    draft: bool = True,
    timeout: int = 600,
    append_system_prompt: str | None = None,
) -> dict:
    """Clone a repo, run Claude CLI on it, push changes, create a draft PR.

    Args:
        repo: GitHub repo in "owner/name" format (e.g. "example-org/example-repo")
        prompt: The task prompt for Claude to execute in the cloned repo
        branch_prefix: Branch name prefix (e.g. "fix/graduate-learnings")
        pr_title: Title for the pull request
        pr_body: Body/description for the pull request
        model: Claude model to use
        max_turns: Max agentic turns for Claude
        draft: Whether to create a draft PR
        timeout: Max seconds for the Claude CLI process
        append_system_prompt: Optional system prompt to append (e.g. skill content for non-self repos)

    Returns:
        dict with keys: pr_url, pr_number, branch, files_changed
    """
    token = os.environ.get("GITHUB_API_KEY", "")
    if not token:
        raise ValueError("GITHUB_API_KEY environment variable is required")

    short_id = uuid.uuid4().hex[:8]
    work_dir = f"/tmp/claude-pr-{short_id}"
    branch_name = f"{branch_prefix}-{short_id}"
    clone_url = f"git@github.com:{repo}.git"

    # Build env for subprocesses — unset CLAUDECODE to avoid nested-session error
    sub_env = {**os.environ, "CLAUDECODE": "", "GH_TOKEN": token}

    try:
        # 1. Clone
        logger.info("[PR-UTIL] Cloning %s to %s ...", repo, work_dir)
        stdout, stderr, rc = await _run(
            ["git", "clone", "--depth", "1", clone_url, work_dir],
            cwd="/tmp",
            env=sub_env,
            timeout=60,
        )
        if rc != 0:
            raise ValueError(f"git clone failed (exit {rc}): {stderr[:300]}")

        # 2. Create branch
        logger.info("[PR-UTIL] Creating branch %s", branch_name)
        _, stderr, rc = await _run(
            ["git", "checkout", "-b", branch_name],
            cwd=work_dir,
            env=sub_env,
        )
        if rc != 0:
            raise ValueError(f"git checkout -b failed (exit {rc}): {stderr[:300]}")

        # 3. Run Claude CLI with stream-json so we can log progress in real time
        log_path = os.path.join(work_dir, "claude.log")
        logger.info("[PR-UTIL] Running Claude (model=%s, max_turns=%d) — log: %s", model, max_turns, log_path)
        cmd = [
            "claude", "-p",
            "--verbose",
            "--model", model,
            "--max-turns", str(max_turns),
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
            "--no-session-persistence",
        ]
        if append_system_prompt:
            cmd.extend(["--append-system-prompt", append_system_prompt])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=work_dir,
            env=sub_env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=10 * 1024 * 1024,  # 10MB line buffer — stream-json events can be large
        )
        # Send prompt via stdin, then close it
        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        # Read stream-json events line by line, log them, write to log file
        result_envelope = None
        with open(log_path, "w") as log_file:
            async def _drain_stderr():
                while True:
                    line = await proc.stderr.readline()
                    if not line:
                        break
                    log_file.write(line.decode())

            stderr_task = asyncio.create_task(_drain_stderr())

            while True:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
                if not line:
                    break
                text = line.decode().strip()
                if not text:
                    continue
                log_file.write(text + "\n")
                log_file.flush()

                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type", "")

                if etype == "assistant" and "message" in event:
                    # Log tool use and text blocks
                    for block in event["message"].get("content", []):
                        if block.get("type") == "tool_use":
                            tool = block.get("name", "?")
                            inp = str(block.get("input", ""))[:120]
                            logger.info("[PR-UTIL] Tool: %s — %s", tool, inp)
                        elif block.get("type") == "text":
                            snippet = block.get("text", "")[:200]
                            if snippet.strip():
                                logger.info("[PR-UTIL] Text: %s", snippet)

                elif etype == "result":
                    result_envelope = event

            await stderr_task
            await proc.wait()

        if proc.returncode != 0:
            raise ValueError(f"Claude CLI failed (exit {proc.returncode}) — see {log_path}")

        # 4. Check result envelope
        if result_envelope:
            subtype = result_envelope.get("subtype", "")
            if subtype.startswith("error"):
                raise ValueError(f"Claude CLI error: {subtype} — see {log_path}")
            logger.info("[PR-UTIL] Claude finished (subtype=%s, turns=%s)",
                        subtype, result_envelope.get("num_turns", "?"))
        else:
            logger.warning("[PR-UTIL] No result envelope received from Claude")

        # 5. Remove claude.log before checking changes (it's our log, not a repo file)
        claude_log_in_repo = os.path.join(work_dir, "claude.log")
        if os.path.exists(claude_log_in_repo):
            os.remove(claude_log_in_repo)

        # Check for changes
        stdout, _, _ = await _run(["git", "diff", "--name-only"], cwd=work_dir, env=sub_env)
        # Also check staged and untracked files
        stdout2, _, _ = await _run(["git", "status", "--porcelain"], cwd=work_dir, env=sub_env)

        if not stdout and not stdout2:
            raise ValueError("Claude made no changes to the repo")

        files_changed = [
            line.lstrip("MADRCU? ") for line in (stdout2 or stdout).splitlines() if line.strip()
        ]
        logger.info("[PR-UTIL] %d files changed: %s", len(files_changed), files_changed)

        # 6. Commit
        await _run(["git", "add", "-A"], cwd=work_dir, env=sub_env)
        _, stderr, rc = await _run(
            ["git", "commit", "-m", pr_title],
            cwd=work_dir,
            env=sub_env,
        )
        if rc != 0:
            raise ValueError(f"git commit failed (exit {rc}): {stderr[:300]}")

        # 7. Push
        logger.info("[PR-UTIL] Pushing branch %s ...", branch_name)
        _, stderr, rc = await _run(
            ["git", "push", "-u", "origin", branch_name],
            cwd=work_dir,
            env=sub_env,
            timeout=60,
        )
        if rc != 0:
            raise ValueError(f"git push failed (exit {rc}): {stderr[:300]}")

        # 8. Create PR via gh CLI
        logger.info("[PR-UTIL] Creating PR ...")
        gh_cmd = [
            "gh", "pr", "create",
            "--repo", repo,
            "--head", branch_name,
            "--title", pr_title,
            "--body", pr_body,
        ]
        if draft:
            gh_cmd.append("--draft")

        stdout, stderr, rc = await _run(gh_cmd, cwd=work_dir, env=sub_env, timeout=30)
        if rc != 0:
            raise ValueError(f"gh pr create failed (exit {rc}): {stderr[:300]}")

        # gh pr create prints the PR URL on stdout
        pr_url = stdout.strip()
        logger.info("[PR-UTIL] PR created: %s", pr_url)

        # Extract PR number from URL (e.g. https://github.com/owner/repo/pull/42)
        pr_number = int(pr_url.rstrip("/").split("/")[-1])

        result = {
            "pr_url": pr_url,
            "pr_number": pr_number,
            "branch": branch_name,
            "files_changed": files_changed,
        }

    except Exception:
        # On failure, keep work_dir for debugging (log file is at {work_dir}/claude.log)
        logger.error("[PR-UTIL] Failed — work dir preserved at %s for debugging", work_dir)
        raise

    # On success, clean up
    if os.path.exists(work_dir):
        shutil.rmtree(work_dir, ignore_errors=True)
        logger.info("[PR-UTIL] Cleaned up %s", work_dir)

    return result
