"""The visible round caps of a short PDF stroke must retain its centerline."""

import pymupdf
import pytest

from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page


@pytest.mark.parametrize("length", [0.0, 0.01])
def test_short_literal_stroke_survives_source_cleanup_and_pdf_reopen(length):
    with pymupdf.open() as original:
        page = original.new_page(width=100, height=100)
        page.draw_line(
            (30, 40), (30 + length, 40), width=12, lineCap=1, color=(1, 0.5, 0)
        )
        encoded = original.tobytes()
    with pymupdf.open(stream=encoded, filetype="pdf") as document:
        page = document[0]
        row = page.get_drawings()[0]
        data = extract_page(page, 1, detect_arcs=False)
        matches = [p for p in data.primitives if p.source_draw_order == row["seqno"]]
        assert len(matches) == 1
        assert matches[0].type == "line"
        factor = 25.4 / 72
        expected = [(p.x * factor, (100 - p.y) * factor) for p in row["items"][0][1:]]
        assert matches[0].points == expected
        assert len(matches[0].points) == 2
