"""Original renderer character programs disambiguate same-name PDF subsets."""
from collections import deque
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import pymupdf as fitz
import pytest
from pathlib import Path
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from pdf_vector_importer.pdfcadcore.embedded_fonts import (
    EmbeddedFontCatalog, EmbeddedFontFailure, source_control_zero_ink_proof,
)
from pdf_vector_importer.pdfcadcore.primitive_extractor import (
    _raw_text_with_source_quads, _source_character_font_digest,
    _source_font_occurrence_keys, extract_page,
)


DETERMINISTIC_FONT_NAME = "BCS Deterministic Test"


def _draw_box(*, top: int, right: int = 550, notch: int = 0) -> object:
    pen = TTGlyphPen(None)
    pen.moveTo((50, 0))
    pen.lineTo((right, 0))
    pen.lineTo((right, top))
    pen.lineTo((50 + notch, top))
    if notch:
        pen.lineTo((50, top - notch))
    else:
        pen.lineTo((50, top))
    pen.closePath()
    return pen.glyph()


def _build_deterministic_test_font(path: Path) -> None:
    glyph_order = [".notdef", "space"]
    cmap = {32: "space"}
    codepoints = [*range(33, 127), 0x1F642]
    for codepoint in codepoints:
        glyph_name = (
            f"uni{codepoint:04X}" if codepoint <= 0xFFFF else f"u{codepoint:05X}"
        )
        glyph_order.append(glyph_name)
        cmap[codepoint] = glyph_name

    glyphs = {".notdef": _draw_box(top=700), "space": TTGlyphPen(None).glyph()}
    for codepoint, glyph_name in cmap.items():
        if glyph_name != "space":
            glyphs[glyph_name] = _draw_box(
                top=500 if codepoint == ord("x") else 700,
                right=430 + (codepoint % 10) * 11,
                notch=18 + (codepoint % 7) * 4,
            )

    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap(cmap)
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 50) for name in glyph_order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
        sxHeight=500,
        sCapHeight=700,
    )
    builder.setupNameTable(
        {
            "familyName": DETERMINISTIC_FONT_NAME,
            "styleName": "Regular",
            "uniqueFontIdentifier": "BCS-Deterministic-Test-Regular-1.0",
            "fullName": f"{DETERMINISTIC_FONT_NAME} Regular",
            "psName": "BCSDeterministicTest-Regular",
            "version": "Version 1.0",
        }
    )
    builder.setupPost()
    builder.setupMaxp()
    builder.font.recalcTimestamp = False
    builder.font["head"].created = 2_082_844_800
    builder.font["head"].modified = 2_082_844_800
    builder.save(str(path))



@pytest.fixture
def deterministic_exact_font(tmp_path):
    path = tmp_path / "source-program-test.ttf"
    _build_deterministic_test_font(path)
    return path


def subset_pdf(font_path):
    pdf = fitz.open()
    page = pdf.new_page(width=240, height=160)
    for resource, visible, hidden, origin in (
        ("F1", "A", "B", (20, 40)), ("F2", "B", "A", (20, 90)),
    ):
        font = TTFont(font_path)
        missing = font.getBestCmap()[ord(hidden)]
        font["glyf"][missing] = TTGlyphPen(None).glyph()
        font["hmtx"].metrics[missing] = (0, 0)
        output = BytesIO()
        font.save(output)
        font.close()
        page.insert_font(fontname=resource, fontbuffer=output.getvalue())
        page.insert_text(origin, visible * 2, fontname=resource, fontsize=16)
    return pdf


