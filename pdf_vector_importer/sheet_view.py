# -*- coding: utf-8 -*-
"""Orthographic distance that frames a PDF sheet face-on.

Blender's viewport formula (ED_view3d_radius_to_dist) is

    distance = radius * lens / sensor_width

with the radius expanded for a non-square window so the sheet fits the
shorter side. A constant multiple of the longer span ignores that and
clips a landscape print in a tall window, or leaves the default
perspective orbit if the distance never matches the lens.
"""
from __future__ import annotations

import math

# Blender DEFAULT_SENSOR_WIDTH for the viewport camera (millimetres).
DEFAULT_SENSOR_WIDTH = 36.0
DEFAULT_LENS = 50.0


def orthographic_fit_distance(
    span_x: float,
    span_y: float,
    *,
    region_aspect: float = 1.0,
    lens: float = DEFAULT_LENS,
    sensor_width: float = DEFAULT_SENSOR_WIDTH,
    margin: float = 1.05,
) -> float:
    """View distance for a top orthographic camera over a sheet of this span."""
    half_x = max(abs(float(span_x)), 1.0e-9) * 0.5
    half_y = max(abs(float(span_y)), 1.0e-9) * 0.5
    radius = math.hypot(half_x, half_y)
    try:
        aspect = float(region_aspect)
    except (TypeError, ValueError):
        aspect = 1.0
    if not math.isfinite(aspect) or aspect <= 0.0:
        aspect = 1.0
    # Match ED_view3d_radius_to_dist(use_aspect): grow the radius so the
    # bounding sphere fits the shorter screen dimension.
    if aspect < 1.0:
        aspect = 1.0 / aspect
    try:
        lens_value = float(lens)
    except (TypeError, ValueError):
        lens_value = DEFAULT_LENS
    try:
        sensor = float(sensor_width)
    except (TypeError, ValueError):
        sensor = DEFAULT_SENSOR_WIDTH
    if not math.isfinite(lens_value) or lens_value <= 0.0:
        lens_value = DEFAULT_LENS
    if not math.isfinite(sensor) or sensor <= 0.0:
        sensor = DEFAULT_SENSOR_WIDTH
    distance = radius * (lens_value / sensor) * aspect * float(margin)
    if not math.isfinite(distance) or distance <= 0.0:
        return 0.4
    return max(distance, 0.4)
