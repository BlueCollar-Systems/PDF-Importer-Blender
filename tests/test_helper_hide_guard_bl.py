"""Failing to hide a helper must never cost the text it belongs to.

``hide_set()`` writes VIEW LAYER visibility. Blender raises when the object is
not in the view layer, which happens whenever the object's collection is not
linked into the scene:

    RuntimeError: Object 'P1_text_glyphs_2_c0012_g44_AffineCarrier' cannot be
    hidden because it is not in View Layer 'ViewLayer'!

The affine-carrier build called it unguarded. On the S-505 48x36 markup sheet
that RuntimeError escaped the glyph conversion, was classified
``glyph_curve_conversion_failed_not_impossibility_proof`` -- explicitly NOT an
impossibility proof, so the fidelity ladder was not allowed to descend -- and
the whole import was refused: ``text delivery failed (required=324,
recorded=324, delivered=16, failed=308)``. Both `glyphs` and `geometry` mode
were unusable on that drawing while `text`, `labels`, `3d_text` and `raster`
all imported fine.

Two sibling call sites already guarded the same call (the startup-cube hide and
the carrier un-hide); these two did not. The object-level ``hide_viewport``
toggle always applies and keeps the gizmo from drawing, so there is never a
reason to lose a text item over it.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for path in (str(REPO), str(REPO / "pdf_vector_importer")):
    if path not in sys.path:
        sys.path.insert(0, path)


def _load_hide_helper():
    """Import just the guard, without pulling bpy in."""
    import importlib.util

    source = (REPO / "pdf_vector_importer" / "bl_text_builder.py").read_text(
        encoding="utf-8"
    )
    start = source.index("def _hide_helper_object(obj):")
    end = source.index("\n\n", source.index("        obj.hide_viewport = True", start))
    namespace: dict = {}
    exec(compile(source[start:end], "_hide_helper_object", "exec"), namespace)
    return namespace["_hide_helper_object"]


class _Obj:
    """Stands in for a Blender object with a configurable hide_set."""

    def __init__(self, hide_set_raises=False, hide_viewport_raises=False):
        self._hide_set_raises = hide_set_raises
        self._hide_viewport_raises = hide_viewport_raises
        self.hide_set_called_with = None
        self._hide_viewport = False

    def hide_set(self, value):
        self.hide_set_called_with = value
        if self._hide_set_raises:
            raise RuntimeError(
                "Object 'P1_text_glyphs_2_c0012_g44_AffineCarrier' cannot be "
                "hidden because it is not in View Layer 'ViewLayer'!"
            )

    @property
    def hide_viewport(self):
        return self._hide_viewport

    @hide_viewport.setter
    def hide_viewport(self, value):
        if self._hide_viewport_raises:
            raise RuntimeError("hide_viewport unavailable")
        self._hide_viewport = value


def test_a_carrier_outside_the_view_layer_is_still_hidden():
    hide = _load_hide_helper()
    obj = _Obj(hide_set_raises=True)
    hide(obj)  # must not raise: this is what killed 308 of 324 spans
    assert obj.hide_set_called_with is True
    assert obj.hide_viewport is True, "the gizmo must still be kept from drawing"


def test_the_normal_path_still_hides_both_ways():
    hide = _load_hide_helper()
    obj = _Obj()
    hide(obj)
    assert obj.hide_set_called_with is True
    assert obj.hide_viewport is True


def test_even_a_total_failure_to_hide_does_not_raise():
    # Losing the gizmo is a cosmetic defect; losing the text is not.
    hide = _load_hide_helper()
    obj = _Obj(hide_set_raises=True, hide_viewport_raises=True)
    hide(obj)


@pytest.mark.parametrize(
    "source_file, call",
    [
        ("bl_text_builder.py", "_hide_helper_object(carrier)"),
        ("bl_import_engine.py", "obj.hide_set(True)"),
    ],
)
def test_no_unguarded_hide_set_remains_on_the_carrier_paths(source_file, call):
    """Pin that neither carrier path calls hide_set bare again."""
    import re

    source = (REPO / "pdf_vector_importer" / source_file).read_text(encoding="utf-8")
    assert call in source
    # A real call only: prose in a docstring writes ``hide_set()`` with no
    # receiver, and must not be mistaken for one.
    call_re = re.compile(r"\w\.hide_set\(")
    for line_no, line in enumerate(source.splitlines(), 1):
        if not call_re.search(line) or line.strip().startswith("#"):
            continue
        window = "\n".join(source.splitlines()[max(0, line_no - 3) : line_no + 2])
        assert "try:" in window or "def _hide_helper_object" in window, (
            f"{source_file}:{line_no} calls hide_set outside a try/except: "
            f"{line.strip()}"
        )
