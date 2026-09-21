"""Native API failure paths must not claim an editable cap was created."""

import sys
from types import SimpleNamespace

import pytest

from pdf_vector_importer.bl_source_capsules import _native_capsule


class Points(list):
    def __init__(self):
        super().__init__([SimpleNamespace(co=None)])

    def add(self, count):
        self.extend(SimpleNamespace(co=None) for _ in range(count))


class NativeObject(dict):
    def __init__(self, mesh):
        super().__init__()
        self.mesh = mesh
        self.cleared = False

    def evaluated_get(self, _graph):
        return self

    def to_mesh(self):
        return self.mesh

    def to_mesh_clear(self):
        self.cleared = True


@pytest.mark.parametrize("areas", [[], [0.0]])
def test_unfilled_native_capsule_is_removed_and_rejected(monkeypatch, areas):
    spline = SimpleNamespace(points=Points())
    curve = SimpleNamespace(
        splines=SimpleNamespace(new=lambda kind: spline), materials=[]
    )
    obj = NativeObject(
        SimpleNamespace(polygons=[SimpleNamespace(area=a) for a in areas])
    )
    removed_objects, removed_curves, linked = [], [], []
    bpy = SimpleNamespace(
        data=SimpleNamespace(
            curves=SimpleNamespace(new=lambda *a: curve, remove=removed_curves.append),
            objects=SimpleNamespace(
                new=lambda *a: obj,
                remove=lambda value, **kw: removed_objects.append(value),
            ),
        ),
        context=SimpleNamespace(
            view_layer=SimpleNamespace(update=lambda: None),
            evaluated_depsgraph_get=lambda: None,
        ),
    )
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    spec = dict(
        scale_mm=25.4 / 72,
        flip_y=True,
        page_height=100.0,
        primitive_id=9,
        recipe=dict(
            source_paint_order=7,
            source_capsule_proof=dict(
                capsule=dict(
                    start=(20.0, 30.0),
                    end=(20.01, 30.0),
                    width=12.0,
                    length=20.01 - 20.0,
                )
            ),
        ),
    )
    with pytest.raises(ValueError, match="no filled surface"):
        _native_capsule(
            spec, SimpleNamespace(objects=SimpleNamespace(link=linked.append)), object()
        )
    assert removed_objects == linked == [obj]
    assert removed_curves == [curve]
    assert obj.cleared
