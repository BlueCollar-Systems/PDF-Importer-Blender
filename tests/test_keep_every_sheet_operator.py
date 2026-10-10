"""The import dialog never calls a mostly-finished import a failure.

When every sheet was built but some items need a look, the operator finishes
(the sheets stay as one undo step) and lists the items as plain warnings.
Fictional sheet D042 only.
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest


@pytest.fixture
def operators_module(monkeypatch: pytest.MonkeyPatch):
    fake_bpy = types.ModuleType("bpy")
    fake_bpy.app = types.SimpleNamespace(version=(4, 1, 0))
    fake_bpy.ops = types.SimpleNamespace(wm=types.SimpleNamespace(redraw_timer=lambda **_k: None))
    fake_bpy.types = types.SimpleNamespace(
        Collection=object, Material=object, Object=object, VectorFont=object,
        Operator=type("Operator", (), {}),
    )
    fake_bpy.data = types.SimpleNamespace()
    fake_bpy.context = types.SimpleNamespace()
    props = types.ModuleType("bpy.props")
    for name in ("BoolProperty", "EnumProperty", "FloatProperty", "StringProperty"):
        setattr(props, name, lambda **_kwargs: None)
    io_utils = types.ModuleType("bpy_extras.io_utils")
    io_utils.ImportHelper = type("ImportHelper", (), {})
    bpy_extras = types.ModuleType("bpy_extras")
    bpy_extras.io_utils = io_utils
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    monkeypatch.setitem(sys.modules, "bmesh", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "bpy.props", props)
    monkeypatch.setitem(sys.modules, "bpy_extras", bpy_extras)
    monkeypatch.setitem(sys.modules, "bpy_extras.io_utils", io_utils)
    engine = importlib.import_module("pdf_vector_importer.bl_import_engine")
    previous = sys.modules.pop("pdf_vector_importer.operators", None)
    try:
        yield importlib.import_module("pdf_vector_importer.operators"), engine
    finally:
        sys.modules.pop("pdf_vector_importer.operators", None)
        if previous is not None:
            sys.modules["pdf_vector_importer.operators"] = previous


def _operator(operators, tmp_path):
    operator = operators.IMPORT_OT_pdf_vector()
    for name in ("mode", "pages", "text_mode", "visual_style", "page_arrangement", "model3d_mode"):
        setattr(operator, name, "")
    for name in ("show_advanced", "resume_interrupted", "import_text", "group_by_color",
                 "auto_focus_view", "keep_selection_after_focus", "auto_hide_default_cube",
                 "white_page_background"):
        setattr(operator, name, False)
    for name in ("line_z_offset_mm", "text_z_offset_mm", "image_z_offset_mm", "page_gap_ratio",
                 "model3d_depth_mm"):
        setattr(operator, name, 0.0)
    operator.filepath = str(tmp_path / "D042.pdf")
    reports = []
    operator.report = lambda level, message: reports.append((level, message))
    return operator, reports


def _context(prefs=None):
    addons = {} if prefs is None else {"pdf_vector_importer": types.SimpleNamespace(preferences=prefs)}
    return types.SimpleNamespace(preferences=types.SimpleNamespace(addons=addons), workspace=None)


def test_incomplete_import_finishes_with_plain_warnings(monkeypatch, operators_module, tmp_path):
    operators, engine = operators_module
    stats = {
        "pages_requested": 3, "pages_imported": 3,
        "text_delivery_failed_item_ids": ["page:2:text:4"],
        "text_page_edge_warnings": [{"page": 2, "skipped_items": [{"entity_id": "EX102_c0"}]}],
        "import_report_path": str(tmp_path / "run" / "D042_import_report.json"),
    }

    def incomplete(*_args, **_kwargs):
        raise engine.IncompleteImportError(
            ["text delivery failed (required=3, recorded=3, delivered=2, zero_ink=0, failed=1)"], stats)

    monkeypatch.setattr(engine, "import_pdf", incomplete)
    prefs = types.SimpleNamespace(remember_last_directory=True, last_import_dir="")
    operator, reports = _operator(operators, tmp_path)

    assert operator.execute(_context(prefs)) == {"FINISHED"}

    assert {tuple(level) for level, _message in reports} == {("WARNING",)}
    messages = [message for _level, message in reports]
    assert not any(message.startswith("PDF import failed") for message in messages)
    assert messages[0] == "Imported 3 of 3 sheets; 2 items need a look."
    assert any("sheet 2" in message for message in messages[1:])
    assert not any("required=" in message or ".json" in message for message in messages)
    assert prefs.last_import_dir == str(tmp_path)
    assert operators._IMPORT_ACTIVE is False


def test_a_real_error_still_says_failed(monkeypatch, operators_module, tmp_path):
    operators, engine = operators_module

    def broken(*_args, **_kwargs):
        raise RuntimeError("PyMuPDF runtime is missing")

    monkeypatch.setattr(engine, "import_pdf", broken)
    operator, reports = _operator(operators, tmp_path)
    assert operator.execute(_context()) == {"CANCELLED"}
    assert reports == [({"ERROR"}, "PDF import failed: PyMuPDF runtime is missing")]


def test_kept_sheet_edge_letters_are_listed_after_a_finished_import(
        monkeypatch, operators_module, tmp_path):
    operators, engine = operators_module
    monkeypatch.setattr(engine, "import_pdf", lambda *_a, **_k: {
        "pages_requested": 3, "pages_imported": 3, "primitives": 9,
        "text_page_edge_warnings": [{"page": 2, "skipped_items": [{"entity_id": "EX102_c0"}]}],
        "import_report_path": str(tmp_path / "run" / "D042_import_report.json"),
    })
    operator, reports = _operator(operators, tmp_path)
    assert operator.execute(_context()) == {"FINISHED"}
    warnings = [message for level, message in reports if level == {"WARNING"}]
    assert warnings == ["1 letter on sheet 2 was left untrimmed at the sheet edge.",
                        f"Details: {tmp_path / 'run'}"]
    assert not [message for level, message in reports if level == {"ERROR"}]
