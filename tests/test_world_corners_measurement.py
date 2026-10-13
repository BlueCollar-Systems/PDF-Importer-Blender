"""Actual evaluated vertices, cleanup, and changing paint remain authoritative."""

import math
import struct
import sys
from types import SimpleNamespace as NS

import pytest

from pdf_vector_importer.image_paint_order import _world_corners


class Vec(tuple):
    x = property(lambda self: self[0])
    y = property(lambda self: self[1])
    z = property(lambda self: self[2])


class Matrix:
    def __init__(self, transform):
        self.transform = transform
        self.copies = self.transforms = 0

    def copy(self):
        self.copies += 1
        return self

    def __matmul__(self, point):
        self.transforms += 1
        return Vec(self.transform(point))


class Evaluated:
    def __init__(self, points, transform=lambda point: point):
        self.points = points
        self.matrix_world = Matrix(transform)
        self.graphs = []
        self.mesh_reads = self.clears = 0

    def evaluated_get(self, graph):
        self.graphs.append(graph)
        return self

    def to_mesh(self):
        self.mesh_reads += 1
        if self.points is None:
            return None
        return NS(vertices=[NS(co=Vec(point)) for point in self.points])

    def to_mesh_clear(self):
        self.clears += 1


@pytest.fixture(autouse=True)
def native_vector(monkeypatch):
    monkeypatch.setitem(sys.modules, "mathutils", NS(Vector=Vec))


def test_affine_rendered_vertices_define_bounds_not_local_box():
    points = [(-2, 3, 5), (11, -7, 2), (9, 10, -8)]
    transform = lambda p: (-p[0] + 2*p[1] + p[2], p[0] - 3*p[1], 2*p[2] + 4)
    obj = Evaluated(points, transform)
    assert set(_world_corners(obj, "graph")) == {
        Vec((x, y, z)) for x in (-23, 13) for y in (-21, 32) for z in (-12, 14)
    }
    assert obj.graphs == ["graph"]
    assert obj.mesh_reads == obj.clears == obj.matrix_world.copies == 1
    assert obj.matrix_world.transforms == len(points)


@pytest.mark.parametrize("axis", range(3))
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_nonfinite_interior_vertex_on_each_axis_is_rejected_and_cleared(axis, bad):
    points = [(i, -i, i % 7) for i in range(75)]
    invalid = list(points[51])
    invalid[axis] = bad
    points[51] = invalid
    obj = Evaluated(points)
    with pytest.raises(ValueError, match="nonfinite coordinates"):
        _world_corners(obj, None)
    assert obj.mesh_reads == obj.clears == 1
    assert obj.matrix_world.transforms == 52


@pytest.mark.parametrize("points", [[], None])
def test_empty_or_unavailable_evaluated_mesh_always_clears(points):
    obj = Evaluated(points)
    if points is None:
        with pytest.raises(ValueError, match="could not be evaluated"):
            _world_corners(obj, None)
    else:
        assert _world_corners(obj, None) == []
    assert obj.mesh_reads == obj.clears == 1


def test_native_transform_exception_clears_temporary_mesh():
    def broken(_):
        raise RuntimeError("native evaluation changed")
    obj = Evaluated([(1, 2, 3)], broken)
    with pytest.raises(RuntimeError, match="native evaluation changed"):
        _world_corners(obj, None)
    assert obj.mesh_reads == obj.clears == 1


def test_mesh_creation_exception_still_clears(monkeypatch):
    obj = Evaluated([(1, 2, 3)])
    def broken():
        raise RuntimeError("native mesh construction failed")
    monkeypatch.setattr(obj, "to_mesh", broken)
    with pytest.raises(RuntimeError, match="native mesh construction failed"):
        _world_corners(obj, None)
    assert obj.clears == 1


@pytest.mark.parametrize("first", [(-0.0, 0.0, -0.0), (0.0, -0.0, 0.0)])
def test_equal_coordinate_ties_keep_first_signed_zero_bits(first):
    obj = Evaluated([first, tuple(-value for value in first)])
    expected = tuple(struct.pack("!d", value) for value in first)
    corners = _world_corners(obj, None)
    assert len(corners) == 8
    assert all(tuple(struct.pack("!d", value) for value in corner) == expected
               for corner in corners)


def test_repeated_measurement_reads_changed_actual_geometry_and_graph():
    obj = Evaluated([(0, 0, 0), (1, 2, 3)])
    first = _world_corners(obj, "first graph")
    obj.points = [(0, 0, 5), (1, 2, 8)]
    second = _world_corners(obj, "new graph")
    assert max(point.z for point in first) == 3
    assert min(point.z for point in second) == 5
    assert max(point.z for point in second) == 8
    assert obj.graphs == ["first graph", "new graph"]
    assert obj.mesh_reads == obj.clears == obj.matrix_world.copies == 2


def test_all_large_mesh_vertices_are_transformed():
    points = [(i, -(i % 13), i % 7) for i in range(20000)]
    obj = Evaluated(points)
    result = _world_corners(obj, None)
    assert set(result) == {Vec((x, y, z)) for x in (0, 19999)
                          for y in (-12, 0) for z in (0, 6)}
    assert obj.matrix_world.transforms == len(points)
    assert obj.mesh_reads == obj.clears == 1
