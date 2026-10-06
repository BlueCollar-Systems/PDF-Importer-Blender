"""Regression: stacked pages of a multi-page import must never overlap.

Pages are stacked downward (-Y), left aligned at x = 0, and a placed page
covers ``[offset, offset + height]``. The stack offset used to advance by the
step of the page just placed only, so a following page taller than that step
reached up into the page above it (792x612 pt then 612x792 pt: 20.3 mm of
overlap in the default "spread" layout; any taller page in "touch").

The offset of a page is now computed from the lowest placed page and the
height of the page being placed, so the arrangement gap (20 percent, the
compact ratio, or zero for "touch") is measured between facing page edges.

Fixtures are synthetic page sizes only; no PDF bytes are involved.
"""
from __future__ import annotations

import hashlib
import importlib
import struct
import sys
import types
from pathlib import Path

import pytest


MM_TO_M = 0.001
PT_TO_MM = 25.4 / 72.0
# Letter landscape, Letter portrait, Tabloid landscape (width_pt, height_pt).
LANDSCAPE = (792.0, 612.0)
PORTRAIT = (612.0, 792.0)
WIDE = (1224.0, 792.0)
# Float noise allowed where two facing edges are meant to coincide ("touch").
EDGE_TOLERANCE_M = 1e-12

_MISSING = object()
_ISOLATED_MODULES = (
    "bpy",
    "bmesh",
    "pdf_vector_importer.bl_geometry_builder",
    "pdf_vector_importer.bl_import_engine",
)


@pytest.fixture(autouse=True)
def _restore_replaced_modules():
    previous = {name: sys.modules.get(name, _MISSING) for name in _ISOLATED_MODULES}
    yield
    for name, module in previous.items():
        if module is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _install_blender_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_bpy = types.SimpleNamespace(
        app=types.SimpleNamespace(version=(5, 2, 0)),
        ops=types.SimpleNamespace(
            wm=types.SimpleNamespace(redraw_timer=lambda **_kwargs: None),
        ),
        types=types.SimpleNamespace(
            Collection=object,
            Material=object,
            Object=object,
            VectorFont=object,
        ),
    )
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    monkeypatch.setitem(sys.modules, "bmesh", types.SimpleNamespace())


def _engine(monkeypatch: pytest.MonkeyPatch):
    _install_blender_stubs(monkeypatch)
    return importlib.import_module("pdf_vector_importer.bl_import_engine")


def _heights_m(sizes_pt):
    # The engine's own conversion: page_data.height (mm) * 0.001.
    return [(height * PT_TO_MM) * MM_TO_M for _width, height in sizes_pt]


def _layout(engine, heights_m, arrangement, gap_ratio=0.20):
    """Offsets from the engine's rule, driven exactly like the page loop."""
    arrangement = engine._normalize_page_arrangement(arrangement)
    gap_ratio = engine._normalize_page_gap_ratio(gap_ratio)
    cursor = 0.0
    placed_height = None
    offsets = []
    for height in heights_m:
        offset = engine._page_stack_offset(cursor, placed_height, height, arrangement)
        offsets.append(offset)
        cursor = offset - engine._page_stack_step(height, arrangement, gap_ratio)
        placed_height = height
    return offsets


def _previous_rule(engine, heights_m, arrangement, gap_ratio=0.20):
    """The rule before this fix, kept verbatim as the reference:

        offset used for the page;  offset -= _page_stack_step(height just placed)
    """
    arrangement = engine._normalize_page_arrangement(arrangement)
    gap_ratio = engine._normalize_page_gap_ratio(gap_ratio)
    offset = 0.0
    offsets = []
    for height in heights_m:
        offsets.append(offset)
        offset -= engine._page_stack_step(height, arrangement, gap_ratio)
    return offsets


def _bits(value: float) -> bytes:
    return struct.pack("<d", value)


def _rects(offsets, heights_m):
    assert len(offsets) == len(heights_m)
    return [(offset, offset + heights_m[index]) for index, offset in enumerate(offsets)]


def _overlap_m(rect_a, rect_b) -> float:
    """Shared Y extent of two page rectangles (pages share x = 0 .. width)."""
    return min(rect_a[1], rect_b[1]) - max(rect_a[0], rect_b[0])


def _assert_no_overlap(rects) -> None:
    for first in range(len(rects)):
        for second in range(first + 1, len(rects)):
            assert _overlap_m(rects[first], rects[second]) <= EDGE_TOLERANCE_M, (
                first + 1, second + 1, rects)


