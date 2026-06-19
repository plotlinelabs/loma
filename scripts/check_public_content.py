#!/usr/bin/env python3
"""Fail if public-blocked private markers are present in tracked text files."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

BLOCKED = [
    r"plotline",
    r"usegogo",
    r"plotlinehq",
    r"plotline\.so",
    r"@plotline\.so",
    r"PLO-",
]

SKIP_PREFIXES = {".git/", "dashboard/package-lock.json", "scripts/check_public_content.py"}
TEXT_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".md", ".yml", ".yaml",
    ".css", ".html", ".sh", ".txt", ".example", "",
}


def tracked_or_worktree_files() -> list[str]:
    result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], check=True, capture_output=True, text=True)
    return [line for line in result.stdout.splitlines() if line]


def is_candidate(path: str) -> bool:
    if any(path.startswith(prefix) for prefix in SKIP_PREFIXES):
        return False
    suffix = Path(path).suffix
    return suffix in TEXT_EXTENSIONS or Path(path).name in {"Dockerfile", "LICENSE", "README"}


def main() -> int:
    pattern = re.compile("|".join(BLOCKED), re.IGNORECASE)
    failures: list[str] = []
    for path in tracked_or_worktree_files():
        if not is_candidate(path) or not os.path.exists(path):
            continue
        try:
            text = Path(path).read_text(errors="ignore")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            normalized = line.replace("plotlinelabs", "")
            if pattern.search(normalized):
                failures.append(f"{path}:{lineno}: {line[:180]}")
    if failures:
        print("Blocked private/internal markers found:")
        print("\n".join(failures[:200]))
        if len(failures) > 200:
            print(f"... and {len(failures) - 200} more")
        return 1
    print("No blocked private/internal markers found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
