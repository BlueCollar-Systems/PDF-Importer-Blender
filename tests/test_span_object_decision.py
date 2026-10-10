"""One editable text object per line only where the PDF's letter spacing is the font's own.

Fictional PDFs (job D042, mark EX101) are built inside each test with an
embedded generated font, so the checks run on every platform.
"""
from __future__ import annotations

from pathlib import Path
import sys
import types

import pytest

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
from fontTools.fontBuilder import FontBuilder  # noqa: E402
from fontTools.pens.ttGlyphPen import TTGlyphPen  # noqa: E402
from fontTools.ttLib import TTFont, newTable  # noqa: E402
from fontTools.ttLib.tables._k_e_r_n import KernTable_format_0  # noqa: E402

from pdf_vector_importer import bl_text_builder  # noqa: E402
from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page  # noqa: E402

LINE = "D042 EX101"
KERNED_LINE = "EX101 PLATE"


def _box(width):
    pen = TTGlyphPen(None)
    pen.moveTo((60, 0))
    pen.lineTo((max(120, width - 60), 0))
    pen.lineTo((max(120, width - 60), 1400))
    pen.lineTo((60, 1400))
    pen.closePath()
    return pen.glyph()


@pytest.fixture(scope="module")
def font_file(tmp_path_factory) -> Path:
    """A TrueType face with uneven advances and one kerning pair (A then T)."""
    path = tmp_path_factory.mktemp("span_font") / "bcsspantest.ttf"
    order = [".notdef", "space"] + [f"uni{code:04X}" for code in range(33, 127)]
    cmap = {32: "space", **{code: f"uni{code:04X}" for code in range(33, 127)}}
    advances = {".notdef": 1000, "space": 569}
    advances.update({f"uni{code:04X}": 900 + (code * 37) % 500 for code in range(33, 127)})
    glyphs = {name: (TTGlyphPen(None).glyph() if name == "space" else _box(advances[name]))
              for name in order}
    builder = FontBuilder(2048, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(cmap)
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (advances[name], 60) for name in order})
    builder.setupHorizontalHeader(ascent=1854, descent=-434)
    builder.setupOS2(sTypoAscender=1854, sTypoDescender=-434, usWinAscent=1854,
                     usWinDescent=434, sxHeight=1062, sCapHeight=1467)
    builder.setupNameTable({
        "familyName": "BCS Span Test", "styleName": "Regular",
        "uniqueFontIdentifier": "BCS-Span-Test-Regular-1.0",
        "fullName": "BCS Span Test Regular", "psName": "BCSSpanTest-Regular",
        "version": "Version 1.0",
    })
    builder.setupPost()
    kern = newTable("kern")
    kern.version = 0
    subtable = KernTable_format_0()
    subtable.version = 0
    subtable.coverage = 1
    subtable.format = 0
    subtable.kernTable = {("uni0041", "uni0054"): -150}
    kern.kernTables = [subtable]
    builder.font["kern"] = kern
    builder.save(str(path))
    return path


def _glyph_hex(font_file: Path, text: str) -> str:
    font = TTFont(str(font_file))
    try:
        cmap = font.getBestCmap()
        return "".join(f"{font.getGlyphID(cmap[ord(char)]):04x}" for char in text)
    finally:
        font.close()


def _span_item(tmp_path, font_file, text, operators, *, expected=None):
    """Extract the one span a custom content stream draws with the embedded face."""
    pdf = tmp_path / "d042_span.pdf"
    document = fitz.open()
    page = document.new_page(width=612, height=396)
    page.insert_font(fontname="F0", fontfile=str(font_file))
    # Writes the font resource and its Unicode map for every glyph used below.
    page.insert_text((72, 96), LINE + " " + KERNED_LINE, fontname="F0", fontsize=14)
    content = f"BT /F0 14 Tf {operators(text)} ET\n".encode("latin-1")
    page.clean_contents()
    document.update_stream(page.get_contents()[0], content)
    document.save(str(pdf))
    document.close()
    document = fitz.open(str(pdf))
    try:
        items = [item for item in extract_page(document[0], 1).text_items if item.text.strip()]
    finally:
        document.close()
    assert len(items) == 1, [item.text for item in items]
    item = items[0]
    assert item.text == (text if expected is None else expected)
    assert item.font_asset is not None
    assert item.requires_individual_positioning
    return item


def _plain(font_file):
    return lambda text: f"1 0 0 1 72 300 Tm [<{_glyph_hex(font_file, text)}>] TJ"


def test_plain_tj_follows_the_fonts_own_widths(tmp_path, font_file):
    item = _span_item(tmp_path, font_file, LINE, _plain(font_file))
    decision, reason = bl_text_builder.span_uses_natural_advances(
        item, host_applies_font_kerning=False
    )
    assert decision is True, reason
    assert "own widths" in reason


def test_letter_spacing_keeps_one_object_per_letter(tmp_path, font_file):
    item = _span_item(
        tmp_path, font_file, LINE,
        lambda text: f"1 Tc 1 0 0 1 72 300 Tm [<{_glyph_hex(font_file, text)}>] TJ",
    )
    decision, reason = bl_text_builder.span_uses_natural_advances(
        item, host_applies_font_kerning=False
    )
    assert decision is False
    assert "adds spacing" in reason


def test_a_tj_gap_keeps_one_object_per_letter(tmp_path, font_file):
    def gap(text):
        return (
            f"1 0 0 1 72 300 Tm [<{_glyph_hex(font_file, text[:2])}> -200 "
            f"<{_glyph_hex(font_file, text[2:])}>] TJ"
        )

    # MuPDF reads a wide TJ gap as a word space it adds itself ("D0 42").
    item = _span_item(tmp_path, font_file, LINE, gap, expected="D0 42 EX101")
    decision, reason = bl_text_builder.span_uses_natural_advances(
        item, host_applies_font_kerning=False
    )
    assert decision is False, reason