def _expected_gap_m(height_above_m, arrangement, gap_ratio):
    if arrangement == "touch":
        return 0.0
    if arrangement == "compact":
        return height_above_m * gap_ratio
    return height_above_m * 0.20


# --------------------------------------------------------------------------
# The rule itself
# --------------------------------------------------------------------------

def test_previous_rule_overlapped_and_the_new_rule_does_not(monkeypatch) -> None:
    """The measured defect: landscape, portrait, wide; page 2 reached 20.3 mm into page 1."""
    engine = _engine(monkeypatch)
    heights = _heights_m([LANDSCAPE, PORTRAIT, WIDE])

    before = _rects(_previous_rule(engine, heights, "spread"), heights)
    assert _overlap_m(before[0], before[1]) == pytest.approx(0.02032, abs=1e-9)

    after = _rects(_layout(engine, heights, "spread"), heights)
    _assert_no_overlap(after)
    # Page 1 is still at the origin; page 2 top sits 20 percent of page 1 below it.
    assert after[0] == (0.0, heights[0])
    assert after[0][0] - after[1][1] == pytest.approx(0.20 * heights[0], abs=EDGE_TOLERANCE_M)
    assert after[1][0] - after[2][1] == pytest.approx(0.20 * heights[1], abs=EDGE_TOLERANCE_M)


@pytest.mark.parametrize("arrangement,gap_ratio", [
    ("spread", 0.20),
    ("touch", 0.20),
    ("compact", 0.20),
    ("compact", 0.05),
    ("compact", 0.0),
])
@pytest.mark.parametrize("sizes", [
    [LANDSCAPE, PORTRAIT],            # short page, then a taller one
    [PORTRAIT, LANDSCAPE],            # tall page, then a shorter one
    [LANDSCAPE, PORTRAIT, WIDE],      # three pages, mixed
    [WIDE, LANDSCAPE, PORTRAIT],
    [PORTRAIT, LANDSCAPE, PORTRAIT, LANDSCAPE, WIDE, (200.0, 3000.0), (3000.0, 100.0)],
])
def test_mixed_heights_never_intersect_and_gap_is_between_facing_edges(
    monkeypatch, arrangement, gap_ratio, sizes,
) -> None:
    engine = _engine(monkeypatch)
    heights = _heights_m(sizes)
    offsets = _layout(engine, heights, arrangement, gap_ratio)
    rects = _rects(offsets, heights)

    assert offsets[0] == 0.0
    _assert_no_overlap(rects)
    for index in range(1, len(rects)):
        bottom_above = rects[index - 1][0]
        top_below = rects[index][1]
        assert bottom_above - top_below == pytest.approx(
            _expected_gap_m(heights[index - 1], arrangement, gap_ratio),
            abs=EDGE_TOLERANCE_M,
        )
        # Each page is below everything placed before it.
        assert top_below <= min(rect[0] for rect in rects[:index]) + EDGE_TOLERANCE_M


@pytest.mark.parametrize("arrangement,gap_ratio", [
    ("spread", 0.20),
    ("touch", 0.20),
    ("compact", 0.20),
    ("compact", 0.07),
    ("compact", 1.0),
    ("overlay", 0.20),
])
@pytest.mark.parametrize("height_pt", [612.0, 792.0, 799.0, 1224.0, 2448.0, 3.0, 0.5])
def test_equal_size_stacks_keep_their_previous_positions_bit_for_bit(
    monkeypatch, arrangement, gap_ratio, height_pt,
) -> None:
    """Proof that single-page and equal-size results did not move.

    For equal heights the correction in ``_page_stack_offset`` is
    ``max(0.001, h) - max(0.001, h)``, which is exactly ``0.0``, and IEEE 754
    ``x + 0.0`` returns ``x`` unchanged. The cursor is the same single
    subtraction the previous rule performed (``offset - step``). So every
    offset has the identical 8 bytes; this test compares those bytes against
    the previous rule for 1 to 40 pages.
    """
    engine = _engine(monkeypatch)
    for page_count in (1, 2, 3, 13, 40):
        heights = _heights_m([(612.0, height_pt)] * page_count)
        new = _layout(engine, heights, arrangement, gap_ratio)
        old = _previous_rule(engine, heights, arrangement, gap_ratio)
        assert [_bits(value) for value in new] == [_bits(value) for value in old]
    assert _layout(engine, _heights_m([(612.0, height_pt)]), arrangement, gap_ratio) == [0.0]


