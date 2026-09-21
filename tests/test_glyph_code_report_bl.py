"""Text delivered as raw glyph codes reaches the Blender operator and report.

Blender imports through pdfcadcore.extract_page, so the shared recovery runs
inside extraction; this host's job is to tally the records per page, publish
extra.text_glyph_codes, add the unproven spans to result.warnings and hand the
caller one operator line.

Synthetic records only (SAMPLE font, fictional sheet D042, job 1000-01).
"""
from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _install_blender_stubs(monkeypatch: pytest.MonkeyPatch):
    """The smallest bpy this report path touches; never the real Blender."""
    fake_bpy = types.ModuleType("bpy")
    fake_bpy.app = types.SimpleNamespace(version=(4, 1, 0))
    fake_bpy.ops = types.SimpleNamespace(
        wm=types.SimpleNamespace(redraw_timer=lambda **_kwargs: None)
    )
    fake_bpy.types = types.SimpleNamespace(
        Collection=object, Material=object, Object=object, VectorFont=object,
        Operator=type("Operator", (), {}),
    )
    fake_bpy.data = types.SimpleNamespace()
    fake_bpy.context = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    monkeypatch.setitem(sys.modules, "bmesh", types.SimpleNamespace())
    return fake_bpy


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch):
    _install_blender_stubs(monkeypatch)
    module = importlib.import_module("pdf_vector_importer.bl_import_engine")
    monkeypatch.setattr(module, "bpy", sys.modules["bpy"])
    monkeypatch.setattr(module, "_pymupdf_version", lambda: "")
    return module


def recovered(page=1, codes=(2, 4, 5, 1), route="outline_identity"):
    return {
        "page_number": page, "font_name": "SampleGothic", "source_xref": 7,
        "status": "recovered", "route": route, "routes": {route: len(codes)},
        "reason": "", "glyphs": len(codes), "glyphs_recovered": len(codes),
        "raw_codes": list(codes), "raw_codes_truncated": False,
        "reference_faces": ["sample.ttf"], "bbox_pdf": [10.0, 90.0, 60.0, 104.0],
    }


def unproven(page=1, codes=(9, 9), bbox=(200.0, 90.0, 240.0, 104.0)):
    return {
        "page_number": page, "font_name": "SampleGothic", "source_xref": 7,
        "status": "unproven", "route": "", "routes": {},
        "reason": "no_reference_face_available",
        "detail": "no installed face matches 'samplegothic' (0 faces indexed)",
        "glyphs": len(codes), "glyphs_unproven": len(codes),
        "raw_codes": list(codes), "raw_codes_truncated": False,
        "looked_for_face": "samplegothic", "reference_faces": [],
        "bbox_pdf": list(bbox),
    }


def written(engine, tmp_path, stats):
    report_path = tmp_path / "D042_import_report.json"
    engine.write_import_report(
        str(tmp_path / "D042.pdf"), {"import_text": False}, stats,
        import_mode="vector", output_path=str(report_path),
    )
    return json.loads(report_path.read_text(encoding="utf-8"))


def base_stats():
    return {"pages_imported": 1, "primitives": 60}


def test_the_report_block_names_the_route_of_every_recovered_span(engine, tmp_path):
    stats = base_stats()
    engine._record_glyph_code_issues(stats, 1, [recovered(), recovered(route="embedded_cmap")])

    report = written(engine, tmp_path, stats)

    block = report["extra"]["text_glyph_codes"]
    assert block["schema"] == "bcs.text_glyph_codes/1.0"
    assert (block["spans_examined"], block["recovered"], block["unproven"]) == (2, 2, 0)
    assert block["spans_by_route"] == {"embedded_cmap": 1, "outline_identity": 1}
    assert report["result"]["warnings"] == 0


