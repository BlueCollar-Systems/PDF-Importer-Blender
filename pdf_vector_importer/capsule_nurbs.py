"""Exact rational quadratic controls for a planar PDF round-cap footprint.

Blender's cyclic Bezier NURBS flag repeats quadratic knots twice. With order
three, the controls below represent straight sides and circular quarter arcs;
the curve's viewport tessellation is distinct from that exact stored boundary.
"""

from __future__ import annotations

import math
import struct


def native_float_close(actual, expected):
    """Permit only native float32 storage error, not a geometric fit tolerance."""
    if not math.isfinite(actual) or not math.isfinite(expected):
        return False
    magnitude = abs(float(expected))
    bits = struct.unpack("I", struct.pack("f", magnitude))[0]
    if bits >= 0x7F7FFFFF:
        return False
    budget = 4 * (struct.unpack("f", struct.pack("I", bits + 1))[0] - magnitude)
    return abs(actual - expected) <= budget


def capsule_controls(capsule):
    """Return source-local (x, y, weight) controls and the source origin.

    The first control precedes the first endpoint in the cyclic basis. Never
    convert the circular boundary to a polygon or change the source centerline.
    """
    start, end = tuple(capsule["start"]), tuple(capsule["end"])
    width = float(capsule["width"])
    if (
        len(start) != 2
        or len(end) != 2
        or width <= 0
        or not all(math.isfinite(v) for v in (*start, *end, width))
    ):
        raise ValueError("Invalid source capsule")
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    if length > width or length != capsule["length"]:
        raise ValueError("Source capsule length disagrees with its endpoints")
    ux, uy = (
        ((end[0] - start[0]) / length, (end[1] - start[1]) / length)
        if length
        else (1.0, 0.0)
    )
    radius = width / 2
    w = math.sqrt(0.5)
    # Each segment is (start, middle control, end), in the local centerline frame.
    upper_start, upper_end = (0.0, radius), (length, radius)
    right, lower_end = (length + radius, 0.0), (length, -radius)
    lower_start, left = (0.0, -radius), (-radius, 0.0)
    segments = []
    if length:
        segments.append((upper_start, (length / 2, radius), upper_end, 1.0))
    segments.extend(
        [
            (upper_end, (length + radius, radius), right, w),
            (right, (length + radius, -radius), lower_end, w),
        ]
    )
    if length:
        segments.append((lower_end, (length / 2, -radius), lower_start, 1.0))
    segments.extend(
        [
            (lower_start, (-radius, -radius), left, w),
            (left, (-radius, radius), upper_start, w),
        ]
    )
    controls = [(segments[-1][1], segments[-1][3])]
    for index, (first, middle, last, weight) in enumerate(segments):
        if index == 0:
            controls.append((first, 1.0))
        if index < len(segments) - 1:
            controls.extend([(middle, weight), (last, 1.0)])
    rotated = [
        (ux * x - uy * y, uy * x + ux * y, weight) for (x, y), weight in controls
    ]
    return tuple(start), rotated


def cyclic_quadratic_knots(control_count):
    """The periodic quadratic Bezier knot contract used by Blender 3.6+.

    Tests use an independent Cox-de Boor evaluator against the analytic capsule.
    Native verification must additionally check the saved flags and controls.
    """
    if type(control_count) is not int or control_count < 6 or control_count % 2:
        raise ValueError(
            "Cyclic quadratic controls must contain endpoint/control pairs"
        )
    return tuple(float(i // 2) for i in range(control_count + 5))