@pytest.mark.parametrize("sizes", [
    [LANDSCAPE, PORTRAIT],
    [PORTRAIT, LANDSCAPE],
    [LANDSCAPE, PORTRAIT, WIDE],
])
def test_overlay_is_unchanged_every_page_stays_at_the_origin(monkeypatch, sizes) -> None:
    engine = _engine(monkeypatch)
    heights = _heights_m(sizes)
    offsets = _layout(engine, heights, "overlay")
    assert [_bits(value) for value in offsets] == [_bits(0.0)] * len(sizes)
    assert offsets == _previous_rule(engine, heights, "overlay")


def test_unknown_placed_height_keeps_the_cursor(monkeypatch) -> None:
    """First page, and a checkpoint written before the height was recorded."""
    engine = _engine(monkeypatch)
    assert _bits(engine._page_stack_offset(0.0, None, 0.2794, "spread")) == _bits(0.0)
    assert _bits(engine._page_stack_offset(-0.25908, None, 0.2794, "spread")) == _bits(-0.25908)


def test_resume_state_records_the_last_stacked_page_height() -> None:
    session = importlib.import_module("pdf_vector_importer.import_session")
    common = dict(
        source_sha256="a" * 64,
        config_sha256="b" * 64,
        requested_pages=[1, 2],
        completed_pages=[1],
        root_collection="PDF Import - synthetic",
        next_stack_offset_m=-0.25908,
        aggregate_stats={},
        text_delivery_items=[],
    )
    assert "stacked_page_height_m" not in session.build_resume_state(**common)
    state = session.build_resume_state(**common, stacked_page_height_m=0.2159)
    assert state["stacked_page_height_m"] == 0.2159
    assert state["next_stack_offset_m"] == -0.25908


# --------------------------------------------------------------------------
# The page loop of import_pdf, with a fake bpy
# --------------------------------------------------------------------------

class _PageObject:
    """Stands for everything a page delivers; its origin is the page origin."""

    type = "MESH"
    parent = None

    def __init__(self, page_number: int, width_m: float, height_m: float) -> None:
        self.name = f"P{page_number}_sheet"
        self.page_number = page_number
        self.width_m = width_m
        self.height_m = height_m
        self.location = [0.0, 0.0, 0.0]

    def get(self, _key, default=None):
        # No importer custom properties: plain page geometry.
        return default

    def world_rect(self):
        x, y = self.location[0], self.location[1]
        return (x, y, x + self.width_m, y + self.height_m)


