"""Affine carrier empties must not obscure the drawing in the viewport.

Blender's default empty display is PLAIN_AXES at 1.0 m, and one carrier is created PER
GLYPH. On 1011 that is 4182 carriers over a 0.887 x 0.591 m sheet: each draws a 2 m
axis-cross, and the viewport becomes a solid black starburst with the drawing buried
inside it (owner report, Blender 5.2, v1.0.94).

Empties do NOT render. Every automated check missed this: camera renders never show them,
and the visual oracle's bounds pass explicitly skips `obj.type == "EMPTY"`. It took a human
opening the file. These locks are the regression guard.

The first fix scaled the quad's extent but never converted it: ``target_quad_model`` is in
model MILLIMETRES (pdfcadcore.primitives), so a 6 mm glyph arrived as ``extent = 6.0`` and
every carrier on every sheet hit the 10 mm ceiling and drew a 20 mm cross. The owner saw
those as vertical lines standing off the S-505 foundation sheet when the view was tilted.
The locks below missed it because they passed quads already scaled to metres
(``0.004`` for a 4 mm glyph), the one input shape the production code never sees. They now
use millimetre quads, the units the caller actually passes.
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest


def _install_blender_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same stub shape the remediation-contract tests use: bl_text_builder imports bpy."""
    fake_bpy = types.SimpleNamespace(
        app=types.SimpleNamespace(version=(5, 2, 0)),
        ops=types.SimpleNamespace(
            wm=types.SimpleNamespace(redraw_timer=lambda **_kwargs: None),
        ),
        types=types.SimpleNamespace(
            Collection=object, Material=object, Object=object, VectorFont=object,
        ),
    )
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    monkeypatch.setitem(sys.modules, "bmesh", types.SimpleNamespace())


@pytest.fixture()
def btb(monkeypatch: pytest.MonkeyPatch):
    _install_blender_stubs(monkeypatch)
    return importlib.import_module("pdf_vector_importer.bl_text_builder")


SHEET_M = 0.887  # the 1011 sheet width, for scale comparisons


def _quad_mm(width_mm: float, height_mm: float):
    """A target_quad_model as the extractor emits it: upper-left first, millimetres."""
    return (
        (0.0, height_mm),
        (width_mm, height_mm),
        (width_mm, 0.0),
        (0.0, 0.0),
    )


def test_carrier_display_size_is_glyph_scaled_not_default_metre(btb):
    size = btb._carrier_display_size_m(_quad_mm(4.0, 6.0))
    assert size < 0.01, "a carrier may never approach Blender's 1.0 m default"
    assert size < SHEET_M / 100.0, "must be negligible against the sheet"
    assert size > 0.0


def test_a_real_millimetre_glyph_does_not_hit_the_ceiling(btb):
    """The regression: every glyph used to clamp to the maximum and draw a 20 mm cross."""
    size = btb._carrier_display_size_m(_quad_mm(4.0, 6.0))
    assert size < btb._CARRIER_DISPLAY_MAX_M
    assert size <= 0.001, "a 6 mm glyph must not produce a millimetre-plus gizmo"


def test_larger_glyphs_get_proportionally_larger_carriers(btb):
    small = btb._carrier_display_size_m(_quad_mm(2.0, 2.0))
    large = btb._carrier_display_size_m(_quad_mm(8.0, 8.0))
    assert large > small


def test_size_is_clamped_so_a_huge_quad_cannot_restore_the_starburst(btb):
    huge = _quad_mm(5000.0, 5000.0)
    assert btb._carrier_display_size_m(huge) <= btb._CARRIER_DISPLAY_MAX_M <= 0.01


def test_degenerate_or_missing_quads_fall_back_to_a_tiny_size(btb):
    for bad in (None, (), ((0.0, 0.0),), ((0.0, 0.0), (0.0, 0.0)), "nonsense"):
        size = btb._carrier_display_size_m(bad)
        assert 0.0 < size <= 0.01, f"{bad!r} produced {size}"


def test_the_creation_site_sets_both_display_properties(btb):
    import inspect
    src = inspect.getsource(btb)
    # Both properties are set together in one helper, called at the creation site.
    assert 'carrier.empty_display_type = "PLAIN_AXES"' in src
    assert "carrier.empty_display_size = _carrier_display_size_m(target_quad)" in src
    assert "_configure_affine_carrier_display(carrier, target_quad)" in src
