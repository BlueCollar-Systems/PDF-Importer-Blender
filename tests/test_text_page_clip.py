"""Visible source ink and native depth must survive page viewport clipping."""
import importlib.util
from pathlib import Path
import struct
from types import SimpleNamespace

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


def _f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


class _Float32Matrix(list):
    def __matmul__(self, point):
        return tuple(_f32(sum(self[row][column] * point[column] for column in range(3))
                          + self[row][3]) for row in range(3))


def _tiny_evaluated_surface(y, *, scale_x=1, height=.0005):
    # Fictional front/back caps with the same coordinate scale as thin clipped
    # glyphs. This is a real evaluator boundary, not a stand-in returned proof.
    vertices = [SimpleNamespace(co=tuple(map(_f32, p))) for p in
                [(0, 0, 0), (.001, 0, 0), (.001, height, 0), (0, height, 0)]]
    triangles = [SimpleNamespace(vertices=indices) for indices in
                 [(0, 1, 2), (0, 2, 3), (2, 1, 0), (3, 2, 0)]]
    matrix = _Float32Matrix([[_f32(scale_x), 0., 0., 0.], [0., 1., 0., _f32(y)],
                            [0., 0., 1., .2], [0., 0., 0., 1.]])
    mesh = SimpleNamespace(vertices=vertices, loop_triangles=triangles, calc_loop_triangles=lambda: None)
    cleared = []
    evaluated = SimpleNamespace(matrix_world=matrix, to_mesh=lambda: mesh, to_mesh_clear=lambda: cleared.append(True))
    obj = SimpleNamespace(evaluated_get=lambda _graph: evaluated)
    bpy = SimpleNamespace(context=SimpleNamespace(evaluated_depsgraph_get=lambda: object()))
    return obj, bpy, evaluated, mesh, cleared


@pytest.mark.parametrize('y', [.1430920660495758, .1430920660495758 - 5.36702, -50., 50.])
def test_tiny_native_cap_area_survives_stack_translation(y):
    obj, bpy, _evaluated, _mesh, cleared = _tiny_evaluated_surface(y)
    expected = 2 * _f32(.001) * _f32(.0005)
    proof = clip.verify_clipped_ink(obj, bpy, (-.01, y - .01, .01, y + .01), expected, (.2, .2))
    assert proof['visible_projected_area_m2'] == pytest.approx(expected, rel=1e-10, abs=0)
    assert cleared == [True]


def test_old_float32_world_projection_rejects_translation_only():
    # The old matrix@vertex result loses information before polygon_area sees it.
    obj, bpy, evaluated, mesh, _cleared = _tiny_evaluated_surface(.1430920660495758 - 5.36702)
    rounded = [evaluated.matrix_world @ v.co for v in mesh.vertices]
    old_area = sum(clip.polygon_area([rounded[i] for i in t.vertices]) for t in mesh.loop_triangles)
    expected = 2 * _f32(.001) * _f32(.0005)
    assert abs(old_area - expected) > max(1e-12, expected * 2e-4)
    assert clip.verify_clipped_ink(obj, bpy, (-.01, -5.24, .01, -5.20), expected, (.2, .2))['visible_ink_area_verified']


@pytest.mark.parametrize('scale_x,height', [(1.01, .0005), (1., .0004)])
def test_large_stack_still_rejects_real_scale_or_clip_change(scale_x, height):
    obj, bpy, _evaluated, _mesh, cleared = _tiny_evaluated_surface(-5.2239, scale_x=scale_x, height=height)
    with pytest.raises(ValueError, match='visible source ink area'):
        clip.verify_clipped_ink(obj, bpy, (-.01, -5.24, .01, -5.20), 2 * _f32(.001) * _f32(.0005), (.2, .2))
    assert cleared == [True]


def test_native_mesh_changes_are_read_again_without_cached_proof():
    obj, bpy, _evaluated, mesh, cleared = _tiny_evaluated_surface(-5.2239)
    expected = 2 * _f32(.001) * _f32(.0005)
    clip.verify_clipped_ink(obj, bpy, (-.01, -5.24, .01, -5.20), expected, (.2, .2))
    mesh.vertices[2].co = (_f32(.0008), _f32(.0005), 0.)
    with pytest.raises(ValueError, match='visible source ink area'):
        clip.verify_clipped_ink(obj, bpy, (-.01, -5.24, .01, -5.20), expected, (.2, .2))
    assert cleared == [True, True]


def test_evaluator_clears_mesh_after_nonfinite_affine_failure():
    obj, bpy, evaluated, _mesh, cleared = _tiny_evaluated_surface(0.)
    evaluated.matrix_world[0][0] = float('inf')
    with pytest.raises(ValueError, match='non-finite geometry'):
        clip._evaluated_ink(obj, bpy)
    assert cleared == [True]