def _import_stack(monkeypatch, tmp_path: Path, sizes_pt, arrangement,
                  *, gap_ratio=None, resume=None, config_extra=None,
                  real_pdf=False, cancel_after_page=None):
    """Run engine.import_pdf over synthetic pages; return (page objects, checkpoints).

    ``resume`` = (completed page count, extra build_resume_state keywords).
    ``config_extra`` overrides import options (visual style, view focus).
    ``real_pdf`` keeps the actual PDF opening/extraction path for API tests;
    only native Blender delivery is stubbed. Cancellation occurs between pages.
    """
    masks = importlib.import_module("pdf_vector_importer.opaque_rectangle_proof")
    triangles = importlib.import_module("pdf_vector_importer.triangle_paint_order")
    paper = importlib.import_module("pdf_vector_importer.page_background")
    monkeypatch.setattr(masks, "plan_opaque_rectangles", lambda *_a, **_k: [])
    monkeypatch.setattr(triangles, "plan_terminal_triangles", lambda *_a, **_k: ([], []))
    monkeypatch.setattr(triangles, "apply_terminal_triangles", lambda *_a, **_k: [])
    monkeypatch.setattr(paper, "add_page_background", lambda *_a, **_k: None)

    engine = _engine(monkeypatch)
    fitz_loader = importlib.import_module("pdf_vector_importer.pdfcadcore.fitz_loader")
    session = importlib.import_module("pdf_vector_importer.import_session")

    class Children:
        def __init__(self):
            self.items = []

        def link(self, value):
            self.items.append(value)

    class Collection(dict):
        __eq__ = object.__eq__

        def __init__(self, name):
            self.name = name
            self.children = Children()
            self.all_objects = []

    class Collections:
        def __init__(self, initial):
            self.items = list(initial)

        def new(self, name):
            value = Collection(name)
            self.items.append(value)
            return value

        def get(self, name):
            return next((item for item in self.items if item.name == name), None)

        def remove(self, value, **_kwargs):
            self.items.remove(value)

    from pymupdf import Rect

    class Page:
        rotation = 0

        def __init__(self, width_pt, height_pt):
            self.rect = Rect(0.0, 0.0, width_pt, height_pt)
            self.mediabox = Rect(self.rect)
            self.cropbox = Rect(self.rect)

        def get_drawings(self, **_kwargs):
            return []

        def get_image_info(self, **_kwargs):
            return []

    class Document:
        page_count = len(sizes_pt)
        is_closed = False

        def load_page(self, index):
            return Page(*sizes_pt[index])

        def close(self):
            self.is_closed = True

    root = Collection("PDF Import - input")
    collections = Collections([root] if resume else [])
    fake_bpy = types.SimpleNamespace(
        app=types.SimpleNamespace(version=(5, 2, 0)),
        data=types.SimpleNamespace(
            collections=collections,
            objects=types.SimpleNamespace(remove=lambda *_args, **_kwargs: None),
        ),
        context=types.SimpleNamespace(
            scene=types.SimpleNamespace(collection=types.SimpleNamespace(children=Children())),
            view_layer=types.SimpleNamespace(update=lambda: None),
        ),
        types=sys.modules["bpy"].types,
        ops=sys.modules["bpy"].ops,
    )
    monkeypatch.setattr(engine, "bpy", fake_bpy)
    monkeypatch.setattr(engine, "check_pymupdf", lambda: True)
    monkeypatch.setattr(engine, "ensure_lib_path", lambda: None)
    if not real_pdf:
        monkeypatch.setattr(fitz_loader, "import_fitz", lambda **_kwargs: object())
        monkeypatch.setattr(fitz_loader, "safe_open", lambda _path: Document())

    def page_data(page_number):
        width_pt, height_pt = sizes_pt[page_number - 1]
        return types.SimpleNamespace(
            page_number=page_number,
            primitives=[],
            text_items=[],
            width=width_pt * PT_TO_MM,
            height=height_pt * PT_TO_MM,
            resolved_scale=None,
        )

    def fake_iter_pages(_doc, pages, **_kwargs):
        # The engine trims its pending list while it consumes the stream.
        for page_number in list(pages):
            yield int(page_number), page_data(int(page_number))

    if not real_pdf:
        monkeypatch.setattr(engine, "iter_pages", fake_iter_pages)
        # A one-page request does not stream; it extracts the page directly.
        monkeypatch.setattr(
            engine, "extract_page", lambda _page, page_number, **_kwargs: page_data(page_number))
    placed = []

    def fake_build_page(data, collection, *_args, **_kwargs):
        obj = _PageObject(data.page_number, data.width * MM_TO_M, data.height * MM_TO_M)
        obj.primitives = data.primitives
        collection.all_objects.append(obj)
        placed.append(obj)
        return {}

    monkeypatch.setattr(engine, "build_page", fake_build_page)
    monkeypatch.setattr(
        engine, "write_import_report", lambda *_a, **_k: str(tmp_path / "report.json"))
    checkpoints = []
    original_write = engine._write_resume_checkpoint_guarded

    def recording_write(path, state, stats):
        checkpoints.append(dict(state))
        return original_write(path, state, stats)

    monkeypatch.setattr(engine, "_write_resume_checkpoint_guarded", recording_write)

    input_pdf = tmp_path / "input.pdf"
    if real_pdf:
        import pymupdf as fitz

        with fitz.open() as document:
            for width, height in sizes_pt:
                page = document.new_page(width=width, height=height)
                page.draw_line((20, 20), (60, 20), width=1)
            document.save(input_pdf)
    else:
        input_pdf.write_bytes(b"%PDF-1.7\n")
    config = {
        "mode": "vector",
        "pages": f"1-{len(sizes_pt)}",
        "import_text": False,
        "ignore_images": True,
        "auto_focus_view": False,
        "auto_hide_default_cube": False,
        "page_arrangement": arrangement,
    }
    if gap_ratio is not None:
        config["page_gap_ratio"] = gap_ratio
    config.update(config_extra or {})
    run_config = dict(config)
    if resume:
        completed_count, extra = resume
        checkpoint = tmp_path / "resume.json"
        state = session.build_resume_state(
            source_sha256=hashlib.sha256(input_pdf.read_bytes()).hexdigest(),
            config_sha256=session.resume_config_sha256(config),
            requested_pages=list(range(1, len(sizes_pt) + 1)),
            completed_pages=list(range(1, completed_count + 1)),
            root_collection=root.name,
            aggregate_stats={"pages_imported": completed_count, "collections": 1 + completed_count},
            text_delivery_items=[],
            **extra,
        )
        session.write_resume_checkpoint(checkpoint, state)
        run_config.update(resume=True, resume_checkpoint_path=str(checkpoint))

    def progress(_fraction, message):
        if cancel_after_page is not None and message.startswith("Processing page "):
            return int(message.split()[2].split("/")[0]) <= cancel_after_page
        return True

    stats = engine.import_pdf(
        str(input_pdf), config=run_config,
        progress_callback=progress if cancel_after_page is not None else None,
    )
    assert stats["pages_imported"] == (cancel_after_page if cancel_after_page is not None else len(sizes_pt))
    assert stats["cancelled"] is (cancel_after_page is not None)
    return engine, placed, checkpoints


