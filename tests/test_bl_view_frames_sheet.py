"""Regression: the opening view frames the sheet(s), straight on.

The view used to frame the delivered ink. A part drawn in one corner of a
Letter sheet then opened centred on the drawing instead of on the print, and
only a page-sized object (the white backing of the "source" style, a page
raster) or ink overflowing 1.5x the page made it frame the paper.

Every placed page now stamps its world-space sheet rectangle on its page
collection, and the framing box is the union of those rectangles for every
visual style. Ink outside the sheet does not enlarge the box. When a page
rectangle is unknown the view falls back to the bounds of the delivered
objects, as before.

Fixtures are synthetic page sizes and boxes only; no PDF bytes are involved.
"""
from __future__ import annotations

import importlib
import math
import sys
import types
from contextlib import nullcontext

import pytest

from test_bl_page_stack_no_overlap import (
    LANDSCAPE,
    MM_TO_M,
    PORTRAIT,
    PT_TO_MM,
    WIDE,
    _import_stack,
    _install_blender_stubs,
    _layout,
)


_MISSING = object()
_ISOLATED_MODULES = (
    "bpy",
    "bmesh",
    "mathutils",
    "pdf_vector_importer.bl_geometry_builder",
    "pdf_vector_importer.bl_import_engine",
)
VISUAL_STYLES = ("high_contrast", "blueprint", "source")


@pytest.fixture(autouse=True)
def _restore_replaced_modules():
    previous = {name: sys.modules.get(name, _MISSING) for name in _ISOLATED_MODULES}
    yield
    for name, module in previous.items():
        if module is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class _Vector:
    def __init__(self, values) -> None:
        seq = list(values) + [0.0, 0.0, 0.0]
        self.x = float(seq[0])
        self.y = float(seq[1])
        self.z = float(seq[2])

    def __add__(self, other):
        return _Vector((self.x + other.x, self.y + other.y, self.z + other.z))

    def __mul__(self, value):
        return _Vector((self.x * value, self.y * value, self.z * value))


class _Identity:
    def __matmul__(self, vec):
        return vec


class _Box(dict):
    """A delivered object: custom properties plus a world-space bound box."""

    type = "MESH"
    matrix_world = _Identity()

    def __init__(self, name, x0, y0, x1, y1, z0=0.0, z1=0.0, **props) -> None:
        super().__init__(props)
        self.name = name
        self.bound_box = [
            (x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)
        ]
        self.selected = None

    def select_set(self, value) -> None:
        self.selected = value

    def hide_set(self, _value) -> None:
        pass


class _PageCollection(dict):
    """A page collection: custom properties, objects, no nested pages."""

    __eq__ = object.__eq__
    __hash__ = object.__hash__

    def __init__(self, name, objects=()) -> None:
        super().__init__()
        self.name = name
        self.all_objects = list(objects)
        self.children = []


def _root(*pages):
    return types.SimpleNamespace(
        children=list(pages),
        all_objects=[obj for page in pages for obj in page.all_objects],
    )


def _engine(monkeypatch: pytest.MonkeyPatch):
    _install_blender_stubs(monkeypatch)
    fake_mathutils = types.ModuleType("mathutils")
    fake_mathutils.Vector = _Vector
    monkeypatch.setitem(sys.modules, "mathutils", fake_mathutils)
    return importlib.import_module("pdf_vector_importer.bl_import_engine")


def _size_mm(size_pt):
    return (size_pt[0] * PT_TO_MM, size_pt[1] * PT_TO_MM)


def _xy(lower, upper):
    return (lower.x, lower.y, upper.x, upper.y)


def _placed_page(engine, number, size_pt, offset_m=0.0, ink=None):
    """A page collection as the import loop leaves it: ink plus the stamped sheet."""
    width_mm, height_mm = _size_mm(size_pt)
    objects = []
    if ink is not None:
        x0, y0, x1, y1 = ink
        objects.append(_Box(f"P{number}_ink", x0, y0 + offset_m, x1, y1 + offset_m))
    page = _PageCollection(f"PDF_Page_{number}", objects)
    rect = engine._record_page_rect(page, width_mm, height_mm, offset_m)
    assert rect == (0.0, offset_m, width_mm * MM_TO_M, offset_m + height_mm * MM_TO_M)
    return page


# A small part drawn near the lower-left corner of the sheet (metres).
CORNER_INK = (0.010, 0.012, 0.060, 0.055)


