"""Regression: text must stay readable on pages that get a page raster.

A page with no embedded image and no primitives (or only a page frame) is
rendered at 300 dpi with its delivered text removed and placed as an opaque
plane: white paper. The default High Contrast style recolors text to 0.95
grey (Blueprint: light blue), so a text-only page became near-white text on
a pure white image (luminance difference 0.05).

The rule, decided per page and recorded in ``stats["page_raster_decisions"]``
(import report ``extra.page_raster_decisions``):

* the render shows no ink once the delivered text is removed -> no page
  raster plane at all; the text keeps the colors of the chosen style;
* the render shows ink (something was not delivered as text/vectors) -> the
  raster stays and that page's text and vectors are built in the source
  colors, as the Source style does;
* a scanned page (nothing delivered on top of it) is unchanged.

Fixtures are synthetic and fictional; PDF pages are generated in memory.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

if "bpy" not in sys.modules:
    sys.modules["bpy"] = types.SimpleNamespace()
if not hasattr(sys.modules["bpy"], "app"):
    sys.modules["bpy"].app = types.SimpleNamespace(version=(4, 1, 0))
if not hasattr(sys.modules["bpy"], "types"):
    sys.modules["bpy"].types = types.SimpleNamespace(
        Collection=object,
        Object=object,
    )
if "bmesh" not in sys.modules:
    sys.modules["bmesh"] = types.SimpleNamespace()

from pdf_vector_importer import bl_import_engine  # noqa: E402
from pdf_vector_importer.pdfcadcore import fitz_loader  # noqa: E402
from pdf_vector_importer.pdfcadcore.primitives import Primitive  # noqa: E402
from pdf_vector_importer.visual_style import preview_color  # noqa: E402

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore


PT_TO_MM = 25.4 / 72.0
LETTER_PT = (612.0, 792.0)
WHITE_PAPER_LUMINANCE = 1.0


def _luminance(rgb) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


# --------------------------------------------------------------------------
# The measured defect, stated in numbers
# --------------------------------------------------------------------------

@pytest.mark.parametrize("style,expected_difference", [
    ("high_contrast", 0.05),
    ("blueprint", 0.3235),
])
def test_preview_text_on_white_paper_is_the_measured_defect(style, expected_difference) -> None:
    """Black source text recolored for the dark preview, over an opaque white raster."""
    recolored = preview_color((0.0, 0.0, 0.0), style)
    difference = WHITE_PAPER_LUMINANCE - _luminance(recolored)
    assert difference == pytest.approx(expected_difference, abs=5e-4)
    assert difference < 0.5
    # The same text in the source colors is readable on that paper.
    assert WHITE_PAPER_LUMINANCE - _luminance(preview_color((0.0, 0.0, 0.0), "source")) == 1.0


# --------------------------------------------------------------------------
# Ink test on a rendered page
# --------------------------------------------------------------------------

def _pixmap(value: int, *, alpha: bool = False, size: int = 24):
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, size, size), alpha)
    pixmap.clear_with(value)
    return pixmap


def test_blank_render_has_no_ink() -> None:
    assert bl_import_engine._pixmap_has_ink(_pixmap(255)) is False


@pytest.mark.parametrize("sample,expected", [
    (255, False),
    (254, False),
    (253, False),   # 255 - tolerance: still paper
    (252, True),    # one step darker than the tolerance: ink
    (128, True),
    (0, True),
])
def test_one_pixel_decides_and_the_paper_tolerance_is_two_steps(sample, expected) -> None:
    assert bl_import_engine._PAGE_RASTER_PAPER_TOLERANCE == 2
    pixmap = _pixmap(255)
    pixmap.set_pixel(7, 11, (255, sample, 255))
    assert bl_import_engine._pixmap_has_ink(pixmap) is expected


@pytest.mark.parametrize("chunk", [1, 7, 1000, 4 * 1024 * 1024])
def test_ink_in_the_last_sample_is_found_whatever_the_chunk_size(monkeypatch, chunk) -> None:
    """The samples are scanned in chunks; no chunk boundary may hide a pixel."""
    monkeypatch.setattr(bl_import_engine, "_PAGE_RASTER_INK_CHUNK", chunk)
    blank = _pixmap(255)
    assert bl_import_engine._pixmap_has_ink(blank) is False
    last = _pixmap(255)
    last.set_pixel(last.width - 1, last.height - 1, (255, 255, 0))
    assert bl_import_engine._pixmap_has_ink(last) is True
    first = _pixmap(255)
    first.set_pixel(0, 0, (0, 255, 255))
    assert bl_import_engine._pixmap_has_ink(first) is True


def test_a_render_that_cannot_be_measured_is_unknown_not_blank() -> None:
    assert bl_import_engine._pixmap_has_ink(_pixmap(255, alpha=True)) is None
    assert bl_import_engine._pixmap_has_ink(types.SimpleNamespace(alpha=0, samples=b"")) is None
    assert bl_import_engine._pixmap_has_ink(object()) is None


# --------------------------------------------------------------------------
# Real renders of synthetic pages
# --------------------------------------------------------------------------

def _config(dpi: int = 144):
    return types.SimpleNamespace(raster_dpi=dpi, user_scale=1.0)


def _span_items(page):
    """Text items as the extractor hands them over: an id and a source box."""
    items = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                items.append(types.SimpleNamespace(
                    id=len(items) + 1,
                    text=span["text"],
                    source_bbox_pdf=tuple(float(value) for value in span["bbox"]),
                ))
    return items


def _text_only_page(document):
    page = document.new_page(width=LETTER_PT[0], height=LETTER_PT[1])
    page.insert_text((72.0, 100.0), "FICTIONAL QUOTE 0001", fontsize=11.0)
    page.insert_text((72.0, 130.0), "Qty 18 plates, grey primer", fontsize=14.0, color=(1, 0, 0))
    return page


def test_text_only_page_renders_blank_once_all_text_is_removed(tmp_path) -> None:
    document = fitz.open()
    page = _text_only_page(document)
    items = _span_items(page)
    assert len(items) == 2

    plan = bl_import_engine._plan_page_raster(page, 1, _config(), str(tmp_path), items)

    assert plan["rendered"]["has_ink"] is False
    assert plan["rendered"]["excluded_text_bbox_count"] == 2
    assert bl_import_engine._page_raster_is_blank(plan["rendered"]) is True
    assert sorted(plan["excluded_text_bboxes"]) == sorted(item.source_bbox_pdf for item in items)
    document.close()


def test_text_left_in_the_render_is_ink(tmp_path) -> None:
    document = fitz.open()
    page = _text_only_page(document)
    items = _span_items(page)

    nothing_removed = bl_import_engine._plan_page_raster(page, 1, _config(), str(tmp_path), [])
    one_removed = bl_import_engine._plan_page_raster(page, 1, _config(), str(tmp_path), items[:1])

    assert nothing_removed["rendered"]["has_ink"] is True
    assert nothing_removed["rendered"]["composition"] == "complete_page_raster"
    assert one_removed["rendered"]["has_ink"] is True
    document.close()


def test_a_hairline_that_was_not_delivered_keeps_the_raster(tmp_path) -> None:
    document = fitz.open()
    page = _text_only_page(document)
    page.draw_line((300.0, 300.0), (310.0, 300.0), color=(0, 0, 0), width=0.1)
    items = _span_items(page)

    plan = bl_import_engine._plan_page_raster(page, 1, _config(300), str(tmp_path), items)

    assert plan["rendered"]["has_ink"] is True
    assert bl_import_engine._page_raster_is_blank(plan["rendered"]) is False
    document.close()


def test_annotation_only_cover_page_renders_blank_once_its_text_is_removed(tmp_path) -> None:
    """A cover page whose only content is a text-box annotation."""
    document = fitz.open()
    page = document.new_page(width=LETTER_PT[0], height=LETTER_PT[1])
    annotation = page.add_freetext_annot(
        fitz.Rect(100.0, 100.0, 300.0, 140.0), "Job 0001 fictional", fontsize=12.0,
        text_color=(1, 0, 0))
    annotation.update()
    items = _span_items(page)
    assert len(items) == 1

    with_text = bl_import_engine._plan_page_raster(page, 1, _config(), str(tmp_path), [])
    without_text = bl_import_engine._plan_page_raster(page, 1, _config(), str(tmp_path), items)

    assert with_text["rendered"]["has_ink"] is True
    assert without_text["rendered"]["has_ink"] is False
    document.close()


def test_scanned_page_always_shows_ink(tmp_path) -> None:
    """A page that is one image: nothing is delivered on top, the raster is the page."""
    document = fitz.open()
    page = document.new_page(width=LETTER_PT[0], height=LETTER_PT[1])
    scan = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 64, 80), False)
    scan.clear_with(250)
    for x in range(10, 50):
        scan.set_pixel(x, 40, (40, 40, 40))
    page.insert_image(page.rect, pixmap=scan)

    plan = bl_import_engine._plan_page_raster(page, 1, _config(), str(tmp_path), [])

    assert plan["rendered"]["has_ink"] is True
    assert plan["rendered"]["composition"] == "complete_page_raster"
    document.close()


def test_the_planned_render_is_reused_when_exactly_the_planned_text_was_delivered(
    monkeypatch, tmp_path,
) -> None:
    document = fitz.open()
    page = _text_only_page(document)
    items = _span_items(page)
    plan = bl_import_engine._plan_page_raster(page, 1, _config(), str(tmp_path), items)
    calls = []
    original = bl_import_engine._render_page_raster

    def counting(*args, **kwargs):
        calls.append(kwargs.get("excluded_text_bboxes"))
        return original(*args, **kwargs)

    monkeypatch.setattr(bl_import_engine, "_render_page_raster", counting)

    # Same boxes, another order: the planned image is the page raster.
    delivered = [item.source_bbox_pdf for item in reversed(items)]
    same = bl_import_engine._page_raster_for_delivered_text(
        page, 1, _config(), str(tmp_path), excluded_text_bboxes=delivered, plan=plan)
    assert same is plan["rendered"]
    assert calls == []

    # One item was not delivered: rendered again, and its ink is in the image.
    partial = bl_import_engine._page_raster_for_delivered_text(
        page, 1, _config(), str(tmp_path), excluded_text_bboxes=delivered[:1], plan=plan)
    assert calls == [delivered[:1]]
    assert partial is not plan["rendered"]
    assert partial["has_ink"] is True

    # No plan (ignored, failed): rendered for the delivered text as before.
    unplanned = bl_import_engine._page_raster_for_delivered_text(
        page, 1, _config(), str(tmp_path), excluded_text_bboxes=delivered, plan=None)
    assert len(calls) == 2
    assert unplanned["has_ink"] is False
    failed_plan = {"rendered": None, "excluded_text_bboxes": delivered}
    bl_import_engine._page_raster_for_delivered_text(
        page, 1, _config(), str(tmp_path), excluded_text_bboxes=delivered, plan=failed_plan)
    assert len(calls) == 3
    document.close()


def test_a_text_box_that_is_not_numeric_gives_no_plan(tmp_path) -> None:
    document = fitz.open()
    page = _text_only_page(document)
    broken = [types.SimpleNamespace(id=1, text="X", source_bbox_pdf=("a", 0.0, 1.0, 1.0))]

    plan = bl_import_engine._plan_page_raster(page, 1, _config(), str(tmp_path), broken)

    assert plan == {"rendered": None, "excluded_text_bboxes": []}
    document.close()


# --------------------------------------------------------------------------
# The decision record
# --------------------------------------------------------------------------

def _decide(has_ink, *, style="high_contrast", page_style=None, paper=False, trigger="sparse_vector_shell"):
    rendered = {"path": "x.png", "xref": -1}
    if has_ink != "missing":
        rendered["has_ink"] = has_ink
    return bl_import_engine._page_raster_decision(
        rendered,
        page_num=3,
        trigger=trigger,
        requested_style=style,
        page_style=page_style or style,
        paper_for_source_colors=paper,
        delivered_text_bboxes=7,
    )


@pytest.mark.parametrize("style", ["high_contrast", "blueprint", "source"])
def test_blank_render_is_omitted_in_every_style(style) -> None:
    decision = _decide(False, style=style)
    assert decision == {
        "page": 3,
        "trigger": "sparse_vector_shell",
        "has_ink": False,
        "plane": "omitted",
        "reason": "no_ink_left_after_delivered_text",
        "requested_visual_style": style,
        "object_colors": style,
        "delivered_text_bboxes_removed": 7,
        "objects_readable_on_raster": True,
    }


def test_render_with_ink_is_kept_and_source_colors_make_it_readable() -> None:
    decision = _decide(True, style="high_contrast", page_style="source", trigger="raster_page")
    assert (decision["plane"], decision["reason"]) == ("kept", "ink_not_delivered_as_objects")
    assert decision["trigger"] == "raster_page"
    assert decision["object_colors"] == "source"
    assert decision["objects_readable_on_raster"] is True


def test_unmeasured_render_is_kept() -> None:
    for has_ink in (None, "missing"):
        decision = _decide(has_ink, page_style="source")
        assert (decision["plane"], decision["reason"]) == ("kept", "ink_not_measured")
        assert decision["has_ink"] is None


def test_blank_render_stays_as_the_paper_under_source_colors_without_the_page_aid() -> None:
    decision = _decide(False, style="source", paper=True)
    assert (decision["plane"], decision["reason"]) == ("kept", "paper_under_source_colors")
    assert decision["objects_readable_on_raster"] is True


def test_the_record_says_so_when_preview_colors_end_up_on_a_kept_raster() -> None:
    """Text delivery failed after the page was styled: reported, not hidden."""
    decision = _decide(True, style="high_contrast", page_style="high_contrast")
    assert decision["plane"] == "kept"
    assert decision["objects_readable_on_raster"] is False


def test_a_page_imported_again_replaces_its_record() -> None:
    stats = {}
    bl_import_engine._record_page_raster_decision(stats, {"page": 1, "plane": "kept"})
    bl_import_engine._record_page_raster_decision(stats, {"page": 2, "plane": "kept"})
    bl_import_engine._record_page_raster_decision(stats, {"page": 1, "plane": "omitted"})
    assert stats["page_raster_decisions"] == [
        {"page": 2, "plane": "kept"},
        {"page": 1, "plane": "omitted"},
    ]


def test_import_report_carries_the_decisions(tmp_path) -> None:
    decision = _decide(False)
    report_path = tmp_path / "report.json"
    bl_import_engine.write_import_report(
        "drawing.pdf",
        {"import_report_path": str(report_path)},
        {"pages_imported": 1, "page_raster_decisions": [decision]},
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))

    def find(node):
        if isinstance(node, dict):
            if "page_raster_decisions" in node:
                return node["page_raster_decisions"]
            for value in node.values():
                found = find(value)
                if found is not None:
                    return found
        return None

    assert find(report) == [decision]


# --------------------------------------------------------------------------
# The page loop of import_pdf, with a fake bpy
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_unrelated_paint_order_and_paper_seams(monkeypatch):
    from pdf_vector_importer import (
        opaque_rectangle_proof, page_background, text_page_clip, triangle_paint_order,
    )
    monkeypatch.setattr(opaque_rectangle_proof, "plan_opaque_rectangles", lambda *_a, **_k: [])
    monkeypatch.setattr(triangle_paint_order, "plan_terminal_triangles", lambda *_a, **_k: ([], []))
    monkeypatch.setattr(triangle_paint_order, "apply_terminal_triangles", lambda *_a, **_k: [])
    monkeypatch.setattr(text_page_clip, "clip_delivered_page_text", lambda *_a, **_k: [])
    backgrounds = []

    def add_page_background(_collection, _width, _height, *, enabled=True, style="source"):
        backgrounds.append({"enabled": enabled, "style": style})
        return None

    monkeypatch.setattr(page_background, "add_page_background", add_page_background)
    return backgrounds


class _Children:
    def __init__(self):
        self.items = []

    def link(self, item):
        self.items.append(item)


class _Collection(dict):
    __eq__ = object.__eq__
    __hash__ = object.__hash__

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.children = _Children()
        self.all_objects = []


class _Collections:
    def __init__(self):
        self.items = []

    def new(self, name):
        collection = _Collection(name)
        self.items.append(collection)
        return collection

    def remove(self, value, **_kwargs):
        self.items.remove(value)


class _FakeBpy:
    def __init__(self):
        self.app = types.SimpleNamespace(version=(5, 2, 0), background=True)
        self.data = types.SimpleNamespace(
            collections=_Collections(),
            objects=types.SimpleNamespace(get=lambda _name: None, remove=lambda *_a, **_k: None),
        )
        self.context = types.SimpleNamespace(
            scene=types.SimpleNamespace(collection=types.SimpleNamespace(children=_Children())),
            view_layer=types.SimpleNamespace(update=lambda: None),
        )


def _text_item(item_id: int, row: int):
    top = 72.0 + 20.0 * row
    return types.SimpleNamespace(
        id=item_id,
        text=f"FICTIONAL LINE {item_id}",
        source_bbox_pdf=(72.0, top, 300.0, top + 12.0),
    )


def _vector_primitives(count: int = 40):
    """Short strokes spread over the sheet: a drawing, not a page frame."""
    return [
        Primitive(
            id=index + 1,
            type="line",
            points=[(10.0 + index, 20.0), (10.0 + index, 60.0)],
            bbox=(10.0 + index, 20.0, 10.0 + index, 60.0),
            stroke_color=(0.0, 0.0, 0.0),
            line_width=0.25,
            page_number=1,
        )
        for index in range(count)
    ]


def _page_spec(*, text=0, nontext_ink=False, primitives=None, embedded=None,
               undelivered=(), unmeasured=False, scan=False, embedded_error=None,
               render_fails=False):
    """One synthetic page.

    ``nontext_ink``: the page shows something that is neither delivered text
    nor an embedded image (a shading, a form, a stroke the extractor dropped).
    ``undelivered``: ids of text items whose delivery fails.
    ``scan``: no drawings, no text, the page is one picture (auto -> raster).
    """
    return {
        "text": [_text_item(index + 1, index) for index in range(text)],
        "nontext_ink": bool(nontext_ink or scan),
        "primitives": list(primitives or []),
        "embedded": embedded,
        "undelivered": set(undelivered),
        "unmeasured": bool(unmeasured),
        "scan": bool(scan),
        "embedded_error": embedded_error,
        "render_fails": bool(render_fails),
    }


class _Run:
    """What one import did, page by page."""

    def __init__(self):
        self.stats = None
        self.error = None
        self.renders = []        # (page, sorted excluded boxes)
        self.planes = []         # (page, placement)
        self.text_styles = {}    # page -> visual_style handed to the text builder
        self.vector_styles = {}  # page -> visual_style handed to the geometry builder
        self.extractions = []    # pages whose embedded images were read
        self.messages = []
        self.report_stats = None
        self.backgrounds = None

    def decisions(self):
        return {record["page"]: record for record in self.stats.get("page_raster_decisions", [])}

    def raster_planes(self, page):
        return [placement for number, placement in self.planes
                if number == page and placement.get("xref") == -1]

    def auto_messages(self, page):
        return [message for message in self.messages
                if "Auto-mode" in message and f"page {page}" in message]


def _import(monkeypatch, tmp_path: Path, pages, *, config=None, expect_incomplete=False) -> _Run:
    run = _Run()
    engine = bl_import_engine

    class Page:
        rotation = 0

        def __init__(self, number):
            self.number = number - 1
            self.rect = fitz.Rect(0.0, 0.0, *LETTER_PT)
            self.mediabox = types.SimpleNamespace(width=LETTER_PT[0], height=LETTER_PT[1])

        def get_drawings(self, **_kwargs):
            # One stroked path per primitive, in the shape PyMuPDF reports them.
            return [
                {
                    "type": "s", "color": (0.0, 0.0, 0.0), "fill": None, "width": 0.25,
                    "rect": fitz.Rect(30.0 + index, 60.0, 30.0 + index, 170.0),
                    "items": [("l", fitz.Point(30.0 + index, 60.0), fitz.Point(30.0 + index, 170.0))],
                }
                for index in range(len(pages[self.number]["primitives"]))
            ]

        def get_image_info(self, **_kwargs):
            return []

        def get_text(self, kind="text", *_args, **_kwargs):
            # Auto mode only counts blocks and words to tell a text page from a scan.
            if kind in ("blocks", "words"):
                return [object()] * len(pages[self.number]["text"])
            return {"blocks": []} if kind in ("dict", "rawdict") else ""

    class Document:
        page_count = len(pages)
        is_closed = False

        def load_page(self, index):
            return Page(index + 1)

        def close(self):
            self.is_closed = True

    def page_data(number):
        spec = pages[number - 1]
        return types.SimpleNamespace(
            page_number=number,
            primitives=list(spec["primitives"]),
            text_items=list(spec["text"]),
            width=LETTER_PT[0] * PT_TO_MM,
            height=LETTER_PT[1] * PT_TO_MM,
            resolved_scale=None,
        )

    def fake_iter_pages(_doc, pages, **_kwargs):
        for number in list(pages):
            yield int(number), page_data(int(number))

    def fake_render(page, page_num, _import_cfg, image_dir, *, excluded_text_bboxes=()):
        spec = pages[page_num - 1]
        excluded = sorted(tuple(box) for box in excluded_text_bboxes)
        run.renders.append((page_num, excluded))
        if spec["render_fails"]:
            return None
        text_left = [item for item in spec["text"] if tuple(item.source_bbox_pdf) not in excluded]
        path = Path(image_dir) / f"page_{page_num:03d}_raster.png"
        path.write_bytes(b"synthetic")
        placement = {
            "path": str(path), "x_mm": 0.0, "y_mm": 0.0,
            "width_mm": LETTER_PT[0] * PT_TO_MM, "height_mm": LETTER_PT[1] * PT_TO_MM,
            "xref": -1, "page_number": page_num,
            "excluded_text_bbox_count": len(excluded),
        }
        if not spec["unmeasured"]:
            placement["has_ink"] = bool(spec["nontext_ink"] or text_left)
        return placement

    def fake_extract(_doc, _page, page_num, _import_cfg, image_dir):
        run.extractions.append(page_num)
        spec = pages[page_num - 1]
        if spec["embedded_error"]:
            raise engine.EmbeddedImageDeliveryError(spec["embedded_error"])
        if not spec["embedded"]:
            return []
        path = Path(image_dir) / f"page_{page_num:03d}_xref_7.png"
        path.write_bytes(b"synthetic")
        return [{"path": str(path), "x_mm": 10.0, "y_mm": 10.0, "width_mm": 20.0,
                 "height_mm": 20.0, "xref": 7, "page_number": page_num, "source_kind": "xobject"}]

    def fake_plane(placement, collection, **_kwargs):
        run.planes.append((int(placement["page_number"]), dict(placement)))
        obj = types.SimpleNamespace(name=f"PDF_Image_{placement['page_number']}_{placement['xref']}",
                                    type="MESH", parent=None, location=[0.0, 0.0, 0.0],
                                    get=lambda _key, default=None: default)
        collection.all_objects.append(obj)
        return obj

    def fake_build_page(data, collection, builder_config, **_kwargs):
        run.vector_styles[data.page_number] = builder_config["visual_style"]
        return {}

    def fake_build_all_text(items, _collection, page_number, **kwargs):
        if items:  # the builder also runs for a page without text; nothing is styled then
            run.text_styles[page_number] = kwargs["visual_style"]
        opts = kwargs["provenance_opts"]
        records = list(getattr(opts, "_text_delivery_records", ()) or ())
        spec = pages[page_number - 1]
        delivered = 0
        for item in items:
            failed = item.id in spec["undelivered"]
            delivered += 0 if failed else 1
            records.append({
                "item_id": f"page:{page_number}:text:{item.id}",
                "page": page_number,
                "source_span_id": item.id,
                "requested_representation": "3d_text",
                "final_representation": None if failed else "3d_text",
                "status": "failed" if failed else "delivered",
                "fallback_used": False,
                "entity_ids": [] if failed else [f"P{page_number}_text_{item.id}"],
                "attempts": [],
            })
        opts._text_delivery_records = records
        return delivered

    def capture_report(_filepath, _config, stats, **_kwargs):
        run.report_stats = dict(stats)
        return str(tmp_path / "import_report.json")

    monkeypatch.setattr(engine, "bpy", _FakeBpy())
    monkeypatch.setattr(engine, "check_pymupdf", lambda: True)
    monkeypatch.setattr(engine, "ensure_lib_path", lambda: None)
    monkeypatch.setattr(fitz_loader, "import_fitz", lambda **_kwargs: object())
    monkeypatch.setattr(fitz_loader, "safe_open", lambda _path: Document())
    monkeypatch.setattr(engine, "iter_pages", fake_iter_pages)
    monkeypatch.setattr(engine, "extract_page", lambda _page, number, **_kwargs: page_data(number))
    monkeypatch.setattr(engine, "_render_page_raster", fake_render)
    monkeypatch.setattr(engine, "_extract_image_placements", fake_extract)
    monkeypatch.setattr(engine, "_create_image_plane", fake_plane)
    monkeypatch.setattr(engine, "build_page", fake_build_page)
    monkeypatch.setattr(engine, "build_all_text", fake_build_all_text)
    monkeypatch.setattr(engine, "_reverify_text_delivery_after_stack", lambda *_a, **_k: [])
    monkeypatch.setattr(engine, "write_import_report", capture_report)
    monkeypatch.setattr(engine.tempfile, "mkdtemp", lambda **_kwargs: str(tmp_path))

    input_pdf = tmp_path / "input.pdf"
    input_pdf.write_bytes(b"%PDF-1.7\n")
    run_config = {
        "mode": "auto",
        "pages": f"1-{len(pages)}" if len(pages) > 1 else "1",
        "import_text": True,
        "text_mode": "3d_text",
        "visual_style": "high_contrast",
        "auto_focus_view": False,
        "auto_hide_default_cube": False,
    }
    run_config.update(config or {})

    def on_progress(_fraction, message):
        run.messages.append(str(message))

    if expect_incomplete:
        with pytest.raises(engine.IncompleteImportError) as caught:
            engine.import_pdf(str(input_pdf), config=run_config, progress_callback=on_progress)
        run.error = caught.value
        run.stats = caught.value.stats
    else:
        run.stats = engine.import_pdf(
            str(input_pdf), config=run_config, progress_callback=on_progress)
    return run


@pytest.mark.parametrize("style", ["high_contrast", "blueprint"])
def test_text_only_page_gets_no_page_raster_and_keeps_the_style_colors(
    monkeypatch, tmp_path, style,
) -> None:
    run = _import(monkeypatch, tmp_path, [_page_spec(text=34)], config={"visual_style": style})

    assert run.raster_planes(1) == []
    assert run.planes == []
    assert run.text_styles == {1: style}
    assert run.stats["raster_pages_imported"] == 0
    assert run.stats["images"] == 0
    assert run.stats["text_items"] == 34
    assert run.stats["raster_delivery_failures"] == []
    decision = run.decisions()[1]
    assert decision["plane"] == "omitted"
    assert decision["reason"] == "no_ink_left_after_delivered_text"
    assert decision["trigger"] == "sparse_vector_shell"
    assert decision["has_ink"] is False
    assert decision["object_colors"] == style
    assert decision["delivered_text_bboxes_removed"] == 34
    # Rendered once: the plan is the answer because all planned text was delivered.
    assert [page for page, _boxes in run.renders] == [1]
    assert len(run.renders[0][1]) == 34
    # The progress line no longer claims a raster fallback that did not happen.
    assert not any("raster fallback" in message for message in run.messages)
    assert any("no page raster" in message for message in run.auto_messages(1))
    # The report gets the same record.
    assert run.report_stats["page_raster_decisions"] == [decision]


@pytest.mark.parametrize("style", ["high_contrast", "blueprint"])
def test_page_whose_raster_carries_ink_keeps_it_and_builds_in_source_colors(
    monkeypatch, tmp_path, style,
) -> None:
    run = _import(
        monkeypatch, tmp_path, [_page_spec(text=5, nontext_ink=True)],
        config={"visual_style": style})

    assert len(run.raster_planes(1)) == 1
    assert run.text_styles == {1: "source"}
    assert run.vector_styles == {1: "source"}
    assert run.stats["raster_pages_imported"] == 1
    decision = run.decisions()[1]
    assert decision["plane"] == "kept"
    assert decision["reason"] == "ink_not_delivered_as_objects"
    assert decision["requested_visual_style"] == style
    assert decision["object_colors"] == "source"
    assert decision["objects_readable_on_raster"] is True
    assert [page for page, _boxes in run.renders] == [1]
    assert any("raster fallback" in message for message in run.auto_messages(1))
    assert any("source colors" in message for message in run.auto_messages(1))
    # Readable: source text is black on the white paper of the raster.
    assert WHITE_PAPER_LUMINANCE - _luminance(preview_color((0.0, 0.0, 0.0), "source")) >= 0.5


def test_the_decision_is_per_page(monkeypatch, tmp_path, _isolate_unrelated_paint_order_and_paper_seams) -> None:
    pages = [
        _page_spec(text=3),                                   # text-only cover page
        _page_spec(text=4, nontext_ink=True),                 # sparse page with undelivered ink
        _page_spec(text=6, primitives=_vector_primitives()),  # a drawing
        _page_spec(scan=True),                                # a scanned page
        _page_spec(text=2, embedded=True),                    # text and an embedded picture
    ]
    run = _import(monkeypatch, tmp_path, pages)

    assert run.stats["pages_imported"] == 5
    # Page 1: no raster, preview colors. Page 2: raster, source colors.
    assert run.raster_planes(1) == [] and run.text_styles[1] == "high_contrast"
    assert len(run.raster_planes(2)) == 1 and run.text_styles[2] == "source"
    assert run.vector_styles[1] == "high_contrast" and run.vector_styles[2] == "source"
    # Page 3: a drawing is not a raster candidate; nothing is rendered or decided for it.
    assert run.raster_planes(3) == []
    assert run.text_styles[3] == "high_contrast" and run.vector_styles[3] == "high_contrast"
    assert 3 not in run.decisions()
    assert 3 not in [page for page, _boxes in run.renders]
    assert run.auto_messages(3) == []
    # Page 4: the scan keeps its raster; nothing is built on it and nothing is announced.
    assert len(run.raster_planes(4)) == 1
    assert 4 not in run.text_styles and 4 not in run.vector_styles
    assert run.decisions()[4]["plane"] == "kept"
    assert run.decisions()[4]["trigger"] == "raster_page"
    assert not any("source colors" in message for message in run.auto_messages(4))
    # Page 5: an embedded picture is not a page raster; the style is untouched.
    assert run.raster_planes(5) == []
    assert [placement["xref"] for page, placement in run.planes if page == 5] == [7]
    assert run.text_styles[5] == "high_contrast"
    assert 5 not in run.decisions()

    assert sorted(run.decisions()) == [1, 2, 4]
    assert run.stats["raster_pages_imported"] == 2
    # The white page aid still follows the style the user chose, on every page.
    backgrounds = _isolate_unrelated_paint_order_and_paper_seams
    assert [entry["style"] for entry in backgrounds] == ["high_contrast"] * 5


def test_scanned_page_is_unchanged(monkeypatch, tmp_path) -> None:
    run = _import(monkeypatch, tmp_path, [_page_spec(scan=True)])

    assert len(run.raster_planes(1)) == 1
    assert run.raster_planes(1)[0]["excluded_text_bbox_count"] == 0
    assert run.stats["raster_pages_imported"] == 1
    assert run.stats["images"] == 1
    assert run.text_styles == {} and run.vector_styles == {}
    assert run.renders == [(1, [])]
    assert run.decisions()[1]["reason"] == "ink_not_delivered_as_objects"
    assert [message for message in run.auto_messages(1) if "source colors" in message] == []


def test_embedded_images_of_a_sparse_page_are_read_once(monkeypatch, tmp_path) -> None:
    run = _import(monkeypatch, tmp_path, [
        _page_spec(text=2),
        _page_spec(text=2, embedded=True),
        _page_spec(text=2, primitives=_vector_primitives()),
    ])
    assert run.extractions == [1, 2, 3]
    assert run.stats["images"] == 1


def test_source_style_drops_the_blank_raster_when_the_white_page_aid_is_the_paper(
    monkeypatch, tmp_path, _isolate_unrelated_paint_order_and_paper_seams,
) -> None:
    run = _import(monkeypatch, tmp_path, [_page_spec(text=8)], config={"visual_style": "source"})

    assert run.raster_planes(1) == []
    assert run.text_styles == {1: "source"}
    assert run.decisions()[1]["plane"] == "omitted"
    assert _isolate_unrelated_paint_order_and_paper_seams == [{"enabled": True, "style": "source"}]


def test_source_style_without_the_white_page_aid_keeps_the_blank_raster_as_paper(
    monkeypatch, tmp_path,
) -> None:
    run = _import(
        monkeypatch, tmp_path, [_page_spec(text=8)],
        config={"visual_style": "source", "white_page_background": False})

    assert len(run.raster_planes(1)) == 1
    assert run.text_styles == {1: "source"}
    decision = run.decisions()[1]
    assert (decision["plane"], decision["reason"]) == ("kept", "paper_under_source_colors")
    assert decision["objects_readable_on_raster"] is True


def test_preview_style_ignores_the_white_page_aid_switch(monkeypatch, tmp_path) -> None:
    run = _import(
        monkeypatch, tmp_path, [_page_spec(text=8)],
        config={"visual_style": "high_contrast", "white_page_background": False})
    assert run.raster_planes(1) == []
    assert run.decisions()[1]["plane"] == "omitted"


def test_unmeasured_render_keeps_the_raster_and_uses_source_colors(monkeypatch, tmp_path) -> None:
    """A renderer that cannot say whether there is ink must not cost the page its raster."""
    run = _import(monkeypatch, tmp_path, [_page_spec(text=3, unmeasured=True)])

    assert len(run.raster_planes(1)) == 1
    assert run.text_styles == {1: "source"}
    decision = run.decisions()[1]
    assert (decision["plane"], decision["reason"]) == ("kept", "ink_not_measured")
    assert decision["objects_readable_on_raster"] is True


def test_text_not_imported_leaves_its_ink_in_the_raster(monkeypatch, tmp_path) -> None:
    run = _import(monkeypatch, tmp_path, [_page_spec(text=5)], config={"import_text": False})

    assert run.renders == [(1, [])]
    assert len(run.raster_planes(1)) == 1
    assert run.text_styles == {}
    assert run.vector_styles == {1: "source"}
    assert run.decisions()[1]["plane"] == "kept"


def test_ignoring_images_never_renders_or_restyles(monkeypatch, tmp_path) -> None:
    run = _import(
        monkeypatch, tmp_path, [_page_spec(text=5, nontext_ink=True)],
        config={"ignore_images": True})

    assert run.renders == [] and run.planes == [] and run.extractions == []
    assert run.text_styles == {1: "high_contrast"}
    assert run.vector_styles == {1: "high_contrast"}
    assert "page_raster_decisions" not in run.stats


def test_explicit_raster_mode_follows_the_same_rule(monkeypatch, tmp_path) -> None:
    run = _import(
        monkeypatch, tmp_path,
        [_page_spec(text=4), _page_spec(text=4, nontext_ink=True)],
        config={"mode": "raster"})

    assert run.raster_planes(1) == [] and len(run.raster_planes(2)) == 1
    assert run.text_styles == {1: "high_contrast", 2: "source"}
    assert run.vector_styles == {}
    assert {page: record["trigger"] for page, record in run.decisions().items()} == {
        1: "raster_page", 2: "raster_page"}
    assert run.extractions == []


def test_undelivered_text_keeps_the_raster_and_the_record_admits_the_preview_colors(
    monkeypatch, tmp_path,
) -> None:
    """Known limit: the page is styled before text delivery can fail.

    The plan (all text removed) is blank, so the page is built in the preview
    colors. One item then fails; its ink is in the raster, which stays. The
    import already ends as incomplete, and the record reports that the
    delivered text is not readable on that raster instead of claiming it is.
    """
    run = _import(
        monkeypatch, tmp_path, [_page_spec(text=3, undelivered={2})], expect_incomplete=True)

    assert "text delivery failed" in " ".join(run.error.failures)
    assert len(run.raster_planes(1)) == 1
    assert run.text_styles == {1: "high_contrast"}
    # Rendered twice: the plan, then again without the two delivered items only.
    assert [len(boxes) for _page, boxes in run.renders] == [3, 2]
    decision = run.decisions()[1]
    assert decision["plane"] == "kept"
    assert decision["has_ink"] is True
    assert decision["object_colors"] == "high_contrast"
    assert decision["objects_readable_on_raster"] is False
    assert decision["delivered_text_bboxes_removed"] == 2
    assert any("preview-colored" in message and "page_raster_decisions" in message
               for message in run.messages)


def test_embedded_image_failure_on_a_sparse_page_still_fails_the_page(monkeypatch, tmp_path) -> None:
    run = _import(
        monkeypatch, tmp_path,
        [_page_spec(text=2, embedded_error="page 1 image xref 7 soft-mask 8 could not be extracted")],
        expect_incomplete=True)

    assert run.extractions == [1]
    assert run.renders == [] and run.planes == []
    assert run.stats["pages_imported"] == 0
    assert run.stats["raster_delivery_failures"] == [{
        "page": 1,
        "stage": "embedded_image",
        "reason": "page 1 image xref 7 soft-mask 8 could not be extracted",
    }]
    # Built as before the failure was known: preview colors, then discarded.
    assert run.text_styles == {1: "high_contrast"}


def test_failed_render_is_still_a_raster_delivery_failure(monkeypatch, tmp_path) -> None:
    run = _import(
        monkeypatch, tmp_path, [_page_spec(text=2, render_fails=True)], expect_incomplete=True)

    # Tried for the plan, then again for the delivered text, as before the plan existed.
    assert [page for page, _boxes in run.renders] == [1, 1]
    assert run.planes == []
    assert run.text_styles == {1: "high_contrast"}
    assert run.stats["raster_delivery_failures"] == [{
        "page": 1, "stage": "render", "reason": "raster_render_failed",
    }]
    assert "page_raster_decisions" not in run.stats
