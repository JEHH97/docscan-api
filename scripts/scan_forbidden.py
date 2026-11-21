"""Reject accidentally committed organization-specific names and contact details."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
# Terms are represented as code points so that this guard does not embed them in the source.
BLOCKED = [
    "".join(map(chr, [83, 105, 116, 101, 68, 111, 99])),
    "".join(map(chr, [65, 84, 76, 65, 83])),
    "".join(map(chr, [83, 75, 89, 76, 73, 78, 69])),
    "".join(map(chr, [76, 69, 78, 83])),
    "".join(map(chr, [79, 112, 115, 67, 101, 110, 116, 101, 114])),
    "".join(map(chr, [79, 110, 101, 87, 101, 98])),
    "".join(map(chr, [69, 117, 116, 101, 108, 115, 97, 116])),
    "".join(map(chr, [66, 101, 97, 109])),
    "".join(map(chr, [103, 105, 116, 46, 118, 105, 97, 115, 97, 116, 46, 99, 111, 109])),
    "".join(map(chr, [64, 118, 105, 97, 115, 97, 116, 46, 99, 111, 109])),
    "".join(map(chr, [99, 233, 100, 117, 108, 97])),
]
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\s().-]*){10,15}(?!\w)")


def is_lock_file(path: Path) -> bool:
    """Return whether a file is a generated dependency lock with version/hash data."""
    return path.name == "uv.lock" or (
        path.name.startswith("requirements") and path.name.endswith(".lock")
    )


def has_phone_number(content: str) -> bool:
    """Return whether text contains a phone-like sequence."""
    phone_content = re.sub(
        r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b",
        "",
        content,
    )
    phone_content = re.sub(r"\b[0-9a-fA-F]{64}\b", "", phone_content)
    phone_content = re.sub(r"(?m)([A-Za-z0-9_.-]+==)[A-Za-z0-9.+!_-]+", r"\1", phone_content)
    return PHONE.search(phone_content) is not None


def file_violations(path: Path, root: Path = ROOT) -> list[str]:
    """Return prohibited-content findings for one repository file."""
    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []

    relative_path = path.relative_to(root)
    violations: list[str] = []
    for term in BLOCKED:
        if term.casefold() in content.casefold():
            violations.append(f"{relative_path}: prohibited text")
            break
    if not is_lock_file(path) and has_phone_number(content):
        violations.append(f"{relative_path}: possible phone number")
    return violations


def scanned_files() -> list[Path]:
    """List repository files while excluding virtual environments and caches."""
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not any(part in EXCLUDED_PARTS for part in path.parts)
    ]


def main() -> int:
    """Return nonzero if any prohibited content is found."""
    violations: list[str] = []
    for path in scanned_files():
        violations.extend(file_violations(path))
    if violations:
        print("Forbidden-content scan failed:", *violations, sep="\n- ", file=sys.stderr)
        return 1
    print(f"Forbidden-content scan passed ({len(scanned_files())} files checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
