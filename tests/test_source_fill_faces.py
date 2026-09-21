"""Exact PDF closure must produce a real native face or a visible failure."""

import ast
import math
from pathlib import Path
from types import SimpleNamespace as NS

import pytest


SOURCE = (
    Path(__file__).resolve().parents[1] / "pdf_vector_importer/bl_geometry_builder.py"
)


class VertexList(list):
    def new(self, co):
        value = NS(co=co)
        self.append(value)
        return value

    def ensure_lookup_table(self):
        pass


@pytest.fixture
def host():
    state = NS(
        meshes=[],
        objects=[],
        linked=[],
        freed=False,
        reject_face=False,
        omit_face=False,
        zero_mesh_area=False,
    )

    def new_mesh(**_):
        mesh = NS(vertices=[], polygons=[], materials=[], update=lambda: None)
        state.meshes.append(mesh)
        return mesh

    def new_object(name, mesh):
        obj = NS(name=name, data=mesh)
        state.objects.append(obj)
        return obj

    class BMesh:
        def __init__(self):
            self.verts = VertexList()
            self.faces = NS(new=self.new_face)

        def new_face(self, vertices):
            if state.reject_face:
                raise ValueError("native rejected face")
            if len({v.co for v in vertices}) != len(vertices):
                raise ValueError("duplicate native vertex coordinate")
            xy = [v.co[:2] for v in vertices]
            area = (
                abs(
                    sum(
                        a[0] * b[1] - b[0] * a[1]
                        for a, b in zip(xy, xy[1:] + xy[:1], strict=True)
                    )
                )
                * 0.5
            )
            self.area = area
            return NS(calc_area=lambda: area)

        def to_mesh(self, mesh):
            mesh.vertices = list(self.verts)
            mesh.polygons = (
                []
                if state.omit_face
                else [NS(area=0 if state.zero_mesh_area else self.area)]
            )

        def free(self):
            state.freed = True

    bpy = NS(
        types=NS(Object=object, Collection=object, Material=object),
        data=NS(
            meshes=NS(new=new_mesh, remove=state.meshes.remove),
            objects=NS(
                new=new_object, remove=lambda obj, **_: state.objects.remove(obj)
            ),
        ),
    )
    function = next(
        node
        for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef) and node.name == "_create_face_mesh"
    )
    env = {"bpy": bpy, "bmesh": NS(new=BMesh), "math": math, "MM_TO_M": 0.001}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), env
    )
    state.create = lambda points: env["_create_face_mesh"](
        "fill", points, NS(objects=NS(link=state.linked.append)), object()
    )
    return state


def test_repeated_exact_closure_creates_three_vertex_triangle_and_preserves_source(
    host,
):
    source = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 0.0)]
    original = list(source)
    obj = host.create(source)
    assert source == original
    assert len(obj.data.vertices) == 3
    assert [v.co for v in obj.data.vertices] == [
        (0.0, 0.0, 0.0),
        (0.01, 0.0, 0.0),
        (0.01, 0.005, 0.0),
    ]
    assert obj.data.polygons[0].area == pytest.approx(0.000025)
    assert host.linked == [obj] and host.freed


def test_nearby_closing_point_is_never_snapped_or_removed(host):
    source = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 1e-10)]
    obj = host.create(source)
    assert len(obj.data.vertices) == 4
    assert obj.data.vertices[-1].co[1] == 1e-13


@pytest.mark.parametrize("failure", ["reject_face", "omit_face", "zero_mesh_area"])
def test_native_fill_failure_is_raised_and_owned_mesh_removed(host, failure):
    setattr(host, failure, True)
    with pytest.raises(ValueError):
        host.create([(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 0.0)])
    assert not host.meshes and not host.objects and not host.linked
    assert host.freed


@pytest.mark.parametrize(
    "source",
    [
        [],
        [(0.0, 0.0), (1.0, 1.0)],
        [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)],
        [(0.0, 0.0), (math.nan, 1.0), (2.0, 0.0)],
    ],
)
def test_invalid_or_zero_area_fill_cannot_be_counted_as_delivered(host, source):
    with pytest.raises(ValueError):
        host.create(source)
    assert not host.meshes and not host.objects and not host.linked
