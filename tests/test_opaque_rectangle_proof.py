import pytest

from pdf_vector_importer import opaque_rectangle_proof as proof
from pdf_vector_importer.opaque_rectangle_order import bind_rectangle_plans
from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page


def source(*, stroke=True, later="text", opacity=1, clip=False):
    fitz = pytest.importorskip("pymupdf")
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.draw_line((10, 10), (180, 150), color=(0, 0, 0), width=1)
    page.draw_rect(
        (20, 20, 160, 100),
        color=(1, 0, 0) if stroke else None,
        fill=(1, 1, 1),
        fill_opacity=opacity,
        width=1,
    )
    page.insert_text((35, 45), "CODE COMPLIANCE", fontsize=10)
    page.insert_text((35, 65), "FIELD APPROVAL", fontsize=10)
    if later == "line":
        page.draw_line((30, 25), (140, 80), color=(0, 0, 0))
    elif later == "image":
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 2, 2))
        pix.clear_with(255)
        page.insert_image((40, 50, 60, 70), pixmap=pix)
    if clip:
        # Exact source clipping of the rectangle must fail full-footprint proof.
        streams = page.get_contents()
        doc.update_stream(
            streams[1],
            b"q 30 110 100 65 re W n\n" + doc.xref_stream(streams[1]) + b"\nQ",
        )
    return doc, page


@pytest.mark.parametrize("stroke", [False, True])
def test_original_opaque_rectangle_and_all_later_text_have_unique_ownership(stroke):
    doc, page = source(stroke=stroke)
    plans = proof.plan_opaque_rectangles(page, "a" * 64)
    assert len(plans) == 1
    plan = plans[0]
    assert plan["source_bbox_pdf"] == [20, 20, 160, 100]
    assert plan["stroke_rgb"] == ([1, 0, 0] if stroke else None)
    assert [
        item["text"] for trace in plan["later_text"] for item in trace["items"]
    ] == ["CODE COMPLIANCE", "FIELD APPROVAL"]
    assert plan["source_paint_order"] < min(
        trace["source_paint_order"] for trace in plan["later_text"]
    )
    doc.close()


def test_saved_source_rectangle_binds_actual_extractor_primitives_and_spans(tmp_path):
    import hashlib
    import pymupdf as fitz

    doc, _ = source()
    path = tmp_path / 'source.pdf'
    doc.save(path)
    doc.close()
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    with fitz.open(path) as reopened:
        page = reopened[0]
        plans = proof.plan_opaque_rectangles(page, sha)
        data = extract_page(page, page_num=1, scale=1.0, flip_y=True)
        bound, unresolved = bind_rectangle_plans(plans, data, page.rect)
    assert unresolved == []
    assert len(bound) == 1
    assert bound[0]['source_proof']['source_sha256'] == sha
    ids = [item for event in bound[0]['later_text'] for item in event['span_ids']]
    assert [item.text for item in data.text_items if item.id in ids] == ['CODE COMPLIANCE', 'FIELD APPROVAL']


@pytest.mark.parametrize(
    "kwargs", [{"opacity": 0.5}, {"later": "line"}, {"later": "image"}, {"clip": True}]
)
def test_partial_translucent_or_unknown_later_overlap_is_not_reordered(kwargs):
    doc, page = source(**kwargs)
    assert proof.plan_opaque_rectangles(page, "a" * 64) == []
    doc.close()


def test_trace_partition_does_not_merge_or_drop_multiline_source_spans():
    trace = dict(
        type=0,
        opacity=1,
        chars=[
            (ord(c), 0, (x, y)) for c, x, y in [("A", 1, 2), ("B", 2, 2), ("C", 1, 4)]
        ],
    )
    spans = [
        dict(
            chars=[dict(c="A", origin=(1, 2)), dict(c="B", origin=(2, 2))],
            bbox=(0, 0, 3, 3),
        ),
        dict(chars=[dict(c="C", origin=(1, 4))], bbox=(0, 3, 3, 5)),
    ]
    raw = dict(
        blocks=[dict(type=0, lines=[dict(spans=[spans[0]]), dict(spans=[spans[1]])])]
    )
    rows = proof._text_items(trace, raw, 1)
    assert [r["source_item_id"] for r in rows] == ["p1:b0:l0:s0", "p1:b0:l1:s0"]
    raw["blocks"][0]["lines"].append(dict(spans=[spans[0]]))
    assert proof._text_items(trace, raw, 1) is None


