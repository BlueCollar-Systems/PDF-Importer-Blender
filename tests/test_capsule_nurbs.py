"""Independent rational-basis proof, with no Blender host or PDF fixture."""

import math
import struct

import pytest

from pdf_vector_importer.capsule_nurbs import (
    capsule_controls,
    cyclic_quadratic_knots,
    native_float_close,
)


def test_native_control_budget_is_float_storage_error_only():
    for value in (0.0, 1e-6, -0.00031, 0.812398124, math.sqrt(0.5)):
        stored = struct.unpack("f", struct.pack("f", value))[0]
        assert native_float_close(stored, value)
        assert not native_float_close(stored + 1e-6, value)
    assert not native_float_close(float("nan"), 0.0)


def point_at(controls, knots, u):
    controls = controls + controls[:2]

    def basis(i, degree):
        if degree == 0:
            return float(knots[i] <= u < knots[i + 1])
        result = 0.0
        left = knots[i + degree] - knots[i]
        right = knots[i + degree + 1] - knots[i + 1]
        if left:
            result += (u - knots[i]) / left * basis(i, degree - 1)
        if right:
            result += (knots[i + degree + 1] - u) / right * basis(i + 1, degree - 1)
        return result

    weighted = [(basis(i, 2) * p[2], p) for i, p in enumerate(controls)]
    denom = sum(weight for weight, _ in weighted)
    return tuple(
        sum(weight * point[axis] for weight, point in weighted) / denom
        for axis in (0, 1)
    )


@pytest.mark.parametrize(
    "end,width",
    [((0.0, 0.0), 12.0), ((0.013, 0.0), 12.0), ((3.0, 4.0), 5.0), ((-4.0, 3.0), 8.0)],
)
def test_blender_periodic_basis_lies_on_exact_capsule(end, width):
    start = (1e6, -2e6)
    actual_end = tuple(a + b for a, b in zip(start, end, strict=True))
    vector = tuple(b - a for a, b in zip(start, actual_end, strict=True))
    length = math.hypot(*vector)
    capsule = dict(start=start, end=actual_end, width=width, length=length)
    origin, controls = capsule_controls(capsule)
    assert origin == start
    assert len(controls) == (12 if length else 8)
    assert {p[2] for p in controls} == {1.0, math.sqrt(0.5)}
    knots = cyclic_quadratic_knots(len(controls))
    assert knots[:4] == (0.0, 0.0, 1.0, 1.0)
    segment_count = len(controls) // 2
    for index in range(segment_count * 51):
        u = 1 + index / 51
        x, y = point_at(controls, knots, u)
        t = (
            min(1.0, max(0.0, (x * vector[0] + y * vector[1]) / length**2))
            if length
            else 0.0
        )
        distance = math.hypot(x - t * vector[0], y - t * vector[1])
        assert distance == pytest.approx(width / 2, abs=2e-12)
    # Periodic end and start converge to the exact same upper endpoint.
    assert point_at(controls, knots, 1 + 1e-10) == pytest.approx(
        point_at(controls, knots, segment_count + 1 - 1e-10), abs=1e-8
    )


@pytest.mark.parametrize(
    "change",
    [dict(width=0), dict(width=float("nan")), dict(length=0.2), dict(end=(100.0, 0.0))],
)
def test_malformed_or_unbounded_capsule_rejects(change):
    capsule = dict(start=(0.0, 0.0), end=(0.01, 0.0), width=12.0, length=0.01)
    capsule.update(change)
    with pytest.raises(ValueError):
        capsule_controls(capsule)
