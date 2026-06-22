# -*- coding: utf-8 -*-
"""Headless checks for Blender text_mode routing helpers."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if "bpy" not in sys.modules:
    sys.modules["bpy"] = types.SimpleNamespace(
        types=types.SimpleNamespace(
            Collection=object,
            Material=object,
            Object=object,
            VectorFont=object,
        )
    )

from pdf_vector_importer.bl_text_builder import _normalize_text_mode, _text_extrusion_depth


def test_normalize_text_mode_defaults_unknown_to_3d_text():
    assert _normalize_text_mode("") == "3d_text"
    assert _normalize_text_mode("bogus") == "3d_text"


@pytest.mark.parametrize(
    "mode",
    ["labels", "3d_text", "glyphs", "geometry"],
)
def test_normalize_text_mode_accepts_all_four(mode: str):
    assert _normalize_text_mode(mode) == mode
    assert _normalize_text_mode(mode.upper()) == mode


def test_extrusion_depth_positive_for_3d_text():
    depth = _text_extrusion_depth(0.01)
    assert depth > 0.0
