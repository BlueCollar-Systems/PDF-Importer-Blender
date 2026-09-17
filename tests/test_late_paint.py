"""Native overlays must follow complete source order and not double-paint crops."""
import pymupdf
import pytest

from pdf_vector_importer.late_paint import final_rectangles, subtract_rectangles
from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page


def test_only_final_unclipped_translucent_rectangles_are_eligible():
    with pymupdf.open() as doc:
        p = doc.new_page(width=200, height=150)
        p.draw_rect((10, 10, 60, 30), fill=(0, 1, 1), color=(0, 1, 1), fill_opacity=.3)
        p.insert_text((10, 50), "Later native text")
        p.draw_rect((10, 40, 60, 60), fill=(0, 1, 1), color=(0, 1, 1), fill_opacity=.3)
        data = extract_page(p, 1, detect_arcs=False)
        eligible = final_rectangles(p, data)
        assert len(eligible) == 1
        assert eligible[0][0].source_draw_order == p.get_drawings()[-1]["seqno"]
        data._source_drawings[-1]["level"] = 1
        assert final_rectangles(p, data) == []


def test_later_image_or_other_paint_stops_final_overlay_proof():
    with pymupdf.open() as doc:
        p = doc.new_page(width=200, height=150)
        p.draw_rect((10, 10, 60, 30), fill=(0, 1, 1), color=(0, 1, 1), fill_opacity=.3)
        p.draw_line((5, 20), (100, 20))
        assert final_rectangles(p, extract_page(p, 1, detect_arcs=False)) == []


def test_final_crop_subtraction_preserves_area_without_overlap():
    original = (0, 0, 10, 10)
    cutters = [(2, 2, 6, 8), (4, 4, 8, 6), (20, 20, 30, 30)]
    remaining = subtract_rectangles(original, cutters)
    area = lambda r: (r[2]-r[0])*(r[3]-r[1])
    assert sum(map(area, remaining)) == pytest.approx(72)
    for i, a in enumerate(remaining):
        for b in cutters + remaining[i+1:]:
            assert min(a[2], b[2]) <= max(a[0], b[0]) or min(a[3], b[3]) <= max(a[1], b[1])
    assert subtract_rectangles(original, [(-1, -1, 11, 11)]) == []


def test_renderer_proof_rejects_unknown_clip_width_and_miter():
    from pdf_vector_importer.pdf_paint_proof import final_svg_rectangles
    with pymupdf.open() as doc:
        p = doc.new_page(width=200, height=150)
        p.draw_rect((10, 10, 60, 30), fill=(0, 1, 1), color=(0, 1, 1), fill_opacity=.3)
        svg, rows = p.get_svg_image(), p.get_drawings()
    assert final_svg_rectangles(svg, rows)
    for bad in [svg.replace('stroke-miterlimit="10"', ''),
                svg.replace('stroke-miterlimit="10"', 'stroke-miterlimit="nan"'),
                svg.replace('stroke-miterlimit="10"', 'stroke-miterlimit="1"'),
                svg.replace('stroke-width="1"', 'stroke-width="2"'),
                svg.replace('<path ', '<path clip-path="url(#unknown)" ', 1),
                svg.replace('fill-opacity=".3"', 'fill-opacity=".7"')]:
        assert not final_svg_rectangles(bad, rows)


def test_exact_miter_ring_has_source_width_and_disjoint_paint_area():
    from pdf_vector_importer.late_paint import paint_rectangles
    paints = paint_rectangles((10, 20, 40, 50), 2)
    assert paints[0] == (11, 21, 39, 49)
    area = lambda r: (r[2]-r[0])*(r[3]-r[1])
    assert sum(map(area, paints)) == 32*32
    assert sum(map(area, paints[1:])) == 32*32 - 28*28
    for i, a in enumerate(paints):
        for b in paints[i+1:]:
            assert min(a[2], b[2]) <= max(a[0], b[0]) or min(a[3], b[3]) <= max(a[1], b[1])


def test_transparent_whitespace_cannot_excise_final_annotation_paint():
    from pdf_vector_importer.late_paint import _has_final_page_pixels
    visible = {"pdf_raster_source_item_id": "page:1:text:1",
               "pdf_raster_final_page_composite": True,
               "pdf_raster_expected_transparent": False}
    assert _has_final_page_pixels(visible)
    assert not _has_final_page_pixels({**visible, "pdf_raster_expected_transparent": True})
    assert not _has_final_page_pixels({**visible, "pdf_raster_final_page_composite": False})
    assert not _has_final_page_pixels({})
