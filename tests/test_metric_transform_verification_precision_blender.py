"""Metric character verification must evaluate the recorded affine in double precision.

On a 48 x 36 in sheet (1.2192 m wide) 15 of 324 spans failed
``evaluated_font_advance_axis_mismatch``. Instrumenting the verifier showed the
recorded ``pdf_affine_matrix`` maps the local advance onto the target quad with
a delta of exactly 0.0 m in double precision for all 3,404 positioned
characters, while the verifier's ``mathutils.Matrix @ Vector`` evaluation --
single precision, whose spacing at [1, 2) m is 1.19e-7 m -- drifted by up to
1.13e-7 m against the 1e-7 m tolerance. Every failing character sat right of
x = 1.0 m; the sheet's 90-degree spans all passed because their advance has no
x component to round. The residue is host arithmetic, not placement: this
suite pins that the intended matrix is verified in double precision, that the
same matrix evaluated in single precision reproduces the pre-fix miss, and
that a real placement error is still rejected.
"""
from __future__ import annotations

import math
import struct
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
# 600-dpi export quantum: coordinates are integers times 0.12 pt.
QUANTUM_MM = 0.12 * 25.4 / 72.0


def _f32(value: float) -> float:
    """Round a double to the nearest single-precision value (what Blender stores)."""
    return struct.unpack("f", struct.pack("f", float(value)))[0]


class _SinglePrecisionVector:
    def __init__(self, values):
        self.values = tuple(_f32(v) for v in values)

    def __getitem__(self, index):
        return self.values[index]


class _SinglePrecisionMatrix:
    """mathutils.Matrix stand-in: float32 storage and float32 arithmetic."""

    def __init__(self, rows):
        self.rows = tuple(tuple(_f32(v) for v in row) for row in rows)

    def __getitem__(self, index):
        return self.rows[index]

    def __matmul__(self, vector):
        out = []
        for row in self.rows:
            acc = _f32(row[0] * vector[0])
            acc = _f32(acc + _f32(row[1] * vector[1]))
            acc = _f32(acc + _f32(row[2] * vector[2]))
            acc = _f32(acc + row[3])
            out.append(acc)
        return _SinglePrecisionVector(out)


def _install_single_precision_mathutils(monkeypatch):
    fake = types.ModuleType("mathutils")
    fake.Matrix = _SinglePrecisionMatrix
    fake.Vector = _SinglePrecisionVector
    monkeypatch.setitem(sys.modules, "mathutils", fake)
    return fake


def _rotated_character(*, degrees: float, origin_quanta, advance_quanta=82, height_quanta=100):
    """One positioned character rotated ``degrees`` with a 600-dpi-quantised origin."""
    cos_a, sin_a = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    advance_mm = advance_quanta * QUANTUM_MM
    height_mm = height_quanta * QUANTUM_MM
    x_mm, y_mm = (origin_quanta[0] * QUANTUM_MM, origin_quanta[1] * QUANTUM_MM)
    h_axis = (advance_mm * cos_a, advance_mm * sin_a)
    v_axis = (-height_mm * sin_a, height_mm * cos_a)
    ll = (x_mm, y_mm)
    lr = (x_mm + h_axis[0], y_mm + h_axis[1])
    ul = (x_mm + v_axis[0], y_mm + v_axis[1])
    ur = (x_mm + h_axis[0] + v_axis[0], y_mm + h_axis[1] + v_axis[1])
    item = NormalizedText(
        id=1,
        text="R",
        normalized="R",
        insertion=ll,
        bbox=(min(p[0] for p in (ul, ur, lr, ll)), min(p[1] for p in (ul, ur, lr, ll)),
              max(p[0] for p in (ul, ur, lr, ll)), max(p[1] for p in (ul, ur, lr, ll))),
        target_quad_model=(ul, ur, lr, ll),
        advance_width=advance_mm,
        glyph_height=height_mm,
        font_size=height_mm,
        rotation=degrees,
        page_number=1,
        positioned_character=True,
    )
    metrics = {
        "local_advance": advance_mm * MM_TO_M,
        "local_line_height": height_mm * MM_TO_M,
        "local_baseline_y": 0.0,
    }
    return item, metrics


def _placed_object(item, metrics, *, translation_error_m=(0.0, 0.0), advance_sign=1.0):
    matrix = bl_text_builder._metric_character_matrix_values(
        local_advance=metrics["local_advance"],
        local_line_height=metrics["local_line_height"],
        local_baseline_y=metrics["local_baseline_y"],
        target_origin=item.insertion,
        target_quad=item.target_quad_model,
        z=0.0,
    )
    rows = [list(row) for row in matrix]
    rows[0][0] *= advance_sign
    rows[1][0] *= advance_sign
    rows[0][3] += translation_error_m[0]
    rows[1][3] += translation_error_m[1]
    obj = {
        "pdf_affine_matrix": [float(v) for row in rows for v in row],
        "pdf_metric_local_advance": metrics["local_advance"],
        "pdf_metric_local_line_height": metrics["local_line_height"],
        "pdf_metric_local_baseline_y": metrics["local_baseline_y"],
        "pdf_full_affine_applied": True,
        "pdf_metric_affine_applied": True,
    }
    return types.SimpleNamespace(name="P1_text_3d_text_1_c0000", get=obj.get, parent=None), rows


