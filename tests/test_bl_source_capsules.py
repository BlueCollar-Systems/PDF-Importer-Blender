"""Source identity and ownership gates for the Blender cap consumer."""

from copy import deepcopy
import hashlib
import sys
from types import SimpleNamespace

import pymupdf
import pytest

from pdf_vector_importer import bl_source_capsules as module


@pytest.fixture
def prepared(monkeypatch):
    capsule = dict(
        start=(20.0, 30.0), end=(20.01, 30.0), width=12.0, length=20.01 - 20.0
    )
    recipe = dict(
        source_paint_order=7,
        source_sha256="a" * 64,
        page=1,
        device_bounds=[100, 100, 200, 200],
        source_capsule_proof=dict(capsule=capsule),
    )
    page = SimpleNamespace(
        rotation=0,
        number=0,
        rect=pymupdf.Rect(0, 0, 100, 100),
        get_drawings=lambda **kw: [dict(seqno=7)],
        get_svg_image=lambda **kw: "<svg/>",
    )
    monkeypatch.setattr(
        module,
        "unclipped_capsules",
        lambda *a: {7: {"source_blend_modes": ["Multiply"]}},
    )
    monkeypatch.setattr(module, "bind_similarity_strokes", lambda *a: {7: {}})
    monkeypatch.setattr(module, "qualify_recipes", lambda *a, **kw: [deepcopy(recipe)])
    scale = 25.4 / 72
    points = [
        module.model_point(p, 100, scale, True)
        for p in (capsule["start"], capsule["end"])
    ]
    primitive = SimpleNamespace(id=9, type="line", source_draw_order=7, points=points)
    return page, SimpleNamespace(primitives=[primitive]), recipe


def prepare(page, data, **kw):
    return module.prepare_capsules(
        page, data, source_sha256="a" * 64, page_number=1, **kw
    )


def test_exact_canonical_centerline_bound_without_point_cleanup(prepared):
    page, data, _ = prepared
    before = deepcopy(data.primitives[0].points)
    specs, unresolved = prepare(page, data)
    assert not unresolved
    assert specs[0]["primitive_id"] == 9
    assert specs[0]["points_mm"] == before == data.primitives[0].points
    assert 0 < abs(before[1][0] - before[0][0]) < 0.01


@pytest.mark.parametrize("change", ["missing", "duplicate", "moved", "collapsed"])
def test_missing_or_changed_canonical_line_cannot_claim_footprint(prepared, change):
    page, data, _ = prepared
    if change == "missing":
        data.primitives = []
    elif change == "duplicate":
        data.primitives *= 2
    elif change == "moved":
        data.primitives[0].points[0] = (0.0, 0.0)
    else:
        data.primitives[0].points = data.primitives[0].points[:1]
    with pytest.raises(ValueError, match="unique unchanged"):
        prepare(page, data)


def test_page_identity_and_whole_import_budget(prepared):
    page, data, _ = prepared
    specs, unresolved = prepare(page, data, used_pixels=module.MAX_IMPORT_PIXELS)
    assert (
        not specs and unresolved[0]["reason"] == "whole_import_composite_pixel_budget"
    )
    page.number = 1
    with pytest.raises(ValueError, match="page identity"):
        prepare(page, data)


def test_original_file_changed_during_source_render_is_terminal(
    prepared, monkeypatch, tmp_path
):
    page, data, _ = prepared
    specs, _ = prepare(page, data)
    path = tmp_path / "source.pdf"
    path.write_bytes(b"original")
    specs[0]["recipe"]["source_sha256"] = hashlib.sha256(b"original").hexdigest()
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace())
    from pdf_vector_importer import image_paint_order

    monkeypatch.setattr(image_paint_order, "_verify_native_stroke", lambda *a: None)
    center = SimpleNamespace(type="CURVE", data=SimpleNamespace(materials=[object()]))

    def render(*_args):
        path.write_bytes(b"changed")
        return []

    monkeypatch.setattr(module, "render_recipes", render)
    with pytest.raises(ValueError, match="changed during render"):
        module.apply_capsules(
            page,
            specs,
            None,
            {"_image_order_stroke_objects": {9: [center]}},
            source_path=path,
            image_dir=tmp_path,
            create_image_plane=None,
            image_cache=None,
            fitz=pymupdf,
        )


def test_missing_native_centerline_cannot_be_hidden_by_display_patch(
    prepared, monkeypatch, tmp_path
):
    page, data, _ = prepared
    specs, _ = prepare(page, data)
    path = tmp_path / "source.pdf"
    path.write_bytes(b"original")
    specs[0]["recipe"]["source_sha256"] = hashlib.sha256(b"original").hexdigest()
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace())
    with pytest.raises(ValueError, match="unique native ownership"):
        module.apply_capsules(
            page,
            specs,
            None,
            {},
            source_path=path,
            image_dir=tmp_path,
            create_image_plane=None,
            image_cache=None,
            fitz=pymupdf,
        )
