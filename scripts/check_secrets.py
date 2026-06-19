#!/usr/bin/env python3
"""Fail on common committed secret patterns while allowing obvious examples."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

SKIP_PREFIXES = {
    ".git/",
    "dashboard/package-lock.json",
}
TEXT_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".md", ".yml", ".yaml",
    ".css", ".html", ".sh", ".txt", ".example", "",
}
ALLOW_SUBSTRINGS = (
    "your-",
    "example",
    "fake",
    "test-",
    "user:pass@",
    "sk-ant-your-key",
    "xoxb-...",
    "xapp-...",
    "${",
)
PATTERNS = [
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"xapp-[A-Za-z0-9-]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"mongodb(?:\+srv)?://[^\s:@]+:[^\s:@]+@[^\s]+"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{16,}['\"]"),
]


def files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def is_candidate(path: str) -> bool:
    if any(path.startswith(prefix) for prefix in SKIP_PREFIXES):
        return False
    suffix = Path(path).suffix
    return suffix in TEXT_EXTENSIONS or Path(path).name in {"Dockerfile", "LICENSE", "README"}


def is_allowed(line: str) -> bool:
    lower = line.lower()
    return any(token in lower for token in ALLOW_SUBSTRINGS)


def main() -> int:
    failures: list[str] = []
    for path in files():
        if not is_candidate(path) or not os.path.exists(path):
            continue
        try:
            text = Path(path).read_text(errors="ignore")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if is_allowed(line):
                continue
            if any(pattern.search(line) for pattern in PATTERNS):
                failures.append(f"{path}:{lineno}: {line[:180]}")
    if failures:
        print("Potential committed secrets found:")
        print("\n".join(failures[:200]))
        if len(failures) > 200:
            print(f"... and {len(failures) - 200} more")
        return 1
    print("No committed secret patterns found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