# The sheet's failing characters: 30-degree-class rotations right of x = 1 m.
# origin (26648, 11815) quanta = (1128.0987 mm, 500.4223 mm).
_RIGHT_OF_ONE_METRE = dict(degrees=30.0, origin_quanta=(26648, 11815))
# The sheet's 90-degree spans ('0 1 -1 0' Tm): advance runs along +y.
_VERTICAL_SPAN = dict(degrees=90.0, origin_quanta=(26648, 11815))


def test_single_precision_evaluation_of_the_recorded_matrix_misses_the_tolerance():
    """The mechanism behind the pre-fix failures, pinned on the test data itself."""
    item, metrics = _rotated_character(**_RIGHT_OF_ONE_METRE)
    obj, rows = _placed_object(item, metrics)
    matrix = _SinglePrecisionMatrix(rows)
    single = matrix @ _SinglePrecisionVector((metrics["local_advance"], 0.0, 0.0))
    expected = tuple(v * MM_TO_M for v in item.target_quad_model[2])  # LR = origin + advance
    single_delta = max(abs(single[0] - expected[0]), abs(single[1] - expected[1]))
    values = obj.get("pdf_affine_matrix")
    double = (
        values[0] * metrics["local_advance"] + values[3],
        values[4] * metrics["local_advance"] + values[7],
    )
    double_delta = max(abs(double[0] - expected[0]), abs(double[1] - expected[1]))
    assert expected[0] > 1.0  # right of one metre: float32 spacing is 1.19e-7 m
    assert single_delta > 1e-7
    assert double_delta <= 1e-12


def test_rotated_character_right_of_one_metre_verifies_in_double_precision(monkeypatch):
    _install_single_precision_mathutils(monkeypatch)
    item, metrics = _rotated_character(**_RIGHT_OF_ONE_METRE)
    obj, rows = _placed_object(item, metrics)

    failures, evidence = bl_text_builder._verify_metric_character_transform(obj, item)

    assert failures == []
    assert evidence["metric_affine_applied"] is True
    assert evidence["evaluated_bounds_verified"] is True
    assert evidence["expected_location_m"] == pytest.approx(
        [item.insertion[0] * MM_TO_M, item.insertion[1] * MM_TO_M]
    )
    # The reported actual location is what the host stores: single precision.
    assert evidence["actual_location_m"] == [_f32(rows[0][3]), _f32(rows[1][3])]


def test_vertical_span_right_of_one_metre_verifies(monkeypatch):
    _install_single_precision_mathutils(monkeypatch)
    item, metrics = _rotated_character(**_VERTICAL_SPAN)
    obj, _rows = _placed_object(item, metrics)

    failures, _evidence = bl_text_builder._verify_metric_character_transform(obj, item)

    assert failures == []


@pytest.mark.parametrize(
    ("translation_error_m", "advance_sign", "expected_failures"),
    [
        # One 600-dpi quantum (42.3 um) of drift is a placement error.
        ((QUANTUM_MM * MM_TO_M, 0.0), 1.0, {
            "evaluated_baseline_anchor_mismatch",
            "evaluated_font_advance_axis_mismatch",
            "evaluated_font_line_axis_mismatch",
        }),
        # A tenth of a micrometre beyond the tolerance still fails: nothing was widened.
        ((0.0, 2e-7), 1.0, {
            "evaluated_baseline_anchor_mismatch",
            "evaluated_font_advance_axis_mismatch",
            "evaluated_font_line_axis_mismatch",
        }),
        # A mirrored advance axis is caught on the advance point alone.
        ((0.0, 0.0), -1.0, {"evaluated_font_advance_axis_mismatch"}),
    ],
)
def test_real_placement_errors_are_still_rejected(
    monkeypatch, translation_error_m, advance_sign, expected_failures
):
    _install_single_precision_mathutils(monkeypatch)
    item, metrics = _rotated_character(**_RIGHT_OF_ONE_METRE)
    obj, _rows = _placed_object(
        item, metrics, translation_error_m=translation_error_m, advance_sign=advance_sign
    )

    failures, _evidence = bl_text_builder._verify_metric_character_transform(obj, item)

    assert set(failures) == expected_failures


def test_verifier_does_not_depend_on_mathutils(monkeypatch):
    """The check is pure arithmetic on the recorded doubles; no host maths module."""
    monkeypatch.setitem(sys.modules, "mathutils", None)
    item, metrics = _rotated_character(**_RIGHT_OF_ONE_METRE)
    obj, _rows = _placed_object(item, metrics)

    failures, evidence = bl_text_builder._verify_metric_character_transform(obj, item)

    assert failures == []
    assert evidence["evaluated_bounds_verified"] is True