@pytest.mark.parametrize("mode", ["auto", "vector", "raster", "hybrid"])
def test_native_scale_override_is_validated_and_default_stays_one(monkeypatch, mode):
    engine = _engine(monkeypatch)
    config = engine._config_from_mode(mode)
    assert engine._apply_overrides(config, {}).user_scale == 1.0
    assert engine._apply_overrides(config, {"user_scale": 1.25}).user_scale == 1.25
    for invalid in (0, -0.0, -1, float("nan"), float("inf"), -float("inf"),
                    True, False, None, "1.25", [], {}, 10 ** 400):
        with pytest.raises(ValueError, match="user_scale must be a finite positive number"):
            engine._apply_overrides(config, {"user_scale": invalid})
        assert config.user_scale == 1.25


@pytest.mark.parametrize("scale", [None, 0.5, 1.0, 1.25, 2])
@pytest.mark.parametrize("page_count", [1, 2])
def test_native_entry_scales_real_pdf_extraction_placement_and_checkpoint(
    monkeypatch, tmp_path, scale, page_count,
):
    import math

    sizes = [LANDSCAPE, PORTRAIT][:page_count]
    overrides = {} if scale is None else {"user_scale": scale}
    scale = 1.0 if scale is None else scale
    engine, placed, checkpoints = _import_stack(
        monkeypatch, tmp_path, sizes, "spread", real_pdf=True,
        config_extra=overrides,
    )
    heights = [(height * PT_TO_MM * scale) * MM_TO_M for _width, height in sizes]
    assert [obj.height_m for obj in placed] == heights
    assert [obj.width_m for obj in placed] == [
        (width * PT_TO_MM * scale) * MM_TO_M for width, _height in sizes]
    # Actual PyMuPDF/core extraction produces the line in model millimetres.
    for obj in placed:
        line = next(primitive for primitive in obj.primitives if primitive.type == "line")
        assert math.dist(line.points[0], line.points[-1]) == pytest.approx(40 * PT_TO_MM * scale)
        assert line.line_width == pytest.approx(PT_TO_MM * scale)
    assert [obj.location[1] for obj in placed] == _layout(engine, heights, "spread")
    assert checkpoints[-1]["stacked_page_height_m"] == heights[-1]
    assert checkpoints[-1]["next_stack_offset_m"] == (
        placed[-1].location[1] - engine._page_stack_step(heights[-1], "spread", 0.20))


def test_invalid_native_scale_stops_before_pdf_extraction(monkeypatch, tmp_path):
    engine = _engine(monkeypatch)

    def unexpected_extraction(*_args, **_kwargs):
        pytest.fail("Invalid scale must fail before PDF extraction")

    monkeypatch.setattr(engine, "iter_pages", unexpected_extraction)
    monkeypatch.setattr(engine, "extract_page", unexpected_extraction)
    with pytest.raises(ValueError, match="user_scale must be a finite positive number"):
        _import_stack(
            monkeypatch, tmp_path, [LANDSCAPE, PORTRAIT], "spread", real_pdf=True,
            config_extra={"user_scale": float("nan")},
        )


