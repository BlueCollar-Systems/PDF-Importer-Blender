"""Affine-carrier EMPTYs must be visually inert.

``_apply_target_quad_affine`` parents a sheared glyph to a helper EMPTY (the
"affine carrier") so Blender can store the shear.  An EMPTY draws its
PLAIN_AXES gizmo at ``empty_display_size`` = 1 m unless the importer sets it,
which put a 2 m vertical line through every positioned glyph of a 48 x 36 in
sheet.  The carrier must keep PLAIN_AXES and take a size derived from the glyph
it carries, in metres, clamped to a range that is invisible at sheet scale.
"""
from __future__ import annotations

import math
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

from pdf_vector_importer import bl_text_builder
from pdf_vector_importer.pdfcadcore.primitives import NormalizedText

MM_TO_M = bl_text_builder.MM_TO_M


class _Matrix:
    """Row-major stand-in for mathutils.Matrix (construction and identity only)."""

    def __init__(self, rows):
        self.rows = tuple(tuple(float(value) for value in row) for row in rows)

    @classmethod
    def Identity(cls, size):
        return cls(
            tuple(
                tuple(1.0 if row == col else 0.0 for col in range(size))
                for row in range(size)
            )
        )

    def __getitem__(self, index):
        return self.rows[index]


class _HostObject(dict):
    """A host object carrying Blender's EMPTY defaults before the importer touches it."""

    def __init__(self, name, data):
        super().__init__()
        self.name = name
        self.data = data
        self.type = "EMPTY" if data is None else "FONT"
        # Blender defaults for a freshly created EMPTY.
        self.empty_display_type = "PLAIN_AXES"
        self.empty_display_size = 1.0
        self.parent = None
        self.matrix_world = None
        self.matrix_basis = None
        self.matrix_parent_inverse = None
        self.users_collection = ()
        self.bound_box = ()


class _Objects:
    def __init__(self):
        self.created = []
        self.removed = []

    def new(self, name, data):
        obj = _HostObject(name, data)
        self.created.append(obj)
        return obj

    def remove(self, obj, do_unlink=True):
        del do_unlink
        self.removed.append(obj.name)


class _CollectionObjects:
    def __init__(self):
        self.items = []

    def link(self, obj):
        self.items.append(obj)


class _Collection:
    def __init__(self):
        self.objects = _CollectionObjects()


def _install(monkeypatch):
    fake_bpy = types.SimpleNamespace(data=types.SimpleNamespace(objects=_Objects()))
    monkeypatch.setattr(bl_text_builder, "bpy", fake_bpy)
    fake_mathutils = types.ModuleType("mathutils")
    fake_mathutils.Matrix = _Matrix
    monkeypatch.setitem(sys.modules, "mathutils", fake_mathutils)
    return fake_bpy


def _sheared_item(*, origin_mm=(100.0, 50.0), width_mm=20.0, height_mm=5.0, shear_mm=1.0):
    """A span whose target quad is a sheared parallelogram (needs a carrier)."""
    x0, y0 = origin_mm
    ll = (x0, y0)
    lr = (x0 + width_mm, y0)
    ul = (x0 + shear_mm, y0 + height_mm)
    ur = (x0 + width_mm + shear_mm, y0 + height_mm)
    return NormalizedText(
        id=7,
        text="AB",
        normalized="AB",
        insertion=(x0, y0),
        bbox=(x0, y0, x0 + width_mm + shear_mm, y0 + height_mm),
        target_quad_model=(ul, ur, lr, ll),
        advance_width=width_mm,
        glyph_height=height_mm,
        font_size=height_mm,
        page_number=1,
    )


def _font_object(collection):
    obj = _HostObject("P1_text_3d_text_7", types.SimpleNamespace(type="FONT"))
    obj.users_collection = (collection,)
    # Evaluated local bounds of the rendered text: 20 mm x 5 mm in metres.
    obj.bound_box = (
        (0.0, 0.0, 0.0),
        (0.0, 0.005, 0.0),
        (0.020, 0.005, 0.0),
        (0.020, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.005, 0.0),
        (0.020, 0.005, 0.0),
        (0.020, 0.0, 0.0),
    )
    return obj


