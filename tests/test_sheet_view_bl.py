"""Orthographic sheet distance matches Blender's viewport formula."""
from __future__ import annotations

import math

from pdf_vector_importer.sheet_view import orthographic_fit_distance


def test_square_sheet_uses_half_diagonal_and_default_lens():
    side = 2.0
    distance = orthographic_fit_distance(side, side, region_aspect=1.0, lens=50.0, sensor_width=36.0, margin=1.0)
    radius = math.hypot(side / 2.0, side / 2.0)
    assert abs(distance - radius * (50.0 / 36.0)) < 1e-9


def test_tall_window_backs_up_so_a_landscape_sheet_still_fits():
    wide = orthographic_fit_distance(4.0, 1.0, region_aspect=1.0, margin=1.0)
    tall_window = orthographic_fit_distance(4.0, 1.0, region_aspect=0.5, margin=1.0)
    assert tall_window > wide * 1.5


def test_distance_never_collapses_to_zero():
    assert orthographic_fit_distance(0.0, 0.0) >= 0.4