def test_unproven_spans_add_to_the_warning_sum_and_never_replace_it(engine, tmp_path):
    stats = base_stats()
    stats["clip_fill_delivery"] = engine._new_clip_fill_delivery()
    engine._record_clip_fill_issues(stats, 1, [{
        "seqno": 4, "reason": "nested", "action": "dropped-unsupported", "exact": False,
        "severity": "warning", "dropped": True, "detail": "SAMPLE",
        "paint_rect": [0, 0, 1, 1], "fill": [0, 0, 0], "fill_opacity": 1.0,
    }], None)
    baseline = written(engine, tmp_path, stats)["result"]["warnings"]
    assert baseline == 1

    engine._record_glyph_code_issues(stats, 1, [recovered(), unproven(), unproven(codes=(12,))])
    report = written(engine, tmp_path, stats)

    assert report["result"]["warnings"] == baseline + 2
    assert report["extra"]["clip_fill_delivery"]["dropped"] == 1
    block = report["extra"]["text_glyph_codes"]
    assert (block["recovered"], block["unproven"]) == (1, 2)
    assert block["by_reason"] == {"no_reference_face_available": 2}
    # Unproven spans sort first so the item cap never hides one.
    assert [item["status"] for item in block["items"]][:2] == ["unproven", "unproven"]
    assert block["items"][0]["raw_codes"] == [9, 9]
    assert "warnings_present" in report["extra"]["diagnostics"]["signals"]
    # An unproven span is reported, never a terminal failure.
    assert report["extra"]["result_status"] == "success"


def test_a_run_with_no_glyph_code_spans_writes_no_block(engine, tmp_path):
    report = written(engine, tmp_path, base_stats())

    assert "text_glyph_codes" not in report["extra"]
    assert report["result"]["warnings"] == 0


def test_the_tally_keeps_the_page_each_record_came_from(engine):
    stats = base_stats()
    engine._record_glyph_code_issues(stats, 1, [recovered()])
    engine._record_glyph_code_issues(stats, 2, [unproven(page=2)])
    engine._record_glyph_code_issues(stats, 2, [None, "not a record"])

    block = engine._glyph_code_delivery(stats)

    # Unproven first, so the item cap never hides one.
    assert [item["page"] for item in block["items"]] == [2, 1]
    assert block["pages"] == [1, 2]
    json.dumps(block, allow_nan=False)


def test_a_tally_restored_from_a_damaged_checkpoint_does_not_raise(engine, tmp_path):
    stats = base_stats()
    stats["text_glyph_codes"] = "not a block"
    engine._record_glyph_code_issues(stats, 1, [unproven()])

    report = written(engine, tmp_path, stats)

    assert report["extra"]["text_glyph_codes"]["unproven"] == 1


def test_the_tally_is_bounded_so_the_resume_checkpoint_does_not_grow_with_it(engine):
    # _current_resume_state copies every JSON-serialisable key of total_stats
    # into the checkpoint and rewrites it after every page. A sheet of nothing
    # but raw glyph codes produces one record per span, so what is kept is a
    # merged block capped like the clipped-fill tally beside it: the counts
    # stay exact, only the listed records are bounded.
    stats = base_stats()
    for page in range(1, 6):
        engine._record_glyph_code_issues(stats, page, [
            unproven(page=page, bbox=(float(index), 90.0, 240.0, 104.0))
            for index in range(300)
        ])

    block = engine._glyph_code_delivery(stats)

    assert block["unproven"] == 1500
    assert len(block["items"]) == engine._GLYPH_CODE_ISSUE_CAP
    assert block["items_truncated"] is True
    assert len(json.dumps(block)) < 200000


