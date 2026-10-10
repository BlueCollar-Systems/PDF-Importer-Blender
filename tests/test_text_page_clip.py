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


def test_source_clipped_to_zero_ink_stays_an_explicit_verified_outcome(monkeypatch):
    monkeypatch.setattr(clip, "_evaluated_ink", lambda *_args: ([], []))
    proof = clip.verify_clipped_ink(object(), object(), (0, 0, 1, 1), 0, (0, .1))
    assert proof["visible_ink_empty"] is True
    assert proof["visible_ink_area_verified"] is True


def _quad(x, y, width, height):
    return [(x, y, 0.0), (x + width, y, 0.0),
            (x + width, y + height, 0.0), (x, y + height, 0.0)]


def test_stacked_sheet_accepts_float32_clip_edge_and_keeps_later_pages(monkeypatch):
    # Sheet 7 of a letter stack sits near |Y|=2 m. A 0.6 mm strip whose clip
    # edge moves ten float32 steps changes area by about 4e-3 relative.
    # The old 2e-4 check called that lost ink, rolled the sheet back, and
    # never reached sheets 8–12. The text object stays on this rung.
    y0 = -2.012
    width = 0.004
    height = 0.0006
    shift = 10 * clip._float32_ulp(abs(y0))
    points = _quad(0.01, y0, width, height - shift)
    expected = width * height
    actual = clip.polygon_area([point[:2] for point in points])
    assert abs(actual - expected) / expected > 2e-4
    monkeypatch.setattr(clip, "_evaluated_ink", lambda *_args: (points, [points]))
    proof = clip.verify_clipped_ink(
        object(), object(), (0.0, y0, 0.216, y0 + 0.2794), expected, (0.0, 0.0))
    assert proof["visible_ink_area_verified"] is True
    assert proof["inside_page_verified"] is True


def test_stacked_sheet_still_rejects_half_of_a_clipped_strip(monkeypatch):
    y0 = -2.012
    width = 0.004
    height = 0.0006
    points = _quad(0.01, y0, width, height * 0.5)
    monkeypatch.setattr(clip, "_evaluated_ink", lambda *_args: (points, [points]))
    with pytest.raises(ValueError, match="visible source ink area"):
        clip.verify_clipped_ink(
            object(), object(), (0.0, y0, 0.216, y0 + 0.2794), width * height, (0.0, 0.0))


def test_stacked_sheet_allows_a_float32_vertex_but_not_a_millimetre(monkeypatch):
    y0 = -2.012
    nudge = 10 * clip._float32_ulp(abs(y0))
    inside = [(0.01, y0 - nudge, 0.0), (0.02, y0 - nudge, 0.0), (0.015, y0 + 0.01, 0.0)]
    area = clip.polygon_area([point[:2] for point in inside])
    monkeypatch.setattr(clip, "_evaluated_ink", lambda *_args: (inside, [inside]))
    proof = clip.verify_clipped_ink(
        object(), object(), (0.0, y0, 0.216, y0 + 0.2794), area, (0.0, 0.0))
    assert proof["inside_page_verified"] is True

    outside = [(0.01, y0 - 0.001, 0.0), (0.02, y0 - 0.001, 0.0), (0.015, y0 + 0.01, 0.0)]
    monkeypatch.setattr(clip, "_evaluated_ink", lambda *_args: (outside, [outside]))
    with pytest.raises(ValueError, match="out-of-page ink"):
        clip.verify_clipped_ink(
            object(), object(), (0.0, y0, 0.216, y0 + 0.2794), area, (0.0, 0.0))