def test_truncated_hmtx_sidebearings_still_bind(deterministic_exact_font):
    """A PDF subset that omits the hmtx side-bearing tail must still become text."""
    import struct

    source = TTFont(deterministic_exact_font)
    source["hhea"].numberOfHMetrics = 2
    saved = BytesIO()
    source.save(saved)
    source.close()
    blob = bytearray(saved.getvalue())
    table_count = struct.unpack_from(">H", blob, 4)[0]
    for index in range(table_count):
        offset = 12 + index * 16
        tag, _checksum, _table_offset, _length = struct.unpack_from(">4sIII", blob, offset)
        if tag == b"hmtx":
            struct.pack_into(">I", blob, offset + 12, 8)
            break
    else:
        raise AssertionError("hmtx table missing")
    broken = bytes(blob)
    from pdf_vector_importer.pdfcadcore.embedded_fonts import (
        _fonttools_loadable,
        _repair_truncated_hmtx,
    )

    assert _fonttools_loadable(broken) is False
    repaired = _repair_truncated_hmtx(broken)
    assert repaired != broken
    assert _fonttools_loadable(repaired) is True

    pdf = fitz.open()
    page = pdf.new_page(width=200, height=80)
    page.insert_font(fontname="F1", fontbuffer=broken)
    page.insert_text((20, 50), "AB", fontname="F1", fontsize=18)
    catalog = EmbeddedFontCatalog.from_page(page, 1)
    assert catalog.failures == ()
    assert catalog.assets
    items = extract_page(page, 1, detect_arcs=False).text_items
    assert any(item.text.strip() == "AB" and item.font_asset is not None for item in items)
    pdf.close()


@pytest.mark.parametrize("in_form", [False, True])
def test_complementary_subsets_bind_actual_character_programs(deterministic_exact_font, in_form):
    source = subset_pdf(deterministic_exact_font)
    pdf = fitz.open()
    if in_form:
        page = pdf.new_page(width=400, height=200)
        page.show_pdf_page(fitz.Rect(20, 10, 260, 170), source, 0)
    else:
        pdf.insert_pdf(source)
        page = pdf[0]
    catalog = EmbeddedFontCatalog.from_page(page, 1)
    raw = _raw_text_with_source_quads(page)
    items = extract_page(page, 1).text_items
    assert len(items) == 2
    assert len({item.font_name for item in items}) == 1
    assert catalog.for_span(items[0].font_name) is None
    assert catalog.failure_for_span(items[0].font_name).reason == "ambiguous_exact_embedded_font_match"
    expected = {
        sha256(pdf.extract_font(rec[0])[3]).hexdigest(): rec[0]
        for rec in page.get_fonts(full=True)
    }
    assert len(expected) == 2
    for item in items:
        asset = item.font_asset
        assert item.font_failure is None
        assert asset.source_xref == expected[asset.source_sha256]
        assert asset.source_binding_method == "original_textpage_character_program_sha256"
        assert asset.source_program_candidates == ((asset.source_xref, asset.resource_name),)
        assert len(item.source_char_layout) == 2
        assert all(c.source_font_binding_verified for c in item.source_char_layout)
        assert all(c.source_font_program_sha256 == asset.source_sha256 for c in item.source_char_layout)
        assert all(c.source_font_character_codepoint == ord(item.text[0]) for c in item.source_char_layout)
        font = TTFont(BytesIO(asset.usable_bytes))
        assert font["glyf"][font.getBestCmap()[ord(item.text[0])]].numberOfContours > 0
        font.close()
    # Copying a borrowed font buffer must not empty or change renderer state.
    assert _raw_text_with_source_quads(page) == raw
    assert page.get_pixmap().samples == page.get_pixmap().samples
    pdf.close()
    source.close()


def test_failed_font_build_is_not_reported_as_missing_from_the_pdf():
    failure = EmbeddedFontFailure(
        1, "Nimbus", "embedded_font_asset_build_failed", 4,
        "ExactFontSourceImpossible", "unsupported embedded font format: pfa",
        "source_specific_impossibility",
    )
    catalog = EmbeddedFontCatalog(1, {}, {"Nimbus": failure},
                                  program_failures=[("ab" * 32, failure)])
    asset, got = catalog.resolve_span("Nimbus", [{
        "source_font_program_sha256": "ab" * 32,
        "source_font_binding_verified": True,
    }])
    assert asset is None
    assert got == failure


