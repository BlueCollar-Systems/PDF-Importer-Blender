"""Import dialog switches do what their labels say.

Z offset 0 means flat, Compact gap is tighter than Spread at its default and
only shown for Compact, the height offsets live under Advanced, and Labels
(which Blender cannot make) is last and says what it gives. No Blender needed.
"""
from __future__ import annotations

import importlib
import math
import sys
import types

import pytest


@pytest.fixture
def modules(monkeypatch: pytest.MonkeyPatch):
    fake_bpy = types.ModuleType("bpy")
    fake_bpy.app = types.SimpleNamespace(version=(4, 1, 0))
    fake_bpy.types = types.SimpleNamespace(
        Collection=object, Material=object, Object=object, VectorFont=object,
        Operator=type("Operator", (), {}),
    )
    props = types.ModuleType("bpy.props")
    for name in ("BoolProperty", "EnumProperty", "FloatProperty", "StringProperty"):
        setattr(props, name, lambda **kwargs: kwargs)
    io_utils = types.ModuleType("bpy_extras.io_utils")
    io_utils.ImportHelper = type("ImportHelper", (), {})
    bpy_extras = types.ModuleType("bpy_extras")
    bpy_extras.io_utils = io_utils
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    monkeypatch.setitem(sys.modules, "bmesh", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "bpy.props", props)
    monkeypatch.setitem(sys.modules, "bpy_extras", bpy_extras)
    monkeypatch.setitem(sys.modules, "bpy_extras.io_utils", io_utils)
    engine = importlib.import_module("pdf_vector_importer.bl_import_engine")
    previous = sys.modules.pop("pdf_vector_importer.operators", None)
    try:
        yield importlib.import_module("pdf_vector_importer.operators"), engine
    finally:
        sys.modules.pop("pdf_vector_importer.operators", None)
        if previous is not None:
            sys.modules["pdf_vector_importer.operators"] = previous


def _property(operators, name):
    """The keyword arguments a dialog property was declared with."""
    annotation = operators.IMPORT_OT_pdf_vector.__annotations__[name]
    return eval(annotation, vars(operators)) if isinstance(annotation, str) else annotation


@pytest.mark.parametrize("value,expected", [
    (0.0, 0.0), (0, 0.0), (-0.2, -0.2), (2.5, 2.5), ("0", 0.0),
    (None, 0.35), ("x", 0.35), (float("nan"), 0.35), (float("inf"), 0.35),
])
def test_zero_offset_means_flat_and_only_bad_values_use_the_default(modules, value, expected):
    _operators, engine = modules
    assert engine._offset_mm({"text_z_offset_mm": value}, "text_z_offset_mm", 0.35) == expected


def test_missing_offset_uses_the_default(modules):
    _operators, engine = modules
    assert engine._offset_mm({}, "line_z_offset_mm", 0.10) == 0.10


def test_compact_default_stacks_tighter_than_spread(modules):
    operators, engine = modules
    default = _property(operators, "page_gap_ratio")["default"]
    assert default == 0.05
    assert engine._normalize_page_gap_ratio(None) == 0.05
    assert engine._normalize_page_gap_ratio(float("nan")) == 0.05
    sheet = 0.2794
    compact = engine._page_stack_step(sheet, "compact", engine._normalize_page_gap_ratio(default))
    spread = engine._page_stack_step(sheet, "spread", engine._normalize_page_gap_ratio(default))
    assert compact < spread and math.isclose(compact, sheet * 1.05)


def test_labels_is_last_and_says_what_blender_makes(modules):
    operators, _engine = modules
    values = [item[0] for item in operators._TEXT_MODE_ITEMS]
    assert values[-1] == "labels" and values[0] == "text"
    assert "flat Text" in operators._TEXT_MODE_ITEMS[-1][1]
    assert _property(operators, "text_mode")["default"] == "3d_text"


class _Layout:
    """Records every drawn property with the box it sits in."""

    def __init__(self, drawn, where="top"):
        self.drawn, self.where, self.enabled = drawn, where, True

    def prop(self, _owner, name, **_kwargs):
        self.drawn.append((self.where, name))

    def box(self):
        self.drawn.append(("box", None))
        return _Layout(self.drawn, f"box{sum(1 for kind, _ in self.drawn if kind == 'box')}")

    def row(self, **_kwargs):
        return _Layout(self.drawn, self.where)

    column = row

    def label(self, **_kwargs):
        pass

    def separator(self, **_kwargs):
        pass


def _draw(operators, *, arrangement, advanced):
    operator = operators.IMPORT_OT_pdf_vector()
    operator.show_advanced = advanced
    operator.page_arrangement = arrangement
    operator.import_text = True
    operator.visual_style = "source"
    operator.model3d_mode = "off"
    drawn = []
    operator.layout = _Layout(drawn)
    operator.draw(None)
    return drawn


@pytest.mark.parametrize("arrangement", ["spread", "compact", "touch", "overlay"])
def test_gap_field_is_drawn_only_for_compact(modules, arrangement):
    operators, _engine = modules
    names = [name for _where, name in _draw(operators, arrangement=arrangement, advanced=False)]
    assert ("page_gap_ratio" in names) is (arrangement == "compact")


@pytest.mark.parametrize("advanced", [False, True])
def test_height_offsets_live_under_advanced(modules, advanced):
    operators, _engine = modules
    drawn = _draw(operators, arrangement="spread", advanced=advanced)
    offsets = [(where, name) for where, name in drawn if name and name.endswith("_z_offset_mm")]
    mode_box = next(where for where, name in drawn if name == "show_advanced")
    if advanced:
        assert offsets == [(mode_box, "line_z_offset_mm"), (mode_box, "text_z_offset_mm"),
                           (mode_box, "image_z_offset_mm")]
    else:
        assert offsets == []


def test_no_empty_box_when_3d_model_options_are_shelved(modules):
    operators, _engine = modules
    assert operators.SHAPE_EXTRUSION_UI_ENABLED is False
    drawn = _draw(operators, arrangement="spread", advanced=False)
    boxes = sum(1 for kind, _name in drawn if kind == "box")
    assert boxes == 3
    for number in range(1, boxes + 1):
        contents = [name for where, name in drawn if where == f"box{number}"]
        assert contents, f"box {number} is drawn empty"


def test_hidden_default_cube_is_hidden_from_renders_too(modules, monkeypatch):
    _operators, engine = modules
    cube = types.SimpleNamespace(hide_viewport=False, hide_render=False, hidden=False)
    cube.hide_set = lambda value: setattr(cube, "hidden", value)
    monkeypatch.setattr(engine, "_is_default_startup_cube", lambda obj: obj is cube)
    assert engine._auto_hide_default_cube(types.SimpleNamespace(objects=[cube])) == 1
    assert cube.hidden and cube.hide_viewport and cube.hide_render