def test_skewed_text_keeps_one_object_per_letter(tmp_path, font_file):
    item = _span_item(
        tmp_path, font_file, LINE,
        lambda text: f"1 0 0.3 1 72 300 Tm [<{_glyph_hex(font_file, text)}>] TJ",
    )
    decision, reason = bl_text_builder.span_uses_natural_advances(
        item, host_applies_font_kerning=False
    )
    assert decision is False
    assert "slant" in reason


def test_rotated_text_without_skew_can_be_one_object(tmp_path, font_file):
    item = _span_item(
        tmp_path, font_file, LINE,
        lambda text: f"0 1 -1 0 300 72 Tm [<{_glyph_hex(font_file, text)}>] TJ",
    )
    decision, reason = bl_text_builder.span_uses_natural_advances(
        item, host_applies_font_kerning=False
    )
    assert decision is True, reason


def test_kerned_neighbours_matter_only_where_the_host_may_kern(tmp_path, font_file):
    item = _span_item(tmp_path, font_file, KERNED_LINE, _plain(font_file))
    kerned, reason = bl_text_builder.span_uses_natural_advances(
        item, host_applies_font_kerning=True
    )
    assert kerned is False and "kerns" in reason
    # Blender 5.2 lays text out without font kerning (measured), so the same
    # span is one object there.
    plain, reason = bl_text_builder.span_uses_natural_advances(
        item, host_applies_font_kerning=False
    )
    assert plain is True, reason


def test_host_kerning_is_assumed_unless_measured_absent(monkeypatch):
    monkeypatch.setattr(bl_text_builder, "bpy", types.SimpleNamespace(
        app=types.SimpleNamespace(version=(5, 2, 2))))
    assert bl_text_builder._host_applies_font_kerning() is False
    monkeypatch.setattr(bl_text_builder, "bpy", types.SimpleNamespace(
        app=types.SimpleNamespace(version=(4, 2, 0))))
    assert bl_text_builder._host_applies_font_kerning() is True
    monkeypatch.setattr(bl_text_builder, "bpy", types.SimpleNamespace())
    assert bl_text_builder._host_applies_font_kerning() is True


def test_span_item_spans_first_letter_to_last_letter(tmp_path, font_file):
    item = _span_item(tmp_path, font_file, LINE, _plain(font_file))
    span = bl_text_builder._span_object_text_item(item)
    first, last = item.source_char_layout[0], item.source_char_layout[-1]
    assert span.text == LINE and span.positioned_character is True
    assert span.requires_individual_positioning is False
    assert len(span.source_char_layout) == 1
    assert span.insertion == tuple(first.target_origin)
    assert span.target_quad_model[0] == tuple(first.target_quad[0])
    assert span.target_quad_model[1] == tuple(last.target_quad[1])
    assert span.source_quad_pdf[1] == tuple(last.source_quad_pdf[1])
    assert span.source_glyph_id == first.glyph_id
    # The per-letter font frame check accepts the span frame unchanged.
    height = bl_text_builder._source_character_font_height(span.source_char_layout[0])
    assert height == pytest.approx(
        first.source_font_ascender - first.source_font_descender
    )


def test_last_letter_check_reads_the_recorded_placement(tmp_path, font_file):
    item = _span_item(tmp_path, font_file, LINE, _plain(font_file))
    span = bl_text_builder._span_object_text_item(item)
    first = item.source_char_layout[0]
    units = [item.font_asset.glyph_advances[layout.glyph_id] for layout in item.source_char_layout]
    local_advance = 1.0
    target = span.target_quad_model
    horizontal = ((target[1][0] - target[0][0]) * 0.001, (target[1][1] - target[0][1]) * 0.001)
    origin = (first.target_origin[0] * 0.001, first.target_origin[1] * 0.001)
    matrix = [
        horizontal[0] / local_advance, 0.0, 0.0, origin[0],
        horizontal[1] / local_advance, 1.0, 0.0, origin[1],
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]
    obj = {"pdf_affine_matrix": matrix, "pdf_metric_local_advance": local_advance,
           "pdf_metric_local_baseline_y": 0.0}
    offset = bl_text_builder._span_last_letter_offset_m(obj, item)
    assert offset < 1e-6
    assert sum(units) > 0


def test_a_small_tj_adjustment_keeps_one_object_per_letter(tmp_path, font_file):
    # A small TJ number moves the letters after it without a word space.
    def nudge(text):
        return (
            f"1 0 0 1 72 300 Tm [<{_glyph_hex(font_file, text[:2])}> -40 "
            f"<{_glyph_hex(font_file, text[2:])}>] TJ"
        )

    item = _span_item(tmp_path, font_file, LINE, nudge)
    decision, reason = bl_text_builder.span_uses_natural_advances(
        item, host_applies_font_kerning=False
    )
    assert decision is False
    assert "letter 3 ('4')" in reason and "adds spacing" in reason


def test_horizontally_squeezed_text_can_be_one_object(tmp_path, font_file):
    # Tz narrows the whole line evenly. The span's placement squeezes the one
    # object the same way the per-letter placement squeezes each letter.
    item = _span_item(
        tmp_path, font_file, LINE,
        lambda text: f"90 Tz 1 0 0 1 72 300 Tm [<{_glyph_hex(font_file, text)}>] TJ",
    )
    decision, reason = bl_text_builder.span_uses_natural_advances(
        item, host_applies_font_kerning=False
    )
    assert decision is True, reason