def test_mixed_partial_and_wrong_program_spans_fail_closed(deterministic_exact_font):
    pdf = subset_pdf(deterministic_exact_font)
    page = pdf[0]
    catalog = EmbeddedFontCatalog.from_page(page, 1)
    spans = [s for b in _raw_text_with_source_quads(page)["blocks"]
             for line in b.get("lines", ()) for s in line["spans"]]
    name = spans[0]["font"]
    a, b = spans[0]["chars"][0], spans[1]["chars"][0]
    for chars, reason in (
        ([a, b], "mixed_source_font_programs_in_span"),
        ([a, {**a, "source_font_program_sha256": ""}], "incomplete_source_font_character_binding"),
        ([{**a, "source_font_binding_verified": False}], "incomplete_source_font_character_binding"),
        ([{**a, "source_font_program_sha256": "0" * 64}], "source_character_font_program_not_in_pdf_inventory"),
    ):
        asset, failure = catalog.resolve_span(name, chars)
        assert asset is None
        assert failure.reason == reason
    assert catalog.resolve_span(name, [{}])[0] is None
    pdf.close()


def test_identical_program_resources_report_all_candidates(deterministic_exact_font):
    pdf = subset_pdf(deterministic_exact_font)
    page = pdf[0]
    original = EmbeddedFontCatalog.from_page(page, 1)
    asset = original._candidates[0]
    catalog = EmbeddedFontCatalog(1, {}, {}, [asset, replace(asset, source_xref=999, resource_name="Another")])
    char = {"source_font_program_sha256": asset.source_sha256, "source_font_binding_verified": True}
    bound, failure = catalog.resolve_span(asset.span_font_name, [char])
    assert failure is None
    assert bound.source_program_candidates == tuple(sorted(((asset.source_xref, asset.resource_name), (999, "Another"))))
    pdf.close()


def test_coincident_character_occurrences_require_complete_same_program_census():
    key = ("A", 1.25, 2.5)
    raw = {"blocks": [{"type": 0, "lines": [{"spans": [{"chars": [
        {"c": "A", "origin": (1.25, 2.5)}, {"c": "A", "origin": (1.25, 2.5)}]}]}]}]}
    def queue(*digests):
        return {key: deque((None, {"source_font_program_sha256": d}) for d in digests)}
    assert _source_font_occurrence_keys(raw, queue("a", "a")) == {key}
    for values in (("a", "b"), ("a",), ("a", "a", "a"), ("", "")):
        assert _source_font_occurrence_keys(raw, queue(*values)) == set()


def test_optional_buffer_copy_api_and_work_bound_are_fail_closed(monkeypatch):
    from pdf_vector_importer.pdfcadcore import embedded_fonts
    buffer = SimpleNamespace(this=123, len=4)
    font = SimpleNamespace(buffer=buffer)
    calls = []
    api = SimpleNamespace(ll_fz_buffer_extract_copy=lambda b: calls.append(b) or b"font")
    cache = {}
    assert _source_character_font_digest(font, api, cache) == sha256(b"font").hexdigest()
    assert _source_character_font_digest(font, api, cache) == sha256(b"font").hexdigest()
    assert calls == [buffer]
    assert _source_character_font_digest(font, SimpleNamespace(), {}) == ""
    monkeypatch.setattr(embedded_fonts, "MAX_EMBEDDED_FONT_BYTES", 3)
    assert _source_character_font_digest(font, api, {}) == ""
    assert calls == [buffer]


def test_visible_annotation_font_is_bound_to_its_own_appearance_resource():
    pdf = fitz.open()
    page = pdf.new_page(width=240, height=160)
    page.insert_text((20, 30), "Page", fontname="cour")
    annotation = page.add_freetext_annot(fitz.Rect(20, 50, 180, 100), "NOTE", fontsize=16, fontname="helv")
    annotation.update()
    # Reopen original bytes so normal annotation appearance is finalized.
    original = pdf.tobytes()
    pdf.close()
    with fitz.open(stream=original, filetype="pdf") as document:
        page = document[0]
        def objects():
            return tuple((document.xref_object(i), document.xref_stream_raw(i))
                         for i in range(1, document.xref_length()))
        original_objects = objects()
        assert not any(rec[3] == "Helvetica" for rec in page.get_fonts(full=True))
        catalog = EmbeddedFontCatalog.from_page(page, 1)
        assert catalog.for_span("Helvetica") is None
        item = next(t for t in extract_page(page, 1).text_items if t.text == "NOTE")
        assert item.font_failure is None
        asset = item.font_asset
        assert asset.source_origin == "pdf_base14_renderer_font"
        assert document.extract_font(asset.source_xref)[0] == "Helvetica"
        assert asset.source_binding_method == "original_textpage_character_program_sha256"
        assert all(c.source_font_program_sha256 == asset.source_sha256 for c in item.source_char_layout)
        assert all(c.source_font_binding_verified for c in item.source_char_layout)
        assert objects() == original_objects


