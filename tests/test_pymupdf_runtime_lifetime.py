"""A release must survive disposal/finalization after repeated text traces."""
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import pymupdf_runtime_smoke as smoke


def runtime_fixture(tmp_path, version="1.28.2", fail_after=None):
    package = tmp_path / "pymupdf"
    package.mkdir()
    marker = tmp_path / "calls.txt"
    (package / "__init__.py").write_text(
        f"__version__={version!r}\n"
        "from pathlib import Path\n"
        f"marker=Path({str(marker)!r})\n"
        "calls=0\n"
        "class Document:\n"
        "    def __enter__(self): return self\n"
        "    def __exit__(self, *args): marker.write_text(str(calls))\n"
        "    def new_page(self): return self\n"
        "    def insert_text(self, *args): pass\n"
        "    def get_texttrace(self):\n"
        "        global calls\n"
        "        calls += 1\n"
        f"        if {fail_after!r} is not None and calls > ({fail_after!r} or 0):\n"
        "            raise RuntimeError('late getter failure')\n"
        "        return [{} for _ in range(10)]\n"
        "def open(): return Document()\n",
        encoding="utf-8",
    )
    return marker


def test_probe_requires_all_repeated_getters_and_clean_disposal(tmp_path):
    marker = runtime_fixture(tmp_path)
    smoke.verify_runtime(sys.executable, tmp_path)
    assert marker.read_text() == "2048"


def test_successful_early_getters_cannot_hide_later_failure(tmp_path):
    marker = runtime_fixture(tmp_path, fail_after=1024)
    with pytest.raises(RuntimeError, match="late getter failure"):
        smoke.verify_runtime(sys.executable, tmp_path)
    assert marker.read_text() == "1025"


@pytest.mark.parametrize("version", ["1.27.2.3", "1.28.0", "1.28.1", "1.28.20"])
def test_probe_rejects_wrong_runtime_version(tmp_path, version):
    marker = runtime_fixture(tmp_path, version)
    with pytest.raises(RuntimeError, match="lifetime smoke failed"):
        smoke.verify_runtime(sys.executable, tmp_path)
    assert not marker.exists()


def test_success_marker_with_failed_process_finalization_is_not_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 1, smoke.SMOKE_MARKER + "\n", "fatal finalizer"))
    with pytest.raises(RuntimeError, match="fatal finalizer"):
        smoke.verify_runtime(sys.executable, tmp_path)


def test_probe_is_isolated_bounded_and_timeout_is_failure(tmp_path, monkeypatch):
    def timeout(command, **kwargs):
        assert command[1:4] == ["-I", "-B", "-c"]
        assert kwargs["timeout"] == 60
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
    monkeypatch.setattr(smoke.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="could not finish"):
        smoke.verify_runtime(sys.executable, tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="Vendored cp310-abi3 runtime targets Windows")
def test_actual_vendored_runtime_version_getters_and_finalization():
    root = Path(__file__).resolve().parents[1]
    lib = root / "pdf_vector_importer" / "lib"
    smoke.verify_runtime(sys.executable, lib, root)
    assert (lib / "pymupdf" / "extra.py").read_bytes() == (
        root / "pdf_vector_importer" / "_vendored_pymupdf_extra.py").read_bytes()