def test_affine_carrier_empty_is_sized_from_the_glyph_it_carries(monkeypatch):
    fake_bpy = _install(monkeypatch)
    collection = _Collection()
    obj = _font_object(collection)
    item = _sheared_item(height_mm=5.0)

    carrier = bl_text_builder._apply_target_quad_affine(
        obj, item, 0.0, collection=collection
    )

    assert carrier is not None
    assert carrier.data is None
    assert carrier.type == "EMPTY"
    # Blender's default gizmo is 1 m; the carrier must have been re-sized.
    assert carrier.empty_display_type == "PLAIN_AXES"
    # 5 % of the quad's vertical edge: hypot(1 mm shear, 5 mm height) = 5.099 mm.
    assert carrier.empty_display_size == pytest.approx(
        math.hypot(1.0, 5.0) * 0.05 * MM_TO_M
    )
    assert carrier.empty_display_size <= bl_text_builder._CARRIER_DISPLAY_MAX_M
    # What the carrier does is unchanged: linked, parented, recorded.
    assert carrier in collection.objects.items
    assert obj.parent is carrier
    assert obj["pdf_affine_carrier"] == carrier.name
    assert obj["pdf_affine_carrier_owned"] is True
    assert obj["pdf_full_affine_applied"] is True
    assert isinstance(carrier.matrix_world, _Matrix)
    assert isinstance(obj.matrix_basis, _Matrix)
    # Every helper EMPTY the builder created during the call is inert.
    empties = [created for created in fake_bpy.data.objects.created if created.data is None]
    assert empties == [carrier]
    for empty in empties:
        assert empty.empty_display_type == "PLAIN_AXES"
        assert (
            bl_text_builder._CARRIER_DISPLAY_MIN_M
            <= empty.empty_display_size
            <= bl_text_builder._CARRIER_DISPLAY_MAX_M
        )


def test_carrier_world_extent_tracks_glyph_height_not_the_carrier_scale(monkeypatch):
    """The carrier's matrix maps local metres to world metres (unit scale), so
    the display size is a world extent; a 12 mm glyph gets the 0.5 mm ceiling."""
    _install(monkeypatch)
    collection = _Collection()
    obj = _font_object(collection)
    item = _sheared_item(height_mm=12.0, width_mm=20.0, shear_mm=2.0)

    carrier = bl_text_builder._apply_target_quad_affine(
        obj, item, 0.0, collection=collection
    )

    assert carrier is not None
    # 12 mm * 5 % = 0.6 mm exceeds the 0.5 mm ceiling.
    assert carrier.empty_display_size == pytest.approx(0.5 * MM_TO_M)
    # The gizmo's Z line (the one that stood up through the sheet) is drawn at
    # display size times the Z column, which the 2-D affine leaves at unit
    # length.
    z_column = (
        carrier.matrix_world[0][2],
        carrier.matrix_world[1][2],
        carrier.matrix_world[2][2],
    )
    assert math.hypot(*z_column) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("quad_mm", "expected_m"),
    [
        # 0.2 mm glyph: 5 % = 0.01 mm, clamped up to the 0.02 mm floor.
        ((((0.0, 0.2), (1.0, 0.2), (1.0, 0.0), (0.0, 0.0))), 0.02 * MM_TO_M),
        # 4.4 mm glyph (a typical 12.6 pt annotation): 0.22 mm.
        ((((0.0, 4.4), (3.0, 4.4), (3.0, 0.0), (0.0, 0.0))), 0.22 * MM_TO_M),
        # 50 mm title glyph: 2.5 mm, clamped down to the 0.5 mm ceiling.
        ((((0.0, 50.0), (30.0, 50.0), (30.0, 0.0), (0.0, 0.0))), 0.5 * MM_TO_M),
        # Rotated 90 degrees: the vertical edge is measured, not the y extent.
        ((((-4.0, 0.0), (-4.0, 3.0), (0.0, 3.0), (0.0, 0.0))), 0.2 * MM_TO_M),
    ],
)
def test_carrier_display_size_is_five_percent_of_glyph_height_clamped(quad_mm, expected_m):
    assert bl_text_builder._carrier_display_size_m(quad_mm) == pytest.approx(expected_m)


@pytest.mark.parametrize(
    "bad_quad",
    [None, (), ((0.0, 0.0),), (((0.0, 0.0), (1.0, 0.0), (1.0, 0.0), (0.0, 0.0))), "quad"],
)
def test_carrier_display_size_falls_back_to_the_floor_for_unusable_quads(bad_quad):
    assert bl_text_builder._carrier_display_size_m(bad_quad) == pytest.approx(
        bl_text_builder._CARRIER_DISPLAY_MIN_M
    )


def test_carrier_display_bounds_are_invisible_at_sheet_scale():
    # A 48 x 36 in sheet is 1.2192 m wide; the largest gizmo is 0.5 mm.
    assert bl_text_builder._CARRIER_DISPLAY_MIN_M == pytest.approx(2e-5)
    assert bl_text_builder._CARRIER_DISPLAY_MAX_M == pytest.approx(5e-4)
    assert bl_text_builder._CARRIER_DISPLAY_MAX_M * 2.0 < 1.2192 * 1e-3