def test_cancelled_real_pdf_checkpoint_retains_nonunit_height_and_scale_hash(monkeypatch, tmp_path):
    from pdf_vector_importer.import_session import load_resume_checkpoint, resume_config_sha256

    scale = 1.25
    _engine_module, placed, checkpoints = _import_stack(
        monkeypatch, tmp_path, [LANDSCAPE, PORTRAIT], "spread", real_pdf=True,
        config_extra={"user_scale": scale, "resume_checkpoint_path": str(tmp_path / "cancelled.json")},
        cancel_after_page=1,
    )
    state = checkpoints[-1]
    height = (LANDSCAPE[1] * PT_TO_MM * scale) * MM_TO_M
    assert [obj.page_number for obj in placed] == [1]
    assert state["completed_pages"] == [1] and state["remaining_pages"] == [2]
    assert state["stacked_page_height_m"] == height
    assert state["next_stack_offset_m"] == -height * 1.2
    config = {"mode": "vector", "pages": "1-2", "import_text": False,
              "ignore_images": True, "auto_focus_view": False, "auto_hide_default_cube": False,
              "page_arrangement": "spread", "user_scale": scale}
    assert state["config_sha256"] == resume_config_sha256(config)
    assert state["config_sha256"] != resume_config_sha256(dict(config, user_scale=1.0))
    # Read the real persisted checkpoint, rather than trusting the recording hook.
    persisted = load_resume_checkpoint(
        tmp_path / "cancelled.json", source_sha256=state["source_sha256"],
        config_sha256=state["config_sha256"],
    )
    assert persisted["stacked_page_height_m"] == height
    assert persisted["next_stack_offset_m"] == state["next_stack_offset_m"]


@pytest.mark.parametrize("legacy", [False, True])
def test_real_pdf_resume_preserves_nonunit_scale_for_modern_and_legacy_checkpoint(
    monkeypatch, tmp_path, legacy,
):
    scale = 1.25
    height = (LANDSCAPE[1] * PT_TO_MM * scale) * MM_TO_M
    saved = {"next_stack_offset_m": -height * 1.2}
    if not legacy:
        saved["stacked_page_height_m"] = height
    engine, placed, checkpoints = _import_stack(
        monkeypatch, tmp_path, [LANDSCAPE, PORTRAIT], "spread", real_pdf=True,
        config_extra={"user_scale": scale}, resume=(1, saved),
    )
    second_height = (PORTRAIT[1] * PT_TO_MM * scale) * MM_TO_M
    assert [obj.page_number for obj in placed] == [2]
    assert placed[0].height_m == second_height
    assert placed[0].location[1] == engine._page_stack_offset(-height * 1.2, height, second_height, "spread")
    assert -placed[0].world_rect()[3] == pytest.approx(0.2 * height, abs=EDGE_TOLERANCE_M)
    final = checkpoints[-1]
    assert final["completed_pages"] == [1, 2] and final["remaining_pages"] == []
    assert final["stacked_page_height_m"] == second_height
    if legacy:
        assert final["aggregate_stats"]["resume_stack_migration"]["user_scale"] == scale
    else:
        assert "resume_stack_migration" not in final["aggregate_stats"]


def _world_y_rects(placed):
    return [(obj.world_rect()[1], obj.world_rect()[3]) for obj in placed]


@pytest.mark.parametrize("arrangement", ["spread", "touch", "compact"])
@pytest.mark.parametrize("sizes", [
    [LANDSCAPE, PORTRAIT],
    [PORTRAIT, LANDSCAPE],
    [LANDSCAPE, PORTRAIT, WIDE],
])
def test_import_places_mixed_pages_without_overlap(monkeypatch, tmp_path, arrangement, sizes) -> None:
    engine, placed, _checkpoints = _import_stack(monkeypatch, tmp_path, sizes, arrangement)

    assert [obj.page_number for obj in placed] == list(range(1, len(sizes) + 1))
    rects = _world_y_rects(placed)
    _assert_no_overlap(rects)
    heights = [obj.height_m for obj in placed]
    for index in range(1, len(rects)):
        assert rects[index - 1][0] - rects[index][1] == pytest.approx(
            _expected_gap_m(heights[index - 1], arrangement, 0.20), abs=EDGE_TOLERANCE_M)
    # Left alignment at x = 0 stays, and nothing moves in Z.
    assert [obj.location[0] for obj in placed] == [0.0] * len(placed)
    assert [obj.location[2] for obj in placed] == [0.0] * len(placed)
    # The loop uses the same offsets as the rule exercised above.
    assert [obj.location[1] for obj in placed] == _layout(engine, heights, arrangement)


