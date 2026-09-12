"""The package identity still hashes exactly the packaged files, read concurrently.

Every import re-reads the installed package to prove its bytes match the release
identity.  The reads are overlapped because per-file open latency on Windows made
that proof cost 2 s per import; the entries, their order and the manifest hash
must be what a sequential walk produces.
"""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pdf_vector_importer import build_identity  # noqa: E402


def _sequential_entries(package_root: Path) -> list[tuple[str, bytes]]:
    parent = package_root.parent
    entries = []
    for path in package_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(package_root)
        if path.name == build_identity.IDENTITY_FILENAME or build_identity.is_excluded_package_member(relative):
            continue
        entries.append((path.relative_to(parent).as_posix(), path.read_bytes()))
    return entries


def _make_tree(root: Path, files: int) -> Path:
    package = root / "pdf_vector_importer"
    (package / "pdfcadcore").mkdir(parents=True)
    (package / "__pycache__").mkdir()
    for index in range(files):
        target = package / ("pdfcadcore" if index % 3 else ".") / f"module_{index:03d}.py"
        target.write_bytes(f"VALUE = {index}\n".encode("utf-8") * (index + 1))
    (package / "__pycache__" / "module_000.cpython-311.pyc").write_bytes(b"\x00compiled")
    (package / build_identity.IDENTITY_FILENAME).write_text("{}", encoding="utf-8")
    return package


def test_overlapped_reads_match_a_sequential_walk(tmp_path: Path) -> None:
    package = _make_tree(tmp_path, files=40)
    expected = _sequential_entries(package)
    actual = build_identity._package_entries(package)
    assert actual == expected
    assert [name for name, _ in actual] == [name for name, _ in expected]
    assert all(not name.endswith(build_identity.IDENTITY_FILENAME) for name, _ in actual)
    assert all("__pycache__" not in name for name, _ in actual)
    assert build_identity.content_manifest_sha256(actual) == build_identity.content_manifest_sha256(expected)


def test_empty_package_has_no_entries(tmp_path: Path) -> None:
    package = tmp_path / "pdf_vector_importer"
    package.mkdir()
    assert build_identity._package_entries(package) == []


def test_runtime_identity_still_verifies_a_manifested_package(tmp_path: Path) -> None:
    package = _make_tree(tmp_path, files=12)
    (package / "__init__.py").write_text('bl_info = {"version": (9, 8, 7)}\n', encoding="utf-8")
    (package / "bl_import_engine.py").write_text("ENGINE = True\n", encoding="utf-8")
    (package / "bl_text_builder.py").write_text("TEXT = True\n", encoding="utf-8")
    (package / build_identity.IDENTITY_FILENAME).unlink()
    identity = build_identity.create_release_identity(
        importer_version="9.8.7",
        source_commit="a" * 40,
        source_tag="v9.8.7",
        package_entries=build_identity._package_entries(package),
    )
    (package / build_identity.IDENTITY_FILENAME).write_text(
        __import__("json").dumps(identity), encoding="utf-8"
    )
    verified = build_identity.runtime_package_identity(package)
    assert verified["package_sha256"] == identity["package_sha256"]
    assert verified["status"] == "verified"
