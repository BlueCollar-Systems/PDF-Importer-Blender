"""Batch reports must preserve each source drawing and count one outcome per file."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from blender_pdf_vector_importer import batch_cli


def _fake_run(pdf_path, **_kwargs):
    return SimpleNamespace(extraction=SimpleNamespace(
        summary=lambda: {"source": str(pdf_path)}
    ))


def test_recursive_batch_preserves_summaries_for_identical_file_names(tmp_path, monkeypatch):
    source = tmp_path / "drawings"
    expected = []
    for folder in ("job-a", "job-b"):
        pdf = source / folder / "shop.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b"%PDF-1.4\n")
        expected.append(pdf)
    summaries = tmp_path / "summaries"
    report = tmp_path / "batch.json"
    monkeypatch.setattr(batch_cli, "run_import", _fake_run)
    monkeypatch.setattr(sys, "argv", ["batch_cli", str(source), "--recursive",
                                     "--summary-dir", str(summaries), "--json", str(report)])

    assert batch_cli.main() == 0

    aggregate = json.loads(report.read_text(encoding="utf-8"))
    outputs = [Path(entry["summary_json"]) for entry in aggregate["results"]]
    assert len(set(outputs)) == 2
    assert [json.loads(path.read_text(encoding="utf-8"))["source"] for path in outputs] == [
        str(path) for path in expected
    ]
    assert aggregate["passed"] == aggregate["total"] == 2
    assert aggregate["failed"] == 0


def test_summary_write_failure_counts_file_once_and_continues(tmp_path, monkeypatch):
    source = tmp_path / "drawings"
    source.mkdir()
    for name in ("a.pdf", "b.pdf"):
        (source / name).write_bytes(b"%PDF-1.4\n")
    summaries = tmp_path / "summaries"
    summaries.mkdir()
    # A non-file destination reproduces an unavailable output without mocking IO.
    (summaries / "a.summary.json").mkdir()
    report = tmp_path / "batch.json"
    monkeypatch.setattr(batch_cli, "run_import", _fake_run)
    monkeypatch.setattr(sys, "argv", ["batch_cli", str(source), "--summary-dir", str(summaries),
                                     "--json", str(report)])

    assert batch_cli.main() == 1

    aggregate = json.loads(report.read_text(encoding="utf-8"))
    assert aggregate["passed"] + aggregate["failed"] == aggregate["total"] == 2
    assert aggregate["passed"] == aggregate["failed"] == 1
    assert [entry["status"] for entry in aggregate["results"]] == ["FAIL", "PASS"]
    assert json.loads((summaries / "b.summary.json").read_text(encoding="utf-8"))["source"].endswith("b.pdf")
