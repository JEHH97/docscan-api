"""Focused tests for the forbidden-content repository guard."""

from pathlib import Path

from scripts.scan_forbidden import BLOCKED, file_violations, has_phone_number


def test_hash_does_not_match_phone_heuristic() -> None:
    """Hexadecimal hash strings are not phone-like sequences."""
    assert not has_phone_number("sha256:" + "a1b2" * 16)


def test_phone_number_in_normal_file_is_rejected(tmp_path: Path) -> None:
    """The phone heuristic remains enabled for ordinary repository files."""
    phone = "202" + "555" + "0147"
    file_path = tmp_path / "notes.txt"
    file_path.write_text(phone, encoding="utf-8")

    assert file_violations(file_path, tmp_path) == ["notes.txt: possible phone number"]


def test_prohibited_term_in_lock_file_is_still_rejected(tmp_path: Path) -> None:
    """Lock files skip only phone-number detection, not prohibited terms."""
    file_path = tmp_path / "requirements-test.lock"
    file_path.write_text(BLOCKED[0], encoding="utf-8")

    assert file_violations(file_path, tmp_path) == ["requirements-test.lock: prohibited text"]


def test_generated_lock_versions_do_not_match_phone_heuristic(tmp_path: Path) -> None:
    """Long generated version fields in lock files do not produce phone findings."""
    file_path = tmp_path / "uv.lock"
    version = "2.33.0." + "2026" + "1030"
    file_path.write_text(f'version = "{version}"\n', encoding="utf-8")

    assert file_violations(file_path, tmp_path) == []