def control_item():
    pdf = fitz.open()
    page = pdf.new_page(width=160, height=120)
    page.insert_text((30, 30), "A", fontname="cour", fontsize=11)
    page.insert_text((30, 60), "X", fontname="cour", fontsize=11)
    pdf.update_stream(page.get_contents()[-1], b"BT /cour 11 Tf 30 60 Td <0a> Tj ET")
    item = next(t for t in extract_page(page, 1).text_items if t.text == "\n")
    pdf.close()
    return item


def test_unencoded_control_keeps_original_semantics_and_exact_empty_glyph_proof():
    item = control_item()
    char = item.source_char_layout[0]
    assert char.source_font_character_codepoint == 10
    assert char.source_glyph_trace_codepoint == 0xFFFD
    assert char.glyph_id == 0
    proof = source_control_zero_ink_proof(item)
    assert proof["glyph_bounds"] is None and proof["original_glyph_bounds"] is None
    assert proof["characters"] == [{"codepoint": 10, "trace_codepoint": 65533,
        "origin_pdf": list(char.source_origin_pdf), "advance_width": char.advance_width}]
    assert proof["source_program_sha256"] == item.font_asset.source_sha256
    for mutation in (
        {"glyph_id": None}, {"glyph_id": 1}, {"source_glyph_trace_codepoint": None},
        {"source_font_binding_verified": False}, {"source_font_program_sha256": "0" * 64},
    ):
        assert source_control_zero_ink_proof(replace(item, source_char_layout=(replace(char, **mutation),))) is None
    assert source_control_zero_ink_proof(replace(item, text=" ")) is None


@pytest.mark.parametrize("mutated", ["original", "usable"])
def test_visible_notdef_cannot_be_certified_as_zero_ink(mutated, deterministic_exact_font):
    item = control_item()
    font = TTFont(deterministic_exact_font)
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0)); pen.lineTo((500, 0)); pen.lineTo((500, 700)); pen.closePath()
    font["glyf"][font.getGlyphOrder()[0]] = pen.glyph()
    output = BytesIO()
    font.save(output)
    font.close()
    data = output.getvalue()
    digest = sha256(data).hexdigest()
    if mutated == "original":
        asset = replace(item.font_asset, source_bytes=data, source_sha256=digest, source_format="ttf")
        item = replace(item, font_asset=asset, source_char_layout=tuple(
            replace(c, source_font_program_sha256=digest) for c in item.source_char_layout))
    else:
        item = replace(item, font_asset=replace(item.font_asset, usable_bytes=data, usable_sha256=digest))
    assert source_control_zero_ink_proof(item) is None


def test_nonembedded_custom_font_keeps_original_absence_proof():
    with fitz.open() as pdf:
        page = pdf.new_page(width=160, height=120)
        page.insert_text((20, 40), "Example", fontname="helv")
        font_xref = page.get_fonts()[0][0]
        pdf.xref_set_key(font_xref, "BaseFont", "/UnembeddedFixtureSans")
        original = pdf.tobytes()
    with fitz.open(stream=original, filetype="pdf") as pdf:
        item = extract_page(pdf[0], 1).text_items[0]
        # MuPDF really did substitute a renderer program, but it is not an
        # embedded source program and must not erase the PDF absence proof.
        assert all(c.source_font_program_sha256 for c in item.source_char_layout)
        assert item.font_asset is None
        failure = item.font_failure
        assert failure.source_xref == font_xref
        assert failure.span_font_name == "UnembeddedFixtureSans"
        assert failure.reason == "embedded_font_asset_build_failed"
        assert failure.detail == "embedded font stream is empty"
        assert failure.proof_category == "source_specific_impossibility"