# --------------------------------------------------------------------------
# The sheet rectangle and its stamp
# --------------------------------------------------------------------------

def test_page_rect_is_the_sheet_where_the_stack_put_it(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    width_mm, height_mm = _size_mm(PORTRAIT)

    assert engine._page_rect_m(width_mm, height_mm) == (
        0.0, 0.0, width_mm * MM_TO_M, height_mm * MM_TO_M)
    # Stacked pages go down -Y and stay left aligned at x = 0.
    assert engine._page_rect_m(width_mm, height_mm, -0.33528) == (
        0.0, -0.33528, width_mm * MM_TO_M, -0.33528 + height_mm * MM_TO_M)


@pytest.mark.parametrize("width_mm,height_mm,offset_m", [
    (0.0, 279.4, 0.0),
    (215.9, 0.0, 0.0),
    (-215.9, 279.4, 0.0),
    (float("nan"), 279.4, 0.0),
    (215.9, float("inf"), 0.0),
    (215.9, 279.4, float("nan")),
    (None, 279.4, 0.0),
    ("wide", 279.4, 0.0),
])
def test_unusable_page_size_gives_no_rectangle(monkeypatch, width_mm, height_mm, offset_m) -> None:
    engine = _engine(monkeypatch)
    page = _PageCollection("PDF_Page_1")

    assert engine._page_rect_m(width_mm, height_mm, offset_m) is None
    assert engine._record_page_rect(page, width_mm, height_mm, offset_m) is None
    assert engine._PAGE_RECT_PROP not in page


def test_stamp_round_trips_through_the_page_collection(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    page = _PageCollection("PDF_Page_2")
    width_mm, height_mm = _size_mm(LANDSCAPE)

    rect = engine._record_page_rect(page, width_mm, height_mm, -0.25908)

    assert page[engine._PAGE_RECT_PROP] == list(rect)
    assert engine._stored_page_rect(page) == rect


def test_collection_without_custom_properties_leaves_the_rectangle_unknown(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    bare = types.SimpleNamespace(name="PDF_Page_1", all_objects=[_Box("ink", *CORNER_INK)])

    assert engine._record_page_rect(bare, 215.9, 279.4) is None
    assert engine._stored_page_rect(bare) is None
    assert engine._imported_page_rects(types.SimpleNamespace(children=[bare])) == []


@pytest.mark.parametrize("stored", [
    [0.0, 0.0, 0.2159],
    [0.0, 0.0, 0.2159, 0.2794, 1.0],
    [0.0, 0.0, float("nan"), 0.2794],
    [0.0, 0.0, 0.0, 0.2794],
    [0.0, 0.2794, 0.2159, 0.0],
    ["a", "b", "c", "d"],
    "sheet",
    7,
])
def test_damaged_stamp_is_read_as_unknown(monkeypatch, stored) -> None:
    engine = _engine(monkeypatch)
    page = _PageCollection("PDF_Page_1", [_Box("ink", *CORNER_INK)])
    page[engine._PAGE_RECT_PROP] = stored

    assert engine._stored_page_rect(page) is None
    assert engine._imported_page_rects(_root(page)) == []


# --------------------------------------------------------------------------
# The framing box
# --------------------------------------------------------------------------

def test_single_portrait_sheet_with_ink_in_a_corner_frames_the_sheet(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    page = _placed_page(engine, 1, PORTRAIT, ink=CORNER_INK)
    root = _root(page)
    width_m, height_m = (value * MM_TO_M for value in _size_mm(PORTRAIT))

    ink_min, ink_max = engine._world_bounds_for_objects(root.all_objects)
    lower, upper, rule = engine._import_view_bounds(root, root.all_objects)

    # The ink alone is what the view used to frame.
    assert _xy(ink_min, ink_max) == pytest.approx(CORNER_INK)
    assert rule == "page_rectangles"
    assert _xy(lower, upper) == (0.0, 0.0, width_m, height_m)
    centre = (lower + upper) * 0.5
    assert (centre.x, centre.y) == pytest.approx((width_m / 2.0, height_m / 2.0))


def test_multi_page_frames_the_union_of_sheets(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    sizes = [LANDSCAPE, PORTRAIT, WIDE]
    heights_m = [_size_mm(size)[1] * MM_TO_M for size in sizes]
    widths_m = [_size_mm(size)[0] * MM_TO_M for size in sizes]
    offsets = _layout(engine, heights_m, "spread")
    pages = [
        _placed_page(engine, index + 1, size, offsets[index], ink=CORNER_INK)
        for index, size in enumerate(sizes)
    ]
    root = _root(*pages)

    lower, upper, rule = engine._import_view_bounds(root, root.all_objects)

    assert rule == "page_rectangles"
    assert engine._imported_page_rects(root) == [
        (0.0, offsets[i], widths_m[i], offsets[i] + heights_m[i]) for i in range(3)
    ]
    # Top of the first sheet down to the bottom of the last, widest sheet wide.
    assert _xy(lower, upper) == (0.0, offsets[2], max(widths_m), heights_m[0])
    assert offsets[2] < offsets[1] < offsets[0] == 0.0


def test_overlay_frames_the_largest_extent_of_the_overlaid_sheets(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    pages = [
        _placed_page(engine, 1, LANDSCAPE, ink=CORNER_INK),
        _placed_page(engine, 2, PORTRAIT, ink=CORNER_INK),
    ]
    root = _root(*pages)

    lower, upper, _rule = engine._import_view_bounds(root, root.all_objects)

    assert _xy(lower, upper) == pytest.approx((
        0.0, 0.0, _size_mm(LANDSCAPE)[0] * MM_TO_M, _size_mm(PORTRAIT)[1] * MM_TO_M))


@pytest.mark.parametrize("stray", [
    (-0.2399, 0.10, 0.0, 0.10),      # a line running 240 mm off the left edge
    (0.20, 0.10, 0.53, 0.10),        # past the right edge
    (0.10, 0.25, 0.10, 0.40),        # past the top edge
    (0.10, -0.05, 0.10, 0.02),       # past the bottom edge
    (-25.0, -40.0, 30.0, 55.0),      # far outside on every side
])
def test_ink_far_outside_the_sheet_does_not_change_the_frame(monkeypatch, stray) -> None:
    engine = _engine(monkeypatch)
    clean = _root(_placed_page(engine, 1, PORTRAIT, ink=CORNER_INK))
    clean_lower, clean_upper, _ = engine._import_view_bounds(clean, clean.all_objects)

    page = _placed_page(engine, 1, PORTRAIT, ink=CORNER_INK)
    page.all_objects.append(_Box("P1_stray", *stray))
    root = _root(page)
    ink_min, ink_max = engine._world_bounds_for_objects(root.all_objects)
    lower, upper, rule = engine._import_view_bounds(root, root.all_objects)

    width_m, height_m = (value * MM_TO_M for value in _size_mm(PORTRAIT))
    # The stray ink does leave the sheet ...
    assert ink_min.x < 0.0 or ink_min.y < 0.0 or ink_max.x > width_m or ink_max.y > height_m
    # ... and the frame is the sheet all the same.
    assert rule == "page_rectangles"
    assert _xy(lower, upper) == _xy(clean_lower, clean_upper) == (0.0, 0.0, width_m, height_m)


def test_depth_of_the_delivered_objects_is_kept_for_the_view_centre(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    page = _placed_page(engine, 1, PORTRAIT)
    page.all_objects.append(_Box("P1_text", 0.02, 0.02, 0.05, 0.03, z0=-0.00126, z1=0.00196))
    root = _root(page)

    lower, upper, _rule = engine._import_view_bounds(root, root.all_objects)

    assert (lower.z, upper.z) == pytest.approx((-0.00126, 0.00196))


def test_sheet_is_framed_even_when_no_object_has_bounds(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    page = _placed_page(engine, 1, LANDSCAPE)
    root = _root(page)

    lower, upper, rule = engine._import_view_bounds(root, [])

    assert rule == "page_rectangles"
    assert _xy(lower, upper) == pytest.approx((
        0.0, 0.0, _size_mm(LANDSCAPE)[0] * MM_TO_M, _size_mm(LANDSCAPE)[1] * MM_TO_M))
    assert (lower.z, upper.z) == (0.0, 0.0)


# --------------------------------------------------------------------------
# Unknown page rectangle: today's bounds
# --------------------------------------------------------------------------

def test_unknown_page_rect_falls_back_to_the_object_bounds(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    page = _PageCollection("PDF_Page_1", [_Box("P1_ink", *CORNER_INK)])
    root = _root(page)

    lower, upper, rule = engine._import_view_bounds(root, root.all_objects)

    assert rule == "object_bounds"
    assert _xy(lower, upper) == pytest.approx(CORNER_INK)


def test_root_without_page_collections_falls_back_to_the_object_bounds(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    flat = types.SimpleNamespace(all_objects=[_Box("ink", *CORNER_INK)])

    lower, upper, rule = engine._import_view_bounds(flat, flat.all_objects)

    assert engine._imported_page_rects(flat) == []
    assert rule == "object_bounds"
    assert _xy(lower, upper) == pytest.approx(CORNER_INK)


def test_one_unknown_page_makes_the_whole_frame_fall_back(monkeypatch) -> None:
    """Framing only the known sheet would cut the other page out of the view."""
    engine = _engine(monkeypatch)
    known = _placed_page(engine, 1, PORTRAIT, ink=CORNER_INK)
    unknown = _PageCollection("PDF_Page_2", [_Box("P2_ink", 0.01, -0.30, 0.06, -0.25)])
    root = _root(known, unknown)

    lower, upper, rule = engine._import_view_bounds(root, root.all_objects)

    assert engine._imported_page_rects(root) == []
    assert rule == "object_bounds"
    assert _xy(lower, upper) == pytest.approx((0.010, -0.30, 0.060, 0.055))


def test_empty_unstamped_collection_does_not_hide_the_known_sheets(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    known = _placed_page(engine, 1, PORTRAIT, ink=CORNER_INK)
    root = _root(known, _PageCollection("PDF_Page_2"))

    lower, upper, rule = engine._import_view_bounds(root, root.all_objects)

    assert rule == "page_rectangles"
    assert _xy(lower, upper) == (
        0.0, 0.0, _size_mm(PORTRAIT)[0] * MM_TO_M, _size_mm(PORTRAIT)[1] * MM_TO_M)


def test_unstamped_import_with_a_white_backing_frames_the_backing(monkeypatch) -> None:
    """No recorded rectangle, but the "source" style backing marks the sheet."""
    engine = _engine(monkeypatch)
    backing = _Box(
        "PDF White Page Background (Display Aid)", 0.0, 0.0, 0.2159, 0.2794,
        z0=-0.00005, z1=-0.00005, pdf_display_aid="display_only_page_background")
    # 20 mm past the left edge: under the old 1.5x rule this widened the frame.
    stray = _Box("P1_stray", -0.020, 0.10, 0.05, 0.10)
    page = _PageCollection("PDF_Page_1", [backing, stray, _Box("P1_ink", *CORNER_INK)])
    root = _root(page)

    lower, upper, rule = engine._import_view_bounds(root, root.all_objects)

    assert rule == "page_background"
    assert _xy(lower, upper) == pytest.approx((0.0, 0.0, 0.2159, 0.2794))


# --------------------------------------------------------------------------
# _prefer_sheet_frame: a known sheet always wins
# --------------------------------------------------------------------------

SHEET = ((0.0, 0.0, -0.0001), (0.216, 0.279, -0.0001))


@pytest.mark.parametrize("full", [
    ((0.033, 0.056, 0.0), (0.207, 0.267, 0.002)),     # ink inside the sheet
    ((-0.020, 0.0, 0.0), (0.216, 0.279, 0.002)),      # just past the edge (< 1.5x)
    ((-0.268, 0.0, 0.0), (0.529, 0.279, 0.001)),      # far past the edge (> 1.5x)
])
def test_prefer_sheet_frame_returns_the_known_sheet(monkeypatch, full) -> None:
    engine = _engine(monkeypatch)
    sheet_min, sheet_max = _Vector(SHEET[0]), _Vector(SHEET[1])

    lower, upper = engine._prefer_sheet_frame(
        _Vector(full[0]), _Vector(full[1]), sheet_min, sheet_max)

    assert lower is sheet_min and upper is sheet_max


def test_prefer_sheet_frame_does_not_need_object_bounds(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    sheet_min, sheet_max = _Vector(SHEET[0]), _Vector(SHEET[1])

    assert engine._prefer_sheet_frame(None, None, sheet_min, sheet_max) == (sheet_min, sheet_max)


@pytest.mark.parametrize("sheet", [
    (None, None),
    (SHEET[0], None),
    (None, SHEET[1]),
    ((0.0, 0.0, 0.0), (0.0, 0.279, 0.0)),               # no width
    ((0.0, 0.0, 0.0), (0.216, 0.0, 0.0)),               # no height
    ((0.0, 0.0, 0.0), (float("nan"), 0.279, 0.0)),
    ((0.0, 0.0, 0.0), (float("inf"), 0.279, 0.0)),
])
def test_prefer_sheet_frame_keeps_the_object_bounds_without_a_usable_sheet(monkeypatch, sheet) -> None:
    engine = _engine(monkeypatch)
    full_min, full_max = _Vector((0.033, 0.056, 0.0)), _Vector((0.207, 0.267, 0.002))
    sheet_min = None if sheet[0] is None else _Vector(sheet[0])
    sheet_max = None if sheet[1] is None else _Vector(sheet[1])

    lower, upper = engine._prefer_sheet_frame(full_min, full_max, sheet_min, sheet_max)

    assert lower is full_min and upper is full_max


# --------------------------------------------------------------------------
# _focus_view_on_import: the viewport ends straight on, on the sheet
# --------------------------------------------------------------------------

class _Region3D:
    """Orthographic viewport: projection scales inversely with view_distance."""

    def __init__(self, projection_xy) -> None:
        self.projection_xy = projection_xy
        self.view_distance = 18.4
        self.view_rotation = (0.78, 0.48, 0.21, 0.34)
        self.view_perspective = "PERSP"
        self.view_location = _Vector((0.0, 0.0, 0.0))
        self.window_matrix = None

    def update(self) -> None:
        self.window_matrix = (
            (self.projection_xy[0] / self.view_distance, 0, 0, 0),
            (0, self.projection_xy[1] / self.view_distance, 0, 0),
            (0, 0, -1, 0), (0, 0, 0, 1),
        )


def _focus(engine, monkeypatch, root, projections):
    regions = [_Region3D(projection) for projection in projections]
    areas = [types.SimpleNamespace(
        type="VIEW_3D", regions=[types.SimpleNamespace(type="WINDOW")],
        spaces=types.SimpleNamespace(active=types.SimpleNamespace(
            region_3d=region, local_view=None, clip_start=0.01, clip_end=1000,
            shading=types.SimpleNamespace(type="SOLID", light="STUDIO", color_type="RANDOM"),
        )),
    ) for region in regions]

    class ObjectList(list):
        active = None

    fake_bpy = types.SimpleNamespace(
        context=types.SimpleNamespace(
            view_layer=types.SimpleNamespace(objects=ObjectList(root.all_objects)),
            window_manager=types.SimpleNamespace(windows=[types.SimpleNamespace(
                screen=types.SimpleNamespace(areas=areas),
            )]),
            scene=object(), temp_override=lambda **_kwargs: nullcontext(),
        ),
        types=sys.modules["bpy"].types,
    )
    monkeypatch.setattr(engine, "bpy", fake_bpy)
    monkeypatch.setattr(engine, "_unhide_collection_tree", lambda _root: None)
    # No ops API is provided: framing must finish without view_selected/view_all.
    assert engine._focus_view_on_import(root) is True
    return regions


def _assert_straight_on_fit(region, rect) -> None:
    x0, y0, x1, y1 = rect
    assert region.view_rotation == (1.0, 0.0, 0.0, 0.0)
    assert region.view_perspective == "ORTHO"
    assert region.view_location.x == pytest.approx((x0 + x1) / 2.0)
    assert region.view_location.y == pytest.approx((y0 + y1) / 2.0)
    region.update()
    projected = [
        (x1 - x0) * abs(region.window_matrix[0][0]) / 2.0,
        (y1 - y0) * abs(region.window_matrix[1][1]) / 2.0,
    ]
    # The whole sheet is inside the viewport and its tighter side has the 10 % margin.
    assert max(projected) == pytest.approx(1.0 / 1.10)
    assert all(value < 1.0 for value in projected)


@pytest.mark.parametrize("size", [PORTRAIT, LANDSCAPE, WIDE])
def test_focus_puts_every_viewport_straight_on_the_sheet(monkeypatch, size) -> None:
    engine = _engine(monkeypatch)
    root = _root(_placed_page(engine, 1, size, ink=CORNER_INK))
    width_m, height_m = (value * MM_TO_M for value in _size_mm(size))

    # A wide, a tall and a near-square viewport in the same window.
    regions = _focus(engine, monkeypatch, root, [(1.0, 1.65), (2.2, 1.0), (1.0, 1.05)])

    for region in regions:
        _assert_straight_on_fit(region, (0.0, 0.0, width_m, height_m))
    # Framing leaves nothing selected.
    assert [obj.selected for obj in root.all_objects] == [False] * len(root.all_objects)


def test_focus_frames_the_whole_stack_of_a_multi_page_import(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    sizes = [LANDSCAPE, PORTRAIT, WIDE]
    heights_m = [_size_mm(size)[1] * MM_TO_M for size in sizes]
    offsets = _layout(engine, heights_m, "spread")
    root = _root(*[
        _placed_page(engine, index + 1, size, offsets[index], ink=CORNER_INK)
        for index, size in enumerate(sizes)
    ])

    (region,) = _focus(engine, monkeypatch, root, [(1.0, 1.65)])

    _assert_straight_on_fit(
        region, (0.0, offsets[2], _size_mm(WIDE)[0] * MM_TO_M, heights_m[0]))


def test_focus_ignores_ink_outside_the_sheet(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    page = _placed_page(engine, 1, PORTRAIT, ink=CORNER_INK)
    page.all_objects.append(_Box("P1_stray", -0.2399, 0.10, 0.0, 0.10))
    root = _root(page)

    (region,) = _focus(engine, monkeypatch, root, [(1.0, 1.65)])

    _assert_straight_on_fit(
        region, (0.0, 0.0, _size_mm(PORTRAIT)[0] * MM_TO_M, _size_mm(PORTRAIT)[1] * MM_TO_M))


def test_focus_without_page_rects_frames_the_objects_as_before(monkeypatch) -> None:
    engine = _engine(monkeypatch)
    root = _root(_PageCollection("PDF_Page_1", [_Box("P1_ink", *CORNER_INK)]))

    (region,) = _focus(engine, monkeypatch, root, [(1.0, 1.65)])

    _assert_straight_on_fit(region, CORNER_INK)


# --------------------------------------------------------------------------
# The page loop of import_pdf stamps every placed page, in every visual style
# --------------------------------------------------------------------------

def _import_and_capture_focus(monkeypatch, tmp_path, sizes, arrangement, style):
    engine = _engine(monkeypatch)
    focus_roots = []

    def capture(root, keep_selected=False, prefer_material_preview=False):
        focus_roots.append(root)
        return True

    monkeypatch.setattr(engine, "_focus_view_on_import", capture)
    same_engine, placed, _checkpoints = _import_stack(
        monkeypatch, tmp_path, sizes, arrangement,
        config_extra={"visual_style": style, "auto_focus_view": True},
    )
    assert same_engine is engine
    assert len(focus_roots) == 1
    pages = list(focus_roots[0].children.items)
    return engine, placed, pages


@pytest.mark.parametrize("style", VISUAL_STYLES)
@pytest.mark.parametrize("arrangement", ["spread", "touch", "compact", "overlay"])
def test_import_stamps_each_page_rect_for_every_visual_style(
        monkeypatch, tmp_path, style, arrangement) -> None:
    sizes = [LANDSCAPE, PORTRAIT, WIDE]
    engine, placed, pages = _import_and_capture_focus(
        monkeypatch, tmp_path, sizes, arrangement, style)

    assert [page.name for page in pages] == ["PDF_Page_1", "PDF_Page_2", "PDF_Page_3"]
    # The fake page object spans its sheet, so its world rectangle is the sheet.
    expected = [obj.world_rect() for obj in placed]
    assert [tuple(page[engine._PAGE_RECT_PROP]) for page in pages] == expected

    root = types.SimpleNamespace(children=pages)
    lower, upper, rule = engine._import_view_bounds(root, [])
    assert rule == "page_rectangles"
    assert _xy(lower, upper) == (
        0.0,
        min(rect[1] for rect in expected),
        max(rect[2] for rect in expected),
        max(rect[3] for rect in expected),
    )
    if arrangement != "overlay":
        # Three sheets and the two gaps between them: more than the paper alone.
        assert upper.y - lower.y > sum(obj.height_m for obj in placed) - 1e-12


@pytest.mark.parametrize("style", VISUAL_STYLES)
def test_import_single_page_stamps_the_sheet_at_the_origin(monkeypatch, tmp_path, style) -> None:
    engine, placed, pages = _import_and_capture_focus(
        monkeypatch, tmp_path, [PORTRAIT], "spread", style)

    (page,) = pages
    width_m, height_m = (value * MM_TO_M for value in _size_mm(PORTRAIT))
    assert tuple(page[engine._PAGE_RECT_PROP]) == (0.0, 0.0, width_m, height_m)
    assert placed[0].location == [0.0, 0.0, 0.0]
    assert math.isclose(width_m, 0.2159) and math.isclose(height_m, 0.2794)