def test_import_overlay_leaves_every_page_at_the_origin(monkeypatch, tmp_path) -> None:
    _engine_module, placed, _checkpoints = _import_stack(
        monkeypatch, tmp_path, [LANDSCAPE, PORTRAIT, WIDE], "overlay")
    assert [obj.location for obj in placed] == [[0.0, 0.0, 0.0]] * 3


@pytest.mark.parametrize("arrangement", ["spread", "touch", "compact", "overlay"])
def test_import_equal_size_pages_keep_the_previous_positions(monkeypatch, tmp_path, arrangement) -> None:
    sizes = [PORTRAIT] * 5
    engine, placed, _checkpoints = _import_stack(monkeypatch, tmp_path, sizes, arrangement)
    heights = [obj.height_m for obj in placed]
    previous = _previous_rule(engine, heights, arrangement)
    assert [_bits(obj.location[1]) for obj in placed] == [_bits(value) for value in previous]


def test_import_single_page_is_not_moved(monkeypatch, tmp_path) -> None:
    _engine_module, placed, _checkpoints = _import_stack(monkeypatch, tmp_path, [PORTRAIT], "spread")
    assert [obj.location for obj in placed] == [[0.0, 0.0, 0.0]]


def test_page_checkpoints_carry_the_height_a_resume_needs(monkeypatch, tmp_path) -> None:
    engine, placed, checkpoints = _import_stack(
        monkeypatch, tmp_path, [LANDSCAPE, PORTRAIT, WIDE], "spread")
    heights = [obj.height_m for obj in placed]
    after_page = {tuple(state["completed_pages"]): state for state in checkpoints}
    for count in (1, 2):
        state = after_page[tuple(range(1, count + 1))]
        assert state["stacked_page_height_m"] == heights[count - 1]
        assert state["next_stack_offset_m"] == placed[count - 1].location[1] - engine._page_stack_step(
            heights[count - 1], "spread", 0.20)


def test_resume_places_a_taller_page_below_the_completed_one(monkeypatch, tmp_path) -> None:
    """Landscape page 1 was completed before the interruption; portrait page 2 resumes."""
    sizes = [LANDSCAPE, PORTRAIT]
    heights = _heights_m(sizes)
    cursor = 0.0 - heights[0] * 1.2
    _engine_module, placed, _checkpoints = _import_stack(
        monkeypatch, tmp_path, sizes, "spread",
        resume=(1, {"next_stack_offset_m": cursor, "stacked_page_height_m": heights[0]}),
    )
    assert [obj.page_number for obj in placed] == [2]
    page_one = (0.0, heights[0])
    page_two = _world_y_rects(placed)[0]
    assert _overlap_m(page_one, page_two) <= EDGE_TOLERANCE_M
    assert page_one[0] - page_two[1] == pytest.approx(0.20 * heights[0], abs=EDGE_TOLERANCE_M)


def test_resume_from_a_checkpoint_without_the_height_uses_its_stored_offset(monkeypatch, tmp_path) -> None:
    """Recovering an equal height keeps the stored offset bit for bit."""
    sizes = [PORTRAIT, PORTRAIT]
    heights = _heights_m(sizes)
    cursor = 0.0 - heights[0] * 1.2
    _engine_module, placed, _checkpoints = _import_stack(
        monkeypatch, tmp_path, sizes, "spread",
        resume=(1, {"next_stack_offset_m": cursor}),
    )
    assert [_bits(obj.location[1]) for obj in placed] == [_bits(cursor)]