@pytest.mark.parametrize("in_form", [False, True])
def test_same_unicode_different_glyph_ids_bind_each_actual_program(deterministic_exact_font, in_form):
    from fontTools.ttLib.reorderGlyphs import reorderGlyphs

    source = fitz.open()
    page = source.new_page(width=240, height=160)
    for index, resource in enumerate(("F1", "F2")):
        font = TTFont(deterministic_exact_font)
        if index:
            order = font.getGlyphOrder()
            a = order.index(font.getBestCmap()[ord("A")])
            b = order.index(font.getBestCmap()[ord("B")])
            order[a], order[b] = order[b], order[a]
            reorderGlyphs(font, order)
        program = BytesIO()
        font.save(program)
        font.close()
        page.insert_font(fontname=resource, fontbuffer=program.getvalue())
        page.insert_text((20, 40 + 40 * index), "A", fontname=resource, fontsize=16)
    pdf = fitz.open()
    if in_form:
        page = pdf.new_page(width=300, height=200)
        page.show_pdf_page(fitz.Rect(10, 10, 250, 170), source, 0)
    else:
        pdf.insert_pdf(source)
        page = pdf[0]
    traces = page.get_texttrace()
    expected_glyphs = [span["chars"][0][1] for span in traces]
    assert len(set(expected_glyphs)) == 2
    assert len({span["font"] for span in traces}) == 1
    items = extract_page(page, 1).text_items
    assert len(items) == 2
    assert all(item.font_failure is None for item in items)
    assert len({item.font_asset.source_sha256 for item in items}) == 2
    assert [item.source_char_layout[0].glyph_id for item in items] == expected_glyphs
    for item in items:
        asset = item.font_asset
        char = item.source_char_layout[0]
        assert char.source_font_program_sha256 == asset.source_sha256
        assert char.source_font_binding_verified
        with TTFont(BytesIO(asset.usable_bytes)) as font:
            assert font.getGlyphID(font.getBestCmap()[ord("A")]) == char.glyph_id
    source.close()
    pdf.close()


def test_partial_trace_census_never_borrows_another_same_name_program_glyph():
    from pdfcadcore.embedded_fonts import _page_program_glyph_maps
    from pdfcadcore.primitive_extractor import _character_layout, _trace_glyph_queues

    def char(text, x, digest):
        return {"c": text, "origin": (x, 0), "bbox": (x, 0, x + 2, 10),
                "quad": ((x, 0), (x + 2, 0), (x + 2, 10), (x, 10)),
                "source_font_program_sha256": digest,
                "source_font_binding_verified": True,
                "source_font_character_codepoint": ord(text)}

    unresolved = char("A", 10, "a" * 64)
    proven = char("B", 20, "a" * 64)
    other = char("A", 0, "b" * 64)
    span = {"font": "Same", "chars": [unresolved, proven, other]}
    raw = {"blocks": [{"type": 0, "lines": [{"spans": [span]}]}]}
    # Glyphs 3 and 7 at the same origin disagree, so this letter stays
    # unbound. Glyph 8 belongs to the other program and must not be borrowed.
    trace = {"font": "Same", "chars": [(65, 8, (0, 0)), (65, 3, (10, 0)),
                                             (65, 7, (10, 0)), (66, 4, (20, 0))]}
    page = SimpleNamespace(get_texttrace=lambda: [trace])
    maps, ambiguous = _page_program_glyph_maps(page, raw)
    assert maps == {"a" * 64: {66: 4}, "b" * 64: {65: 8}}
    assert not ambiguous
    assert unresolved["source_font_binding_verified"] is False
    layouts = _character_layout({}, span, "Same", lambda x, y: (x, y), _trace_glyph_queues(page))
    assert layouts[0].glyph_id is None
    assert not layouts[0].source_font_binding_verified
    assert layouts[1].glyph_id == 4 and layouts[2].glyph_id == 8
    catalog = EmbeddedFontCatalog(1, {}, {})
    asset, failure = catalog.resolve_span("Same", [unresolved])
    assert asset is None and failure.reason == "incomplete_source_font_character_binding"


