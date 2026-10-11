"""Actual source/font no-ink proof, without importing or pretending to run bpy."""
import ast
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pymupdf
import pytest

from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page


def proof_function():
    source = Path(__file__).parents[1] / "pdf_vector_importer/bl_text_builder.py"
    node = next(node for node in ast.parse(source.read_text(encoding="utf-8")).body
                if isinstance(node, ast.FunctionDef) and node.name == "_source_space_no_ink_proof")
    namespace = {"sha256": sha256, "BytesIO": BytesIO,
                 "__name__": "pdf_vector_importer._space_proof_test", "__package__": "pdf_vector_importer"}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
    return namespace[node.name]


@pytest.fixture
def inferred_space():
    with pymupdf.open() as document:
        page = document.new_page(width=240, height=160)
        page.insert_text((20, 40), "A", fontname="helv", fontsize=12)
        page.insert_text((33, 40), "B", fontname="helv", fontsize=12)
        items = extract_page(page, 1).text_items
        space = next(item for item in items if item.text == " ")
        assert len(space.source_char_layout) == 1
        assert space.source_char_layout[0].source_synthetic_spacing is True
        return space


def test_actual_inferred_source_space_has_original_bound_empty_font_outline(inferred_space):
    proof = proof_function()(inferred_space)
    assert proof["source_space_no_ink_verified"] is True
    assert proof["advance_preserved"] is True
    assert proof["source_sha256"] == sha256(inferred_space.font_asset.source_bytes).hexdigest()


@pytest.mark.parametrize("change", ["binding", "flags", "program", "source_bytes", "usable_bytes", "control"])
def test_empty_curve_cannot_be_accepted_by_whitespace_alone(inferred_space, change):
    item = inferred_space
    char = item.source_char_layout[0]
    if change == "binding":
        char = replace(char, source_font_binding_verified=False)
    elif change == "flags":
        char = replace(char, source_stext_flags=16)
    elif change == "program":
        char = replace(char, source_font_program_sha256="0" * 64)
    elif change == "source_bytes":
        item = replace(item, font_asset=replace(item.font_asset, source_bytes=b"unbound source"))
    elif change == "usable_bytes":
        item = replace(item, font_asset=replace(item.font_asset, usable_bytes=b"invalid font"))
    else:
        item = replace(item, text="\t")
    item = replace(item, source_char_layout=[char])
    assert proof_function()(item) is None


def _space_program(visible):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "space"])
    builder.setupCharacterMap({32: "space"})
    pen = TTGlyphPen(None)
    if visible:
        pen.moveTo((0, 0))
        for point in ((400, 0), (400, 500), (0, 500)):
            pen.lineTo(point)
        pen.closePath()
    builder.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "space": pen.glyph()})
    builder.setupHorizontalMetrics({".notdef": (500, 0), "space": (500, 0)})
    builder.setupHorizontalHeader(ascent=1000, descent=0)
    builder.setupNameTable({"familyName": "Space proof", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=1000, sTypoDescender=0, usWinAscent=1000, usWinDescent=0)
    builder.setupPost()
    builder.setupMaxp()
    stream = BytesIO()
    builder.font.save(stream)
    return stream.getvalue()


@pytest.mark.parametrize("original_visible,usable_visible", [(True, False), (False, True)])
def test_visible_original_or_usable_space_cannot_qualify_as_empty(inferred_space, original_visible, usable_visible):
    original, usable = _space_program(original_visible), _space_program(usable_visible)
    original_sha = sha256(original).hexdigest()
    asset = replace(inferred_space.font_asset, source_format="ttf", source_bytes=original,
                    source_sha256=original_sha, usable_bytes=usable, usable_sha256=sha256(usable).hexdigest())
    char = replace(inferred_space.source_char_layout[0], source_font_program_sha256=original_sha)
    item = replace(inferred_space, font_asset=asset, source_char_layout=[char])
    assert proof_function()(item) is None
