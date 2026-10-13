"""The real-Blender CI matrix: fictional fixtures, pass/fail rules, workflow wiring.

blender-host-matrix.yml imports PDFs made by scripts/make_ci_fixture.py inside
real Blender and judges them with scripts/_headless_import_test.py. These
tests pin what those PDFs contain and how a result is judged, so the CI legs
keep testing what their step names promise.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace
from hashlib import sha256

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fixture_maker():
    return _load_script("make_ci_fixture")


@pytest.fixture(scope="module")
def headless():
    return _load_script("_headless_import_test")


def _spans(page):
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            yield from line["spans"]


def test_standard_fixture_has_two_sheet_sizes_a_picture_and_edge_text(tmp_path, fixture_maker):
    out = tmp_path / "ci.pdf"
    assert fixture_maker.main([str(out)]) == 0

    doc = pymupdf.open(out)
    try:
        assert doc.page_count == 2
        sizes = {(round(p.rect.width), round(p.rect.height)) for p in doc}
        assert sizes == {(612, 792), (1224, 792)}
        assert sum(len(p.get_images(full=True)) for p in doc) == 1

        for page in doc:
            fonts = page.get_fonts(full=True)
            assert fonts, "every sheet carries text"
            # (xref, ext, type, basefont, ...): ext 'n/a' = font program not embedded
            assert all(f[3] == "Helvetica" and f[1] == "n/a" for f in fonts)

        sheet2 = doc[1]
        crossing = [s for s in _spans(sheet2) if s["bbox"][3] > sheet2.rect.height]
        assert [s["text"] for s in crossing] == ["EDGE BOTTOM EX102"]

        text = "".join(p.get_text() for p in doc)
        assert "D042" in text and "EX101" in text and "EX102" in text
    finally:
        doc.close()


def test_edge_far_fixture_moves_the_edge_text_sheet_far_down(tmp_path, fixture_maker):
    out = tmp_path / "far.pdf"
    assert fixture_maker.main([str(out), "--variant", "edge-far"]) == 0

    doc = pymupdf.open(out)
    try:
        assert doc.page_count == 2
        # Sheet 1 is tall enough to push sheet 2 several metres down the stack.
        assert doc[0].rect.height * 25.4 / 72 / 1000 > 4.5
        crossing = [s for s in _spans(doc[1]) if s["bbox"][1] < 0]
        assert [s["text"] for s in crossing] == ["EDGE TOP EX102"]
    finally:
        doc.close()


def test_cid_cff_fixture_embeds_a_public_name_keyed_program(tmp_path, fixture_maker):
    from io import BytesIO
    from fontTools.cffLib import CFFFontSet
    out = tmp_path / "cid.pdf"
    assert fixture_maker.main([str(out), "--variant", "cid-cff"]) == 0
    with pymupdf.open(out) as doc:
        assert len(doc) == 1
        font = doc[0].get_fonts(full=True)[0]
        assert font[1:3] == ("cid", "Type0")
        source = CFFFontSet()
        source.decompile(BytesIO(doc.extract_font(font[0])[3]), None, isCFF2=False)
        assert len(source.topDictIndex) == 1
        assert not {"ROS", "FDArray", "FDSelect"} & source.topDictIndex[0].rawDict.keys()
        assert doc[0].get_text().strip() == "BLENDER CONTROL"


def _summary(**overrides):
    summary = {
        "outcome": "returned",
        "stats": {"pages_imported": 2, "primitives": 7, "images": 1, "text_items": 4,
                  "text_delivery_failed_items": 0},
        "objects_per_page": {1: 54, 2: 41},
    }
    summary.update(overrides)
    return summary


def test_complete_two_sheet_import_passes(headless):
    assert headless._checks(_summary(), 2, 1) == []


def test_missing_sheet_is_named(headless):
    summary = _summary(
        outcome="raised", exception_type="IncompleteImportError", exception="incomplete",
        stats={"pages_imported": 1, "primitives": 3, "images": 0, "text_items": 1,
               "text_delivery_failed_items": 2},
        objects_per_page={1: 3},
    )
    problems = headless._checks(summary, 2, None)
    assert any("pages_imported=1 (expected 2)" in p for p in problems)
    assert any("sheet 2 has no objects" in p for p in problems)
    assert any("text_delivery_failed_items=2" in p for p in problems)
    assert any(p.startswith("import raised IncompleteImportError") for p in problems)


def test_factory_camera_and_cube_do_not_count_as_imported(headless):
    # Only objects inside PDF_Page_N collections count; Blender's own startup
    # camera, light and cube must not make an empty import look successful.
    summary = _summary(stats={"pages_imported": 0, "primitives": 0, "images": 0}, objects_per_page={})
    problems = headless._checks(summary, None, None)
    assert "no objects were created on any sheet" in problems


def _editable_summary():
    summary = _summary()
    summary["native_text_delivery"] = {
        "summary": {"source_items": 1, "delivered_items": 1, "fallback_items": 0,
                    "failed_items": 0, "final_counts": {"3d_text": 1}},
        "items": [{"status": "delivered", "final_representation": "3d_text", "native_entities": [
            {"type": "FONT", "extrude": .001, "packed_font_sha_verified": True}]}],
    }
    return summary


def test_requested_native_3d_text_with_verified_font_passes(headless):
    assert headless._checks(_editable_summary(), 2, 1, "3d_text") == []


@pytest.mark.parametrize("bad", [
    {"type": "MESH"}, {"type": None}, {"extrude": 0}, {"extrude": float("nan")},
    {"packed_font_sha_verified": False},
])
def test_generic_completion_cannot_replace_real_editable_3d_text(headless, bad):
    summary = deepcopy(_editable_summary())
    summary["native_text_delivery"]["items"][0]["native_entities"][0].update(bad)
    assert headless._checks(summary, 2, 1, "3d_text")


def test_raster_fallback_cannot_pass_editable_text_contract(headless):
    summary = _editable_summary()
    summary["native_text_delivery"]["summary"].update(final_counts={"raster": 1}, fallback_items=1)
    summary["native_text_delivery"]["items"][0]["final_representation"] = "raster"
    assert headless._checks(summary, 2, 1, "3d_text")


def test_native_entity_capture_checks_the_actual_packed_font_bytes(headless):
    class FontObject(dict):
        type = "FONT"
        data = SimpleNamespace(extrude=.001, font=SimpleNamespace(packed_file=SimpleNamespace(data=b"font")))
    obj = FontObject(pdf_exact_font_sha256=sha256(b"font").hexdigest())
    bpy = SimpleNamespace(data=SimpleNamespace(objects={"text": obj}))
    report = {"extra": {"text_delivery": {"items": [{"entity_ids": ["text"], "status": "delivered", "final_representation": "3d_text"}]}}}
    captured = headless._native_text_delivery(report, bpy)
    assert captured["items"][0]["native_entities"][0]["packed_font_sha_verified"] is True
    obj["pdf_exact_font_sha256"] = "wrong"
    assert headless._native_text_delivery(report, bpy)["items"][0]["native_entities"][0]["packed_font_sha_verified"] is False


def test_host_matrix_workflow_covers_every_release_line():
    text = (ROOT / ".github" / "workflows" / "blender-host-matrix.yml").read_text(encoding="utf-8")
    for version in ("3.1.2", "3.6.", "4.2.", "4.5.", "5.2."):
        assert f'"{version}' in text, version
    for mode in ("TEST_TEXT_MODE: text", "TEST_TEXT_MODE: 3d_text", "TEST_TEXT_MODE: glyphs"):
        assert mode in text
    assert "NATIVE_PROPERTIES_PASS" in text
    assert 'TEST_EXPECT_PAGES: "2"' in text
    # BL-1010-keep-every-sheet has landed: the far-edge import is a strict
    # check now, not a known failure that stays green whatever happens.
    assert "TEST_EXPECT_FAIL" not in text
    assert "XFAIL" not in text
    assert 'TEST_PDF="$CI_FIXTURES/ci_edge_far.pdf"' in text
    assert text.count('grep -q "HEADLESS_IMPORT_PASS"') == 5
    assert 'TEST_PDF="$CI_FIXTURES/ci_cid_cff.pdf"' in text
    assert 'TEST_EXPECT_TEXT_REPRESENTATION: 3d_text' in text
    # The audit switch is BC_PDF_AUDIT; the placeholder name is gone.
    assert 'BC_PDF_AUDIT: "1"' in text
    assert "BC_BL_AUDIT_IMPORT" not in text
    assert "TEST_ADDON_SOURCE: installed" in text
    assert "workflow_dispatch:" in text and "schedule:" in text


def test_unit_ci_runs_python_313_for_blender_5():
    text = (ROOT / ".github" / "workflows" / "bl-pdfimporter-ci.yml").read_text(encoding="utf-8")
    assert '"3.13"' in text
    assert "Blender 3.0/3.1 hosts (Python 3.9)" not in text