@pytest.mark.parametrize("arrangement", ["spread", "touch", "compact"])
def test_legacy_resume_recovers_last_height_and_prevents_mixed_size_overlap(monkeypatch, tmp_path, arrangement):
    sizes = [LANDSCAPE, PORTRAIT]
    heights = _heights_m(sizes)
    engine = _engine(monkeypatch)
    cursor = -engine._page_stack_step(heights[0], arrangement, .20)
    # The old missing-height route incorrectly placed the portrait here.
    assert _overlap_m((0, heights[0]), (cursor, cursor + heights[1])) > 0
    _, placed, checkpoints = _import_stack(
        monkeypatch, tmp_path, sizes, arrangement,
        resume=(1, {"next_stack_offset_m": cursor}),
    )
    assert [obj.page_number for obj in placed] == [2]
    page_two = _world_y_rects(placed)[0]
    assert _overlap_m((0, heights[0]), page_two) <= EDGE_TOLERANCE_M
    assert -page_two[1] == pytest.approx(_expected_gap_m(heights[0], arrangement, .20), abs=EDGE_TOLERANCE_M)
    evidence = checkpoints[-1]["aggregate_stats"]["resume_stack_migration"]
    assert evidence["completed_page"] == 1
    assert evidence["stacked_page_height_m"] == heights[0]
    assert evidence["existing_objects_moved"] is False


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("scale", [.5, 2.0])
def test_legacy_migration_uses_actual_cropped_rotated_pdf_extent(monkeypatch, rotation, scale):
    import pymupdf as fitz
    engine = _engine(monkeypatch)
    document = fitz.open()
    page = document.new_page(width=800, height=1000)
    page.set_cropbox(fitz.Rect(20, 30, 700, 900))
    page.set_rotation(rotation)
    source = document.tobytes()
    document.close()
    source_hash = hashlib.sha256(source).hexdigest()
    state = {"source_sha256": source_hash, "config_sha256": "c" * 64,
             "requested_pages": [1], "completed_pages": [1], "remaining_pages": []}
    with fitz.open(stream=source, filetype="pdf") as doc:
        before = doc.tobytes(no_new_id=True)
        height, evidence = engine._legacy_resume_stack_height(
            doc, state, source_sha256=source_hash, config_sha256="c" * 64, scale=scale)
        expected = (doc[0].rect.height * PT_TO_MM * scale) * MM_TO_M
        assert height == expected
        assert evidence["source_boxes_points"] == {
            name: list(getattr(doc[0], name)) for name in ("rect", "cropbox", "mediabox")}
        assert evidence["source_rotation_degrees"] == rotation
        assert evidence["user_scale"] == scale
        assert evidence["source_sha256"] == source_hash
        assert doc.tobytes(no_new_id=True) == before


@pytest.mark.parametrize("change", ["height", "crop", "rotation", "scale"])
def test_changed_source_or_scale_cannot_supply_a_legacy_height(monkeypatch, change):
    import pymupdf as fitz
    engine = _engine(monkeypatch)
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        source_hash = hashlib.sha256(doc.tobytes()).hexdigest()
        state = {"source_sha256": source_hash, "config_sha256": "c" * 64,
                 "requested_pages": [1], "completed_pages": [1], "remaining_pages": []}
        config_hash = "c" * 64
        if change == "height": page.set_mediabox(fitz.Rect(0, 0, 600, 900))
        if change == "crop": page.set_cropbox(fitz.Rect(10, 10, 500, 700))
        if change == "rotation": page.set_rotation(90)
        if change == "scale": config_hash = "d" * 64
        else: source_hash = hashlib.sha256(doc.tobytes()).hexdigest()
        with pytest.raises(ValueError, match="authenticated PDF"):
            engine._legacy_resume_stack_height(doc, state, source_sha256=source_hash,
                                               config_sha256=config_hash, scale=2 if change == "scale" else 1)


@pytest.mark.parametrize("completed,remaining", [([2], [1]), ([1, 1], [2]), ([True], [2]), ([1], [1, 2])])
def test_legacy_migration_rejects_ambiguous_completed_page_order(monkeypatch, completed, remaining):
    engine = _engine(monkeypatch)
    doc = types.SimpleNamespace(page_count=2, load_page=lambda _i: pytest.fail("must reject before source page read"))
    state = {"source_sha256": "a" * 64, "config_sha256": "c" * 64,
             "requested_pages": [1, 2], "completed_pages": completed, "remaining_pages": remaining}
    with pytest.raises(ValueError, match="completed page prefix"):
        engine._legacy_resume_stack_height(doc, state, source_sha256="a" * 64,
                                           config_sha256="c" * 64, scale=1)


def test_new_checkpoint_does_not_need_legacy_migration(monkeypatch, tmp_path):
    engine = _engine(monkeypatch)
    monkeypatch.setattr(engine, "_legacy_resume_stack_height", lambda *_a, **_k: pytest.fail("new checkpoint already has height"))
    height = _heights_m([LANDSCAPE])[0]
    _, _, checkpoints = _import_stack(monkeypatch, tmp_path, [LANDSCAPE, PORTRAIT], "spread",
        resume=(1, {"next_stack_offset_m": -height * 1.2, "stacked_page_height_m": height}))
    assert "resume_stack_migration" not in checkpoints[-1]["aggregate_stats"]
