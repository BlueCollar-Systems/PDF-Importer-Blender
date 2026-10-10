"""Visible source ink and native depth must survive page viewport clipping."""
import importlib.util
import sys
import types
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


class _Identity:
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
