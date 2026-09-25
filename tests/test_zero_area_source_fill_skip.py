"""Zero-area / collinear PDF fills must not abort the rest of the page."""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest


def _builder():
    bpy = sys.modules.setdefault("bpy", SimpleNamespace())
    bpy.types = getattr(bpy, "types", SimpleNamespace())
    for name in ("Collection", "Material", "Object"):
        if not hasattr(bpy.types, name):
            setattr(bpy.types, name, object)
    sys.modules.setdefault("bmesh", SimpleNamespace())
    return importlib.import_module("pdf_vector_importer.bl_geometry_builder")


def _page_with_zero_area_and_real_fill():
    primitives = importlib.import_module("pdf_vector_importer.pdfcadcore.primitives")
    zero = primitives.Primitive(
        id=1,
        type="closed_loop",
        points=[(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (0.0, 0.0)],
        closed=True,
        fill_color=(1.0, 0.0, 0.0),
        stroke_color=(0.0, 0.0, 0.0),
        area=0.0,
    )
    real = primitives.Primitive(
        id=2,
        type="closed_loop",
        points=[(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 0.0)],
        closed=True,
        fill_color=(0.0, 1.0, 0.0),
        stroke_color=None,
        area=25.0,
    )
    stroke = primitives.Primitive(
        id=3,
        type="line",
        points=[(0.0, 0.0), (5.0, 5.0)],
        closed=False,
        stroke_color=(0.0, 0.0, 1.0),
    )
    return primitives.PageData(
        page_number=2,
        width=100.0,
        height=100.0,
        primitives=[zero, real, stroke],
    )


@pytest.mark.parametrize(
    "message",
    [
        "Native source fill has no positive face area",
        "Native source fill mesh has no positive face area",
        "Source fill lacks a finite native face boundary",
    ],
)
def test_zero_area_source_fill_is_skipped_and_page_still_builds(monkeypatch, message):
    """Regression: page 2 of the bound-set PDF aborted on the first message."""
    module = _builder()
    faces = []
    curves = []

    def face_mesh(name, points, *_args, **_kwargs):
        # Collinear / open / zero-area fills refuse delivery at create time.
        if abs(module._polygon_area(points)) <= 1e-18:
            raise ValueError(message)
        obj = SimpleNamespace(name=name)
        faces.append(obj)
        return obj

    def poly_curve(name, *_args, **_kwargs):
        obj = SimpleNamespace(name=name)
        curves.append(obj)
        return obj

    def multi_poly(name, *_args, **_kwargs):
        obj = SimpleNamespace(name=name)
        curves.append(obj)
        return obj

    monkeypatch.setattr(module, "_create_face_mesh", face_mesh)
    monkeypatch.setattr(module, "_create_poly_curve", poly_curve)
    monkeypatch.setattr(module, "_create_multi_poly_curve", multi_poly)
    monkeypatch.setattr(module, "_resolve_collection", lambda *a, **k: object())
    monkeypatch.setattr(module, "_get_or_create_material", lambda *a, **k: object())

    stats = module.build_page(
        _page_with_zero_area_and_real_fill(),
        object(),
        config={"batch_open_curves": True},
    )

    assert stats["meshes"] == 1
    assert len(faces) == 1 and faces[0].name.endswith("_face")
    assert stats["curves"] >= 2  # zero-area outline + open stroke (+ maybe more)
    assert len(stats["geometry_delivery_issues"]) == 1
    issue = stats["geometry_delivery_issues"][0]
    assert issue["page"] == 2
    assert issue["primitive_id"] == 1
    assert issue["status"] == "skipped"
    assert issue["reason"] == "zero_area_or_degenerate_source_fill"
    assert message in issue["detail"]


def test_unrelated_source_fill_error_still_aborts_the_page(monkeypatch):
    module = _builder()

    def face_mesh(*_args, **_kwargs):
        raise ValueError("Native source fill lost its face during mesh conversion")

    monkeypatch.setattr(module, "_create_face_mesh", face_mesh)
    monkeypatch.setattr(module, "_create_poly_curve", lambda *a, **k: object())
    monkeypatch.setattr(module, "_resolve_collection", lambda *a, **k: object())
    monkeypatch.setattr(module, "_get_or_create_material", lambda *a, **k: object())

    with pytest.raises(ValueError, match="lost its face"):
        module.build_page(_page_with_zero_area_and_real_fill(), object())


def test_skipped_zero_area_fill_is_not_a_terminal_import_failure():
    engine = importlib.import_module("pdf_vector_importer.bl_import_engine")
    stats = {
        "text_source_spans": 0,
        "geometry_delivery_issues": [
            {
                "page": 2,
                "primitive_id": 1,
                "status": "skipped",
                "reason": "zero_area_or_degenerate_source_fill",
            }
        ],
    }
    assert engine._terminal_import_failures({"import_text": False}, stats, None) == []