def test_identical_coincident_trace_glyphs_keep_the_original_program_binding():
    from pdfcadcore.embedded_fonts import _page_program_glyph_maps
    from pdfcadcore.primitive_extractor import _character_layout, _trace_glyph_queues

    def char(text, x, digest):
        return {"c": text, "origin": (x, 0), "bbox": (x, 0, x + 2, 10),
                "quad": ((x, 0), (x + 2, 0), (x + 2, 10), (x, 10)),
                "source_font_program_sha256": digest,
                "source_font_binding_verified": True,
                "source_font_character_codepoint": ord(text)}

    own = char("A", 10, "a" * 64)
    other = char("A", 0, "b" * 64)
    span = {"font": "Same", "chars": [own, other]}
    raw = {"blocks": [{"type": 0, "lines": [{"spans": [span]}]}]}
    trace = {"font": "Same", "chars": [(65, 8, (0, 0)), (65, 3, (10, 0)), (65, 3, (10, 0))]}
    page = SimpleNamespace(get_texttrace=lambda: [trace])
    maps, ambiguous = _page_program_glyph_maps(page, raw)
    assert maps == {"a" * 64: {65: 3}, "b" * 64: {65: 8}}
    assert not ambiguous and own["source_font_binding_verified"] is True
    layouts = _character_layout({}, span, "Same", lambda x, y: (x, y), _trace_glyph_queues(page))
    assert [char.glyph_id for char in layouts] == [3, 8]
    assert all(char.source_font_binding_verified for char in layouts)
    assert [char.source_font_program_sha256 for char in layouts] == ["a" * 64, "b" * 64]


@pytest.mark.parametrize("text,flags,trace_count,expected", [
    (" ", 4, 0, True), (" ", 532, 0, True), (" ", 28, 0, True),
    (" ", 16, 0, False), (" ", 24, 0, False), ("A", 4, 0, False),
    (" ", 4, 1, False), (" ", 4, 2, False), (" ", True, 0, False),
])
def test_only_native_synthetic_spaces_without_paint_can_retain_font_binding(text, flags, trace_count, expected):
    from pdfcadcore.embedded_fonts import _page_program_glyph_maps
    from pdfcadcore.primitive_extractor import _character_layout, _trace_glyph_queues

    char = {"c": text, "origin": (10, 20), "bbox": (10, 20, 20, 30),
            "quad": ((10, 20), (20, 20), (20, 30), (10, 30)),
            "source_font_program_sha256": "a" * 64,
            "source_font_binding_verified": True,
            "source_font_character_codepoint": ord(text), "source_stext_flags": flags}
    span = {"font": "Same", "chars": [char]}
    raw = {"blocks": [{"type": 0, "lines": [{"spans": [span]}]}]}
    trace = {"font": "Same", "chars": [(ord(text), 2, (10, 20))] * trace_count}
    page = SimpleNamespace(get_texttrace=lambda: [trace])
    _page_program_glyph_maps(page, raw)
    assert char["source_font_binding_verified"] is expected
    assert bool(char.get("source_synthetic_spacing")) is expected
    layout = _character_layout({}, span, "Same", lambda x, y: (x, y), _trace_glyph_queues(page))[0]
    assert layout.glyph_id is None
    assert layout.source_synthetic_spacing is expected
    assert layout.source_origin_pdf == (10, 20)
    assert layout.advance_width == 10


