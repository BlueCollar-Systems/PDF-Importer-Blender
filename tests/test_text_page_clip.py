"""Visible source ink and native depth must survive page viewport clipping."""
import importlib.util
import sys
import types
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


class _Identity(list):
    def __init__(self):
        super().__init__([[1.,0.,0.,0.],[0.,1.,0.,0.],[0.,0.,1.,0.],[0.,0.,0.,1.]])

    def __matmul__(self, vector):
        return tuple(vector)


class _Modifiers(list):
    def new(self, name, kind):
        modifier = types.SimpleNamespace(name=name, type=kind, node_group=None)
        self.append(modifier)
        return modifier


class _Letter(dict):
    """A delivered letter that pokes past the right sheet edge."""

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.type = "FONT"
        self.matrix_world = _Identity()
        self.bound_box = [(0.9, 0.1, 0.0), (1.2, 0.3, 0.001)]
        self.modifiers = _Modifiers()

    def evaluated_get(self, _depsgraph):
        return self


def _stub_blender(monkeypatch, letters):
    removed = []
    objects = types.SimpleNamespace(get={obj.name: obj for obj in letters}.get,
                                    remove=lambda obj, **_k: removed.append(obj.name))
    bpy = types.SimpleNamespace(
        context=types.SimpleNamespace(view_layer=types.SimpleNamespace(update=lambda: None),
                                      evaluated_depsgraph_get=lambda: None),
        data=types.SimpleNamespace(objects=objects,
                                   meshes=types.SimpleNamespace(remove=lambda _m: None),
                                   node_groups=types.SimpleNamespace(remove=lambda t: removed.append(t.name))))
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setitem(sys.modules, "mathutils", types.SimpleNamespace(Vector=tuple))
    ink = [(0.9, 0.1, 0.0), (1.2, 0.1, 0.0), (0.9, 0.3, 0.001)]
    monkeypatch.setattr(clip, "_evaluated_ink", lambda *_args: (ink, [ink]))
    monkeypatch.setattr(clip, "_page_prism", lambda *_args: types.SimpleNamespace(
        name="PDF text viewport helper", data=object()))
    monkeypatch.setattr(clip, "_clip_tree", lambda *_args: types.SimpleNamespace(
        name="PDF text page viewport"))
    return removed


def _edge_record(*names):
    return {"page": 2, "item_id": "page:2:text:1", "status": "delivered",
            "final_representation": "3d_text", "entity_ids": list(names)}


def test_one_letter_failing_its_trim_check_stays_untrimmed_and_the_rest_are_trimmed(monkeypatch):
    good, bad = _Letter("D042_EX102_c0"), _Letter("D042_EX102_c1")
    _stub_blender(monkeypatch, [good, bad])

    def verify(obj, *_args):
        if obj is bad:
            raise ValueError("Native text viewport changed the visible source ink area")
        return {"inside_page_verified": True}

    monkeypatch.setattr(clip, "verify_clipped_ink", verify)
    record = _edge_record(good.name, bad.name)
    result = clip.clip_delivered_page_text(None, [record], page_number=2,
                                           width_mm=1000, height_mm=1000)
    assert [proof["entity_id"] for proof in result] == [good.name]
    assert len(good.modifiers) == 1 and good["pdf_page_clip_helper_id"] == "PDF text viewport helper"
    assert [proof["entity_id"] for proof in record["page_viewport_clips"]] == [good.name]
    assert len(bad.modifiers) == 0 and "pdf_page_clip_helper_id" not in bad
    skipped = {"entity_id": bad.name, "page": 2,
               "reason": "ValueError: Native text viewport changed the visible source ink area"}
    assert record["page_viewport_clip_skipped"] == [skipped]
    assert result.skipped == [skipped]
    assert record["status"] == "delivered"


def test_trim_tool_that_cannot_be_built_leaves_every_edge_letter_as_delivered(monkeypatch):
    letters = [_Letter("D042_EX103_c0"), _Letter("D042_EX103_c1")]
    _stub_blender(monkeypatch, letters)

    def no_tree(*_args):
        raise RuntimeError("node tree unavailable")

    monkeypatch.setattr(clip, "_clip_tree", no_tree)
    record = _edge_record(*(letter.name for letter in letters))
    result = clip.clip_delivered_page_text(None, [record], page_number=2,
                                           width_mm=1000, height_mm=1000)
    assert list(result) == [] and len(result.skipped) == 2
    assert all(not letter.modifiers for letter in letters)
    assert "page_viewport_clips" not in record


def test_an_unused_trim_guide_is_removed(monkeypatch):
    letter = _Letter("D042_EX104_c0")
    removed = _stub_blender(monkeypatch, [letter])
    monkeypatch.setattr(clip, "verify_clipped_ink",
                        lambda *_args: (_ for _ in ()).throw(ValueError("area")))
    result = clip.clip_delivered_page_text(None, [_edge_record(letter.name)], page_number=2,
                                           width_mm=1000, height_mm=1000)
    assert len(result.skipped) == 1 and not letter.modifiers
    assert removed == ["PDF text page viewport", "PDF text viewport helper"]


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