def test_duplicate_source_rectangles_cannot_borrow_one_svg_occurrence():
    doc, page = source()
    original = page.get_drawings
    rows = original()
    duplicate = dict(rows[1])
    duplicate["seqno"] += 100
    page.get_drawings = lambda **kwargs: (
        original(**kwargs) if kwargs else rows + [duplicate]
    )
    assert proof.plan_opaque_rectangles(page, "a" * 64) == []
    doc.close()


def test_later_text_cannot_borrow_preceding_path_clip_after_graphics_restore():
    doc, page = source()
    page.draw_rect((0, 0, 300, 200), color=None, fill=(0, 0, 0))
    xref = page.get_contents()[-1]
    doc.update_stream(
        xref, b"q 0 200 m 5 200 l 4 195 l h W n\n" + doc.xref_stream(xref) + b"\nQ"
    )
    page.insert_text((35, 85), "LATE LABEL", fontsize=10)
    plans = proof.plan_opaque_rectangles(page, "a" * 64)
    assert len(plans) == 1
    assert [
        item["text"] for trace in plans[0]["later_text"] for item in trace["items"]
    ] == ["CODE COMPLIANCE", "FIELD APPROVAL", "LATE LABEL"]
    excluded = {p["source_paint_order"] for p in plans[0]["clip_disjoint_later_paints"]}
    final_trace = page.get_texttrace()[-1]["seqno"]
    assert final_trace not in excluded
    assert final_trace - 1 in excluded
    doc.close()


def test_partially_clipped_border_dependency_keeps_full_retained_stroke_envelope():
    doc, page = source()
    stream = page.get_contents()[1]
    # Page coordinates are flipped in PDF content. Clip the outer .3 pt of
    # each border while retaining the complete fill; native centerline remains
    # editable and its full stroke envelope still participates in closure.
    doc.update_stream(stream, b'q 19.8 99.8 140.4 80.4 re W n\n'
                      + doc.xref_stream(stream) + b'\nQ')
    plans = proof.plan_opaque_rectangles(page, 'a' * 64)
    assert len(plans) == 1
    assert plans[0]['original_stroke_fully_unclipped'] is False
    assert plans[0]['paint_bounds_pdf'] == pytest.approx([19.5, 19.5, 160.5, 100.5])
    doc.close()


def test_page_sized_evenodd_frame_clip_is_proven_disjoint_without_bbox_guess():
    outer = [(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]
    inner = [(5, 5), (95, 5), (95, 95), (5, 95), (5, 5)]
    clip = dict(
        even_odd=True,
        level=1,
        items=[
            ("l", a, b)
            for row in (outer, inner)
            for a, b in zip(row, row[1:], strict=False)
        ],
    )
    result = proof._clip_excludes_region(clip, (40, 40, 60, 60))
    assert result and result["winding"] == 2
    assert (
        proof._clip_excludes_region(dict(clip, even_odd=False), (40, 40, 60, 60))
        is None
    )
    assert proof._clip_excludes_region(clip, (3, 40, 8, 60)) is None
    # An entire painted island inside the requested region must not disappear.
    assert (
        proof._clip_excludes_region(
            dict(
                clip,
                items=[("l", a, b) for a, b in zip(inner, inner[1:], strict=False)],
            ),
            (-1, -1, 101, 101),
        )
        is None
    )
@pytest.mark.parametrize('extra', [
    '<style>path { fill-opacity:.2 }</style>',
    '<defs><style>path { display:none }</style></defs>',
    '<svg x="20"><path d="M0 0H1V1H0Z"/></svg>',
    '<defs><clipPath id="c"><path d="M0 0H1V1H0Z"/></clipPath>'
    '<clipPath id="c"><path d="M0 0H100V100H0Z"/></clipPath></defs>',
])
def test_ambiguous_or_unmodeled_svg_document_never_certifies_rectangle(extra):
    from pdf_vector_importer.opaque_rectangle_proof import _svg_rects
    assert _svg_rects('<svg>'+extra+'<path d="M10 10H20V20H10Z" fill="#ffffff"/></svg>') == []


def test_external_stylesheet_is_not_silently_discarded_by_xml_parser():
    from pdf_vector_importer.opaque_rectangle_proof import _svg_rects
    assert _svg_rects('<?xml-stylesheet type="text/css" href="paint.css"?>'
                      '<svg><path d="M10 10H20V20H10Z" fill="#ffffff"/></svg>') == []
