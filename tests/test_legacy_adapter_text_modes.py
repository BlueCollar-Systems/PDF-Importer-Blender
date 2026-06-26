"""Regression checks for legacy Blender adapter text mode routing."""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "blender_pdf_vector_importer" / "adapters" / "blender_adapter.py"


def test_legacy_adapter_passes_selected_text_mode_to_builder() -> None:
    source = ADAPTER.read_text(encoding="utf-8")

    assert "_create_text_object(" in source
    assert "opts.text_mode" in source
    assert "_create_text_object(text, page_collection)" not in source


def test_legacy_adapter_text_modes_have_distinct_outputs() -> None:
    source = ADAPTER.read_text(encoding="utf-8")

    assert "def _normalize_text_mode(" in source
    assert 'if mode == "3d_text":' in source
    assert "curve.extrude = _text_extrusion_depth(curve.size)" in source
    assert 'if mode in {"glyphs", "geometry"}:' in source
    assert "def _meshify_text_object(" in source
    assert 'obj["pdf_text_mode"] = mode' in source
