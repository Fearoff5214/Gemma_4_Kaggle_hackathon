"""Fail if staged files contain something that looks like a credential.

Usage:
    python scripts/scan_secrets.py            # scan files staged for commit
    python scripts/scan_secrets.py FILE...    # scan the given files

Enable it as a pre-commit hook once per clone:
    git config core.hooksPath scripts/hooks
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PATTERNS = {
    "Kaggle API key in JSON": re.compile(r'"key"\s*:\s*"[0-9a-f]{32}"'),
    "Kaggle key assignment": re.compile(r"KAGGLE_(?:KEY|API_TOKEN)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{20,}"),
    "Kaggle access token": re.compile(r"\bKGAT_[A-Za-z0-9]{20,}"),
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}"),
    "Hugging Face token": re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
}
FORBIDDEN_NAMES = {"kaggle.json", "access_token", ".env"}


def staged_files() -> list[Path]:
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z"],
                         capture_output=True, text=True, check=True).stdout
    return [Path(name) for name in out.split("\0") if name]


def scan(paths: list[Path]) -> list[str]:
    findings = []
    for path in paths:
        if path.name in FORBIDDEN_NAMES:
            findings.append(f"{path}: this file must never be committed")
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue  # deleted or unreadable; nothing to leak
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{path}:{line}: looks like a {label}")
    return findings


def main(argv: list[str]) -> int:
    paths = [Path(arg) for arg in argv] if argv else staged_files()
    findings = scan(paths)
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        print("Secret scan failed. Remove the value (keep it in .env or ~/.kaggle) and try again.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
