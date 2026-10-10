"""A font the PDF names but does not carry uses the installed face only when its widths match.

Fixtures are fictional (job D042, mark EX101) and built inside each test.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import types

import pytest

ARIAL = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf"
pytestmark = pytest.mark.skipif(
    not ARIAL.is_file(), reason="the installed Windows Arial face is not on this machine"
)

if "bpy" not in sys.modules:
    sys.modules["bpy"] = types.SimpleNamespace(
        types=types.SimpleNamespace(
            Collection=object,
            Material=object,
            Object=object,
            VectorFont=object,
        )
    )
if "bmesh" not in sys.modules:
    sys.modules["bmesh"] = types.SimpleNamespace()

import pymupdf as fitz  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402

from pdf_vector_importer import installed_font_route as route  # noqa: E402
from pdf_vector_importer.pdfcadcore import glyph_code_recovery as gcr  # noqa: E402
from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page  # noqa: E402

TEXT = "D042 EX101"


def _arial_widths():
    font = TTFont(str(ARIAL))
    try:
        upm = font["head"].unitsPerEm
        cmap = font.getBestCmap()
        hmtx = font["hmtx"].metrics
        return {
            code: round(hmtx[cmap[code]][0] * 1000 / upm)
            for code in range(32, 127)
        }
    finally:
        font.close()


def _arial_glyph_ids(text):
    font = TTFont(str(ARIAL))
    try:
        cmap = font.getBestCmap()
        return [font.getGlyphID(cmap[ord(char)]) for char in text]
    finally:
        font.close()


def _not_embedded_arial_pdf(path: Path, *, width_change=None, encoding=b"/WinAnsiEncoding"):
    widths = _arial_widths()
    if width_change:
        code, delta = width_change
        widths[code] += delta
    width_text = " ".join(str(widths[code]) for code in range(32, 127)).encode()
    content = b"BT /F1 14 Tf 50 300 Td (" + TEXT.encode() + b") Tj ET\n"
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 396]"
        b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
        b"<</Type/Font/Subtype/TrueType/BaseFont/Arial/FirstChar 32/LastChar 126"
        b"/Widths[" + width_text + b"]/Encoding " + encoding + b"/FontDescriptor 6 0 R>>",
        b"<</Length %d>>stream\n" % len(content) + content + b"\nendstream",
        b"<</Type/FontDescriptor/FontName/Arial/Flags 32/FontBBox[-665 -325 2000 1006]"
        b"/ItalicAngle 0/Ascent 905/Descent -212/CapHeight 716/StemV 80>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref,
    )
    path.write_bytes(bytes(out))
    return path


@pytest.fixture(autouse=True)
def _fresh_font_caches(monkeypatch):
    monkeypatch.delenv("BCS_GLYPH_REFERENCE_FONTS", raising=False)
    gcr.clear_reference_font_cache()
    route.clear_installed_face_cache()
    yield
    gcr.clear_reference_font_cache()
    route.clear_installed_face_cache()


def _attach(pdf: Path):
    document = fitz.open(str(pdf))
    page = document[0]
    page_data = extract_page(page, 1)
    original = list(page_data.text_items)
    stats = {}
    items = route.attach_installed_fonts(document, page, original, 1, stats)
    return document, original, items, stats


def _absence_proof(item):
    failure = item.font_failure
    return (
        failure.reason == "embedded_font_asset_build_failed"
        and failure.detail == "embedded font stream is empty"
        and failure.source_xref == 4
    )


def test_private_core_helpers_this_route_reads_still_exist():
    # The route reads these shared-core helpers without changing them. A core
    # sync that renames one must fail here, not silently disable the route.
    for name in ("_face_style", "_reference_faces_for", "_resolved_key",
                 "_parse_width_array", "_xref_key", "_descriptor_style",
                 "clear_reference_font_cache"):
        assert callable(getattr(gcr, name)), name
    assert gcr._parse_width_array("[32 [278 278 355]]") == {32: 278.0, 33: 278.0, 34: 355.0}


def test_matching_widths_attach_the_installed_face_with_its_own_glyph_ids(tmp_path):
    document, original, items, stats = _attach(_not_embedded_arial_pdf(tmp_path / "d042.pdf"))
    try:
        assert len(items) == len(original) == 1
        source, installed = original[0], items[0]
        # The extraction's absence proof is the starting point and stays intact.
        assert source.font_asset is None and _absence_proof(source)
        asset = installed.font_asset
        assert asset is not None and installed is not source
        assert asset.source_origin == route.INSTALLED_FONT_ORIGIN
        assert route.is_installed_font_asset(asset)
        assert os.path.normcase(asset.face_path) == os.path.normcase(str(ARIAL))
        assert asset.usable_format == "ttf"
        assert asset.page_number == 1 and asset.span_font_name == installed.font_name == "Arial"
        assert asset.usable_sha256 == asset.face_sha256 == asset.asset_id.split(":", 1)[1]
        assert asset.widths_checked == 95 and asset.worst_width_delta <= 1.0
        assert [layout.glyph_id for layout in installed.source_char_layout] == _arial_glyph_ids(TEXT)
        assert "".join(layout.text for layout in installed.source_char_layout) == TEXT
        # Placement truth comes from the PDF and is untouched.
        assert [layout.source_quad_pdf for layout in installed.source_char_layout] == [
            layout.source_quad_pdf for layout in source.source_char_layout
        ]
        assert _absence_proof(installed)
        assert route.installed_font_absence_item(installed) is source
        assert route.installed_font_absence_item(source) is None
        evidence = route.installed_font_evidence(installed)
        assert evidence["font_source"] == "installed"
        assert evidence["widths_matched"] == "95/95"
        assert evidence["installed_face_file"].lower() == "arial.ttf"
        assert stats["installed_font_items"] == 1
        assert stats["installed_font_checks"][0]["status"] == "used"
    finally:
        document.close()


def test_one_changed_width_keeps_todays_route_and_the_absence_proof(tmp_path):
    pdf = _not_embedded_arial_pdf(tmp_path / "d042_w.pdf", width_change=(ord("X"), 20))
    document, original, items, stats = _attach(pdf)
    try:
        assert items[0] is original[0]
        assert items[0].font_asset is None and _absence_proof(items[0])
        assert stats.get("installed_font_items", 0) == 0
        check = stats["installed_font_checks"][0]
        assert check["status"] == "not_used" and "'X'" in check["reason"]
    finally:
        document.close()


def test_a_differences_encoding_keeps_todays_route(tmp_path):
    pdf = _not_embedded_arial_pdf(
        tmp_path / "d042_diff.pdf",
        encoding=b"<</Type/Encoding/BaseEncoding/WinAnsiEncoding/Differences[69/X]>>",
    )
    document, original, items, stats = _attach(pdf)
    try:
        assert all(item.font_asset is None for item in items)
        assert items[0] is original[0]
        assert "encoding" in stats["installed_font_checks"][0]["reason"]
    finally:
        document.close()


def test_two_installed_faces_with_the_same_name_are_not_guessed_between(tmp_path, monkeypatch):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    shutil.copyfile(ARIAL, fonts / "arial.ttf")
    shutil.copyfile(ARIAL, fonts / "arial_copy.ttf")
    monkeypatch.setenv("BCS_GLYPH_REFERENCE_FONTS", str(fonts))
    gcr.clear_reference_font_cache()
    document, original, items, stats = _attach(_not_embedded_arial_pdf(tmp_path / "d042.pdf"))
    try:
        assert items[0] is original[0]
        assert "unique" in stats["installed_font_checks"][0]["reason"]
    finally:
        document.close()


def test_no_installed_face_keeps_todays_route(tmp_path, monkeypatch):
    monkeypatch.setenv("BCS_GLYPH_REFERENCE_FONTS", "none")
    gcr.clear_reference_font_cache()
    document, original, items, stats = _attach(_not_embedded_arial_pdf(tmp_path / "d042.pdf"))
    try:
        assert items[0] is original[0] and items[0].font_asset is None
        assert stats["installed_font_checks"][0]["status"] == "not_used"
    finally:
        document.close()


def test_an_embedded_font_is_never_replaced(tmp_path):
    pdf = tmp_path / "d042_embedded.pdf"
    document = fitz.open()
    page = document.new_page(width=612, height=396)
    page.insert_font(fontname="F0", fontfile=str(ARIAL))
    page.insert_text((50, 96), TEXT, fontname="F0", fontsize=14)
    document.save(str(pdf), garbage=3, deflate=True)
    document.close()
    document, original, items, stats = _attach(pdf)
    try:
        assert [item is source for item, source in zip(items, original, strict=True)] == [True] * len(items)
        assert all(not route.is_installed_font_asset(item.font_asset) for item in items)
        assert stats == {}
    finally:
        document.close()


# ── Blender side: an installed-face attempt that does not verify steps back ──


def _builder():
    from pdf_vector_importer import bl_text_builder

    return bl_text_builder


def test_installed_attempt_that_fails_is_cleaned_up_and_retried_on_the_original(tmp_path, monkeypatch):
    builder = _builder()
    from pdf_vector_importer.text_delivery import AttemptOutcome

    document, original, items, _stats = _attach(_not_embedded_arial_pdf(tmp_path / "d042.pdf"))
    document.close()
    installed, source = items[0], original[0]
    seen = []
    cleaned = []

    def fake_once(representation, text_item, collection, **_options):
        seen.append((representation, text_item is installed))
        if text_item is installed:
            return AttemptOutcome.failed(
                "requested_font_representation_visual_verification_failed",
                evidence={"failures": ["evaluated_font_advance_axis_mismatch"]},
                owned_objects=("candidate",),
            )
        return AttemptOutcome.impossible(
            "exact_source_font_unavailable_for_item", evidence={"item": "original"}
        )

    def fake_cleanup(outcome, _collection):
        cleaned.append(tuple(outcome.owned_objects))
        return {"status": "complete", "removed": ["candidate"]}

    monkeypatch.setattr(builder, "_attempt_one_representation_once", fake_once)
    monkeypatch.setattr(builder, "_cleanup_attempt", fake_cleanup)
    options = dict(effective_page=1, requested="text", item_id="page:1:text:1",
                   visual_style="source", z_offset_m=0.0, terminal_raster_callback=None)
    outcome = builder._attempt_one_representation("text", installed, None, **options)
    assert seen == [("text", True), ("text", False)]
    assert cleaned == [("candidate",)]
    assert outcome.status == "impossible" and outcome.evidence["item"] == "original"
    attempt = outcome.evidence["installed_font_attempt"]
    assert attempt["failures"] == ["evaluated_font_advance_axis_mismatch"]
    assert attempt["cleanup"]["status"] == "complete"

    # A rung that draws no font (raster) always uses the original item.
    seen.clear()
    builder._attempt_one_representation("raster", installed, None, **options)
    assert seen == [("raster", False)]
    assert source is route.installed_font_absence_item(installed)


def test_installed_attempt_that_verifies_is_kept(tmp_path, monkeypatch):
    builder = _builder()
    from pdf_vector_importer.text_delivery import AttemptOutcome

    document, _original, items, _stats = _attach(_not_embedded_arial_pdf(tmp_path / "d042.pdf"))
    document.close()
    delivered = AttemptOutcome.delivered(object(), entity_ids=("P1_text_text_1",),
                                         evidence={"font_source": "installed"})
    calls = []

    def fake_once(representation, text_item, collection, **_options):
        calls.append(text_item)
        return delivered

    monkeypatch.setattr(builder, "_attempt_one_representation_once", fake_once)
    outcome = builder._attempt_one_representation(
        "3d_text", items[0], None, effective_page=1, requested="3d_text",
        item_id="page:1:text:1", visual_style="source", z_offset_m=0.0,
        terminal_raster_callback=None,
    )
    assert outcome is delivered and calls == [items[0]]


def test_font_evidence_names_the_installed_face_and_the_absence_proof(tmp_path):
    builder = _builder()
    document, _original, items, _stats = _attach(_not_embedded_arial_pdf(tmp_path / "d042.pdf"))
    document.close()
    evidence = builder._font_asset_evidence(items[0])
    assert evidence["font_source"] == "installed"
    assert evidence["widths_matched"] == "95/95"
    assert evidence["source_font_absence"]["detail"] == "embedded font stream is empty"
    assert evidence["font_name"] == "Arial"


def test_report_block_counts_installed_deliveries_and_step_backs():
    stats = {"installed_font_items": 2, "installed_font_checks": [{"status": "used"}]}
    records = [
        {"status": "delivered", "attempts": [{"evidence": {"font_source": "installed"}}]},
        {"status": "delivered", "attempts": [
            {"evidence": {"installed_font_attempt": {"status": "failed"}}},
        ]},
    ]
    block = route.installed_font_report(stats, records)
    assert block["items_with_installed_font"] == 2
    assert block["items_delivered_with_installed_font"] == 1
    assert block["items_back_on_previous_route"] == 1
    assert route.installed_font_report({}, []) is None


def test_a_large_face_is_decoded_once_per_page_for_glyph_ink_checks(monkeypatch):
    # Glyphs and Geometry check each glyph's ink. An installed Arial has more
    # glyphs than the prefetch limit, so it is checked glyph by glyph; decoding
    # its whole glyph table once per glyph made Glyphs mode slow.
    from hashlib import sha256
    import fontTools.ttLib as ttlib

    builder = _builder()
    data = ARIAL.read_bytes()
    asset = types.SimpleNamespace(usable_sha256=sha256(data).hexdigest(), usable_bytes=data)
    real = ttlib.TTFont
    opened = []

    def counting_ttfont(*args, **kwargs):
        opened.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(ttlib, "TTFont", counting_ttfont)
    builder._FONT_GLYPH_INK_CACHE.clear()
    builder._release_large_font_glyph_sets()
    try:
        glyph_e, glyph_x, glyph_space = _arial_glyph_ids("EX ")
        assert builder._exact_font_glyph_has_visible_ink(asset, glyph_e) is True
        assert builder._exact_font_glyph_has_visible_ink(asset, glyph_x) is True
        assert builder._exact_font_glyph_has_visible_ink(asset, glyph_space) is False
        assert len(opened) == 1
    finally:
        builder._release_large_font_glyph_sets()
        builder._FONT_GLYPH_INK_CACHE.clear()
    assert builder._LARGE_FONT_GLYPH_SETS == {}


def test_engine_offers_installed_faces_only_to_font_drawing_modes(monkeypatch):
    from pdf_vector_importer import bl_import_engine as engine

    calls = []

    def fake_attach(document, page, items, page_number, stats):
        calls.append(page_number)
        return ["installed copy"]

    monkeypatch.setattr(route, "attach_installed_fonts", fake_attach)
    for mode in ("labels", "text", "3d_text", "glyphs", "geometry"):
        assert engine._with_installed_fonts(None, None, ["item"], 1, mode, {}) == ["installed copy"]
    assert engine._with_installed_fonts(None, None, ["item"], 1, "raster", {}) == ["item"]
    assert calls == [1, 1, 1, 1, 1]

    def broken_attach(*_args):
        raise KeyError("unexpected PDF shape")

    monkeypatch.setattr(route, "attach_installed_fonts", broken_attach)
    stats = {}
    assert engine._with_installed_fonts(None, None, ["item"], 3, "text", stats) == ["item"]
    assert stats["installed_font_checks"] == [{
        "page": 3, "status": "not_used",
        "reason": "the installed-font check could not finish (KeyError)",
    }]
