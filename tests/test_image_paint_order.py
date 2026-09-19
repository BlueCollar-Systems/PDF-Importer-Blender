"""Source images cannot erase later paint or escape their original clips."""

import base64
from types import SimpleNamespace

import pymupdf as fitz
import pytest

from pdf_vector_importer.image_paint_order import (
    _contains,
    _svg_images,
    plan_opaque_images,
)
from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page


def png():
    pixels = fitz.Pixmap(fitz.csRGB, (0, 0, 4, 3), False)
    pixels.clear_with(255)
    return pixels.tobytes("png")


def fixture_page():
    doc = fitz.open()
    p = doc.new_page(width=200, height=150)
    p.draw_line((0, 40), (180, 40), color=(0, 0, 0))
    p.insert_image((20, 20, 120, 95), stream=png())
    return doc, p


def test_exact_opaque_image_binds_pixels_affine_occurrence_and_paint_order():
    doc, p = fixture_page()
    plan = plan_opaque_images(p, extract_page(p, 1, detect_arcs=False))
    assert len(plan) == 1
    row = plan[0]
    assert row["source_paint_order"] == 1
    assert row["source_xref"] == p.get_images()[0][0]
    assert row["quad_pdf"] == ((20, 20), (120, 20), (120, 95), (20, 95))
    assert row["later_strokes"] == row["later_text_items"] == {}
    assert row["width"] == 4 and row["height"] == 3
    doc.close()


def test_later_stroke_and_text_are_obligations_not_erased():
    doc, p = fixture_page()
    p.draw_rect((25, 25, 115, 90), color=(1, 0, 0), fill=(1, 1, 1), fill_opacity=0)
    p.insert_text((35, 50), "DATE", fontsize=12)
    data = extract_page(p, 1, detect_arcs=False)
    plan = plan_opaque_images(p, data)
    assert len(plan) == 1
    assert list(plan[0]["later_strokes"]) == [3]
    assert list(plan[0]["later_text_items"]) == [4]
    assert list(plan[0]["later_text_items"].values()) == [data.text_items[0].id]
    doc.close()


@pytest.mark.parametrize(
    "later", ["opaque-fill", "alpha-stroke", "image", "unbound-text", "unknown-order"]
)
def test_unaccounted_later_paint_prevents_qualification(later):
    doc, p = fixture_page()
    if later == "opaque-fill":
        p.draw_rect((30, 30, 60, 60), fill=(0, 0, 1))
    elif later == "alpha-stroke":
        p.draw_line((0, 50), (180, 50), stroke_opacity=0.5)
    elif later == "image":
        p.insert_image((40, 40, 100, 85), stream=png())
    elif later == "unbound-text":
        p.insert_text((30, 50), "Later")
    else:
        p.draw_line((0, 50), (180, 50))
    data = extract_page(p, 1, detect_arcs=False)
    if later == "unbound-text":
        data.text_items.clear()
    if later == "unknown-order":
        data.primitives[-1].source_draw_order = None
    assert not any(r["source_paint_order"] == 1 for r in plan_opaque_images(p, data))
    doc.close()


def test_moved_stroke_expands_dependency_region_to_prevent_outside_occlusion():
    doc, p = fixture_page()
    p.draw_line((100, 50), (175, 50))
    # This later filled region misses the image but intersects the stroke.
    p.draw_rect((150, 45, 165, 55), fill=(1, 0, 0))
    assert plan_opaque_images(p, extract_page(p, 1, detect_arcs=False)) == []
    doc.close()


def svg_image(*, clip="", group="", image=""):
    encoded = base64.b64encode(png()).decode()
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        'width="200" height="150" viewBox="0 0 200 150">'
        f'{clip}<g {group}><image width="4" height="3" transform="matrix(25,0,0,25,20,20)" '
        f'xlink:href="data:image/png;base64,{encoded}" {image}/></g></svg>'
    )


@pytest.mark.parametrize(
    "attributes",
    [
        'opacity=".5"',
        'style="mix-blend-mode:multiply"',
        'filter="url(#unknown)"',
        'mask="url(#unknown)"',
    ],
)
def test_ancestor_compositing_is_not_assumed_normal(attributes):
    assert _svg_images(svg_image(group=attributes), fitz) == [None]


def test_full_rectangle_clip_proven_but_partial_clip_rejected():
    clip = '<defs><clipPath id="c"><path d="M20 20H120V95H20Z"/></clipPath></defs>'
    svg = svg_image(clip=clip, group='clip-path="url(#c)"')
    assert _svg_images(svg, fitz)[0] is not None
    assert _svg_images(svg.replace("H120", "H119.99"), fitz) == [None]
    assert _svg_images(
        svg.replace("M20 20H120V95H20Z", "M20 20L120 20L50 30Z"), fitz
    ) == [None]
    assert _svg_images(
        svg.replace('id="c"', 'id="c" clipPathUnits="objectBoundingBox"'), fitz
    ) == [None]


def test_degenerate_or_crossed_clip_cannot_prove_coverage():
    assert not _contains(((0, 0),) * 4, ((100, 100),))
    assert not _contains(((0, 0), (2, 2), (0, 2), (2, 0)), ((1, 1),))
    assert not _contains(((0, 0), (2, 0), (0.5, 0.5), (0, 2)), ((1, 1),))


def test_no_image_inventory_does_not_render_svg():
    page = SimpleNamespace(get_image_info=lambda **_: [])
    assert plan_opaque_images(page, SimpleNamespace()) == []


def test_source_knockout_group_does_not_qualify_as_ordinary_opaque_paint():
    doc, page = fixture_page()
    data = extract_page(page, 1, detect_arcs=False)

    class KnockoutPage:
        def __getattr__(self, name):
            return getattr(page, name)

        def get_drawings(self, **_):
            return page.get_drawings(extended=True) + [
                {
                    "type": "group",
                    "blendmode": "Normal",
                    "opacity": 1.0,
                    "knockout": True,
                    "rect": (20, 20, 120, 95),
                }
            ]

    assert plan_opaque_images(KnockoutPage(), data) == []
    doc.close()


def test_original_knockout_declaration_rejects_even_if_renderer_flattens_flag():
    doc, page = fixture_page()
    doc.xref_set_key(page.xref, 'Group', '<< /S /Transparency /K true /I false >>')
    assert plan_opaque_images(page, extract_page(page, 1, detect_arcs=False)) == []
    doc.close()
