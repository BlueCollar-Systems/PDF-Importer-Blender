import importlib
import sys
from types import SimpleNamespace

import pytest


def builder():
    bpy = sys.modules.setdefault("bpy", SimpleNamespace())
    bpy.types = getattr(bpy, "types", SimpleNamespace())
    for name in ("Collection", "Material", "Object"):
        if not hasattr(bpy.types, name):
            setattr(bpy.types, name, object)
    sys.modules.setdefault("bmesh", SimpleNamespace())
    return importlib.import_module("pdf_vector_importer.bl_geometry_builder")


def test_native_compound_fill_groups_hole_in_one_flat_curve(monkeypatch):
    module = builder()
    splines, objects, linked = [], [], []
    def spline_new(kind):
        assert kind == "POLY"
        spline = SimpleNamespace()
        splines.append(spline)
        return spline
    curve = SimpleNamespace(splines=SimpleNamespace(new=spline_new), materials=[])
    def object_new(name, data):
        obj = {}
        objects.append((name, data, obj))
        return obj
    monkeypatch.setattr(module.bpy, "data", SimpleNamespace(
        curves=SimpleNamespace(new=lambda **kwargs: curve), objects=SimpleNamespace(new=object_new)), raising=False)
    monkeypatch.setattr(module, "_write_spline_points", lambda spline, pts: setattr(spline, "coords", pts))
    contours = [[(0, 0), (10, 0), (10, 10), (0, 10)], [(3, 3), (7, 3), (7, 7), (3, 7)]]
    obj = module._create_compound_clip_fill("mask", contours, SimpleNamespace(objects=SimpleNamespace(link=linked.append)), "ink", True)
    assert len(objects) == 1 and linked == [obj]
    assert len(splines) == 2 and all(s.use_cyclic_u for s in splines)
    assert curve.dimensions == "2D" and curve.fill_mode == "BOTH"
    assert curve.bevel_depth == curve.extrude == 0
    assert splines[1].coords[0] == (.003, .003)
    assert obj["bcs_clip_fill_contour_count"] == 2


def test_unsupported_winding_or_invalid_contours_fail_before_creating_geometry():
    module = builder()
    ring = [(0, 0), (10, 0), (10, 10), (0, 10)]
    with pytest.raises(ValueError, match="winding-aware"):
        module._create_compound_clip_fill("mask", [ring, ring], None, None, False)
    with pytest.raises(ValueError, match="degenerate or non-finite"):
        module._create_compound_clip_fill("mask", [[(0, 0), (1, 1), (float("nan"), 0)]], None, None, True)


def test_build_page_emits_each_clip_group_once_instead_of_separate_faces(monkeypatch):
    module = builder()
    from pdf_vector_importer.pdfcadcore.primitives import PageData, Primitive
    rings = [[(0, 0), (10, 0), (10, 10), (0, 10)], [(3, 3), (7, 3), (7, 7), (3, 7)]]
    primitives = [Primitive(id=i, type="closed_loop", points=ring, closed=True, fill_color=(0, 0, 0),
                            clip_fill_group_id="clip-fill:9", clip_fill_even_odd=True) for i, ring in enumerate(rings)]
    monkeypatch.setattr(module, "_resolve_collection", lambda *a, **k: None)
    monkeypatch.setattr(module, "_get_or_create_material", lambda *a, **k: None)
    calls = []
    def compound(*args):
        calls.append(args)
        return {}
    monkeypatch.setattr(module, "_create_compound_clip_fill", compound)
    stats = module.build_page(PageData(1, 20, 20, primitives=primitives), None)
    assert len(calls) == 1 and calls[0][1] == rings
    assert stats["compound_clip_fills"] == 1 and stats["compound_clip_contours"] == 2
    assert stats["meshes"] == 0 and stats["curves"] == 1
