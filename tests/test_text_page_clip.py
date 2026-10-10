"""Visible source ink and native depth must survive page viewport clipping."""
import importlib.util
from pathlib import Path

import pymupdf
import pytest

spec = importlib.util.spec_from_file_location(
    "text_page_clip_under_test", Path(__file__).parents[1] / "pdf_vector_importer/text_page_clip.py")
clip = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clip)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("scale", [1, 4])
def test_independent_source_renderer_pixels_survive_triangle_intersection(rotation, scale):
    # Integer source intersections avoid a renderer's antialias quantization
    # changing when an oblique edge is split at a fractional pixel position.
    triangle = [(-20, 20), (100, 20), (40, 80)]
    clipped = clip.rectangle_intersection(triangle, (0, 0, 80, 60))
    samples = []
    for points in (triangle, clipped):
        with pymupdf.open() as document:
            page = document.new_page(width=80, height=60)
            page.draw_polyline(points, color=None, fill=(0, 0, 0), closePath=True)
            page.set_rotation(rotation)
            samples.append(page.get_pixmap(matrix=pymupdf.Matrix(scale, scale)).samples)
    assert samples[0] == samples[1]


def test_fractional_visible_triangle_area_is_the_analytic_integral():
    # Integral of 1-x from0 to1/2 =3/8, without raster quantization.
    triangle = [(-1, 0), (1, 0), (0, 1)]
    assert clip.polygon_area(clip.rectangle_intersection(triangle, (0, 0, .5, 1))) == .375


def test_independent_projected_ink_measure_rejects_lost_visible_geometry(monkeypatch):
    points = [(0, 0, .2), (1, 0, .2), (0, 1, .2)]
    monkeypatch.setattr(clip, "_evaluated_ink", lambda *_args: (points, [points]))
    with pytest.raises(ValueError, match="visible source ink area"):
        clip.verify_clipped_ink(object(), object(), (0, 0, 1, 1), 1.0, (.2, .2))


def test_page_containment_rejects_off_sheet_ink_even_when_area_matches(monkeypatch):
    points = [(0, 0, .2), (2, 0, .2), (0, .5, .2)]
    monkeypatch.setattr(clip, "_evaluated_ink", lambda *_args: (points, [points]))
    with pytest.raises(ValueError, match="out-of-page ink"):
        clip.verify_clipped_ink(object(), object(), (0, 0, 1, 1), .5, (.2, .2))


def test_verified_ink_cannot_hide_a_flattened_3d_text_depth(monkeypatch):
    points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    monkeypatch.setattr(clip, "_evaluated_ink", lambda *_args: (points, [points]))
    with pytest.raises(ValueError, match="source text depth"):
        clip.verify_clipped_ink(object(), object(), (0, 0, 1, 1), .5, (0, .1))


def test_stacked_sheet_float32_noise_does_not_reject_clipped_ink(monkeypatch):
    import struct

    def f32(value):
        return struct.unpack("f", struct.pack("f", value))[0]

    local = [(0.01, 0.01, 0.2), (0.03, 0.01, 0.2), (0.01, 0.04, 0.2)]
    shift = 6.0
    points = [(f32(point[0] + shift), f32(point[1] + shift), point[2]) for point in local]
    monkeypatch.setattr(clip, "_evaluated_ink", lambda *_args: (points, [points]))
    area = clip.polygon_area(local)
    proof = clip.verify_clipped_ink(
        object(), object(), (shift, shift, shift + 1.0, shift + 1.0), area, (0.2, 0.2))
    assert proof["inside_page_verified"] is True
    assert proof["visible_ink_area_verified"] is True


def test_stacked_sheet_still_rejects_a_real_millimetre_leak(monkeypatch):
    import struct

    def f32(value):
        return struct.unpack("f", struct.pack("f", value))[0]

    shift = 6.0
    points = [(f32(shift - 0.001), f32(shift + 0.01), 0.2),
              (f32(shift + 0.02), f32(shift + 0.01), 0.2),
              (f32(shift + 0.01), f32(shift + 0.03), 0.2)]
    monkeypatch.setattr(clip, "_evaluated_ink", lambda *_args: (points, [points]))
    with pytest.raises(ValueError, match="out-of-page ink"):
        clip.verify_clipped_ink(
            object(), object(), (shift, shift, shift + 1.0, shift + 1.0), 0.0001, (0.2, 0.2))


def test_source_clipped_to_zero_ink_stays_an_explicit_verified_outcome(monkeypatch):
    monkeypatch.setattr(clip, "_evaluated_ink", lambda *_args: ([], []))
    proof = clip.verify_clipped_ink(object(), object(), (0, 0, 1, 1), 0, (0, .1))
    assert proof["visible_ink_empty"] is True
    assert proof["visible_ink_area_verified"] is True