def test_one_operator_line_per_import_states_recovery_and_warns_on_unproven(engine):
    recovered_only = base_stats()
    engine._record_glyph_code_issues(recovered_only, 1, [recovered()])
    mixed = base_stats()
    engine._record_glyph_code_issues(mixed, 1, [recovered(), unproven()])

    clean_line = engine.summarize_glyph_code_block(
        engine._glyph_code_delivery(recovered_only),
        "See text_glyph_codes in the import report.",
    )
    mixed_line = engine.summarize_glyph_code_block(
        engine._glyph_code_delivery(mixed), "See text_glyph_codes in the import report."
    )

    assert "outline_identity" in clean_line and "could not be proven" not in clean_line
    assert "could not be proven" in mixed_line and "SampleGothic" in mixed_line
    assert "\n" not in mixed_line
    assert engine.summarize_glyph_code_block(engine._glyph_code_delivery({}), "") == ""


@pytest.fixture
def operators_module(monkeypatch: pytest.MonkeyPatch):
    fake_bpy = _install_blender_stubs(monkeypatch)
    props = types.ModuleType("bpy.props")
    for name in ("BoolProperty", "EnumProperty", "FloatProperty", "StringProperty"):
        setattr(props, name, lambda **_kwargs: None)
    io_utils = types.ModuleType("bpy_extras.io_utils")
    io_utils.ImportHelper = type("ImportHelper", (), {})
    bpy_extras = types.ModuleType("bpy_extras")
    bpy_extras.io_utils = io_utils
    monkeypatch.setitem(sys.modules, "bpy.props", props)
    monkeypatch.setitem(sys.modules, "bpy_extras", bpy_extras)
    monkeypatch.setitem(sys.modules, "bpy_extras.io_utils", io_utils)
    engine = importlib.import_module("pdf_vector_importer.bl_import_engine")
    previous = sys.modules.pop("pdf_vector_importer.operators", None)
    try:
        yield importlib.import_module("pdf_vector_importer.operators"), engine, fake_bpy
    finally:
        sys.modules.pop("pdf_vector_importer.operators", None)
        if previous is not None:
            sys.modules["pdf_vector_importer.operators"] = previous


@pytest.mark.parametrize("warning,expected", [
    ("1 text span(s) use an embedded font with no usable Unicode map; their "
     "characters could not be proven and are shown as the PDF's raw glyph codes "
     "(font SampleGothic, page 1).", 1),
    ("", 0),
])
def test_the_operator_tells_the_shop_user_what_the_report_says(
    monkeypatch: pytest.MonkeyPatch, operators_module, warning: str, expected: int
) -> None:
    # The add-on surfaces every sibling warning. A character matched against an
    # installed reference face rather than read from the file, or a span still
    # showing raw codes, is the one a shop user most needs to hear about.
    operators, engine, _fake_bpy = operators_module
    monkeypatch.setattr(engine, "import_pdf", lambda *_a, **_k: {
        "primitives": 12, "pages_imported": 1, "text_glyph_code_warning": warning,
        "import_report_path": "D042_import_report.json",
    })
    operator = operators.IMPORT_OT_pdf_vector()
    for name in ("mode", "pages", "text_mode", "visual_style", "page_arrangement", "model3d_mode"):
        setattr(operator, name, "")
    for name in ("show_advanced", "resume_interrupted", "import_text", "group_by_color",
                 "auto_focus_view", "keep_selection_after_focus", "auto_hide_default_cube"):
        setattr(operator, name, False)
    for name in ("line_z_offset_mm", "text_z_offset_mm", "image_z_offset_mm",
                 "page_gap_ratio", "model3d_depth_mm"):
        setattr(operator, name, 0.0)
    operator.filepath = "D042.pdf"
    reports = []
    operator.report = lambda level, message: reports.append((level, message))
    context = types.SimpleNamespace(
        preferences=types.SimpleNamespace(addons={}), workspace=None,
    )

    assert operator.execute(context) == {"FINISHED"}

    warnings = [message for level, message in reports if level == {"WARNING"}]
    assert len(warnings) == expected
    if expected:
        assert warnings[0].startswith(warning)
        assert "extra.text_glyph_codes in D042_import_report.json" in warnings[0]
    assert not [message for level, message in reports if level == {"ERROR"}]