def test_filled_synthetic_word_space_keeps_its_trace_glyph():
    """A painted word space can also carry MuPDF's synthetic bit.

    Sheet-edge lines do this. Stripping that space used to fail the whole
    line as an unverified font binding.
    """
    from pdfcadcore.embedded_fonts import _page_program_glyph_maps
    from pdfcadcore.primitive_extractor import _character_layout, _trace_glyph_queues

    char = {"c": " ", "origin": (10, 20), "bbox": (10, 20, 20, 30),
            "quad": ((10, 20), (20, 20), (20, 30), (10, 30)),
            "source_font_program_sha256": "a" * 64,
            "source_font_binding_verified": True,
            "source_font_character_codepoint": ord(" "), "source_stext_flags": 20}
    span = {"font": "Same", "chars": [char]}
    raw = {"blocks": [{"type": 0, "lines": [{"spans": [span]}]}]}
    trace = {"font": "Same", "chars": [(ord(" "), 1, (10, 20))]}
    page = SimpleNamespace(get_texttrace=lambda: [trace])
    maps, ambiguous = _page_program_glyph_maps(page, raw)
    assert maps == {"a" * 64: {32: 1}} and not ambiguous
    assert char["source_font_binding_verified"] is True
    assert not char.get("source_synthetic_spacing")
    layout = _character_layout({}, span, "Same", lambda x, y: (x, y), _trace_glyph_queues(page))[0]
    assert layout.glyph_id == 1 and layout.source_font_binding_verified
    assert layout.source_synthetic_spacing is False


@pytest.mark.parametrize("visible", [False, True])
def test_synthetic_space_requires_original_exact_font_empty_glyph(deterministic_exact_font, visible):
    font = TTFont(deterministic_exact_font)
    if visible:
        name = font.getBestCmap()[32]
        pen = TTGlyphPen(None)
        pen.moveTo((0, 0)); pen.lineTo((500, 0)); pen.lineTo((500, 700)); pen.closePath()
        font["glyf"][name] = pen.glyph()
    stream = BytesIO()
    font.save(stream)
    font.close()
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_font(fontname="F1", fontbuffer=stream.getvalue())
        page.insert_text((20, 40), "A", fontname="F1")
        catalog = EmbeddedFontCatalog.from_page(page, 1)
        asset = catalog._candidates[0]
        char = {"c": " ", "source_font_program_sha256": asset.source_sha256,
                "source_font_binding_verified": True, "source_synthetic_spacing": True}
        bound, failure = catalog.resolve_span(asset.span_font_name, [char])
        if visible:
            assert bound is None and failure.reason == "synthetic_space_zero_ink_unverified"
            assert not char["source_font_binding_verified"]
            assert not char["source_synthetic_spacing"]
        else:
            assert failure is None and bound.source_sha256 == asset.source_sha256


@pytest.mark.parametrize("space_name,visible,expected", [
    ("space", False, True), ("space", True, False),
    ("unknownSpacing", False, False),
])
def test_original_cff_synthetic_space_proof_requires_encoded_empty_glyph(space_name, visible, expected):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    order = [".notdef", space_name, "A"]
    strings = {}
    for name in order:
        pen = T2CharStringPen(500, None)
        if name == "A" or (name == space_name and visible):
            pen.moveTo((0, 0)); pen.lineTo((500, 0)); pen.lineTo((500, 700)); pen.closePath()
        strings[name] = pen.getCharString()
    builder = FontBuilder(1000, isTTF=False)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({32: space_name, 65: "A"})
    builder.setupHorizontalMetrics({name: (500, 0) for name in order})
    builder.setupHorizontalHeader(ascent=1000, descent=0)
    builder.setupNameTable({"familyName": "ExactCFF", "styleName": "Regular",
                           "psName": "ExactCFF", "fullName": "ExactCFF"})
    builder.setupOS2(sTypoAscender=1000, sTypoDescender=0, usWinAscent=1000, usWinDescent=0)
    builder.setupPost()
    builder.setupCFF("ExactCFF", {"FullName": "ExactCFF", "FamilyName": "ExactCFF",
                                "Weight": "Regular", "FontMatrix": [0.001, 0, 0, 0.001, 0, 0]},
                     strings, {})
    # Strip the wrapper's cmap: raw CFF proof must come from its own encoding
    # and original glyph names, never the repaired or wrapper Unicode table.
    original = builder.font["CFF "].compile(builder.font)
    builder.font.close()
    asset = SimpleNamespace(source_bytes=original, source_format="cff",
                            source_sha256=sha256(original).hexdigest(), base_font_name="ExactCFF")
    catalog = EmbeddedFontCatalog(1, {}, {})
    assert catalog._original_space_is_empty(asset) is expected
