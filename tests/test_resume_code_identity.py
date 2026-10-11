"""Checkpoint code/runtime changes must reject without relying on timestamps."""
import os
from pathlib import Path

import pytest

from pdf_vector_importer import import_session as session


def test_actual_code_and_bundled_runtime_bytes_change_identity_even_with_same_stat(tmp_path):
    (tmp_path / "lib").mkdir()
    module = tmp_path / "module.py"
    runtime = tmp_path / "lib/runtime.dll"
    module.write_bytes(b"version = 1\n")
    runtime.write_bytes(b"original runtime")
    original = session.resume_content_sha256(tmp_path)
    for path, changed in ((module, b"version = 2\n"), (runtime, b"modified runtime")):
        old_bytes, old_stat = path.read_bytes(), path.stat()
        assert len(old_bytes) == len(changed)
        path.write_bytes(changed)
        os.utime(path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        assert path.stat().st_size == old_stat.st_size and path.stat().st_mtime_ns == old_stat.st_mtime_ns
        assert session.resume_content_sha256(tmp_path) != original
        path.write_bytes(old_bytes)
        assert session.resume_content_sha256(tmp_path) == original


def test_empty_or_unreadable_code_cannot_generate_a_partial_digest(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="no importer code"):
        session.resume_content_sha256(tmp_path)
    def failing_walk(_root, *, onerror, followlinks):
        onerror(PermissionError("test-owned unreadable directory"))
        yield  # pragma: no cover - raising onerror must terminate
    monkeypatch.setattr(session.os, "walk", failing_walk)
    with pytest.raises(ValueError, match="cannot read"):
        session.resume_content_sha256(tmp_path)


def test_linked_code_directory_is_rejected_and_not_traversed(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "module.py").write_text("pass", encoding="utf-8")
    package = tmp_path / "package"
    package.mkdir()
    try:
        (package / "linked-code").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Windows did not permit creating the test-owned directory symlink")
    with pytest.raises(ValueError, match="linked code"):
        session.resume_content_sha256(package)


def test_generated_bytecode_does_not_change_importer_code_identity(tmp_path):
    (tmp_path / "module.py").write_text("pass", encoding="utf-8")
    original = session.resume_content_sha256(tmp_path)
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "module.pyc").write_bytes(b"generated cache")
    assert session.resume_content_sha256(tmp_path) == original
