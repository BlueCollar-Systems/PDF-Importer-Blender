"""Regression: batched PDF paths must keep page coordinates, not a origin starburst.

Blender 3.2 ignores ``spline.points[i].co = (x, y, z, w)`` on the default
first spline point (the RNA setter is a no-op). Every polyline then starts at
world origin, so Top Orthographic at the grid looks like an ink-blot starburst
instead of the sheet. Object ``location`` may still be (0,0,0); the span lives
in curve data. Frame-All must also read those spline points when ``bound_box``
is stale/collapsed.
"""
from __future__ import annotations

import importlib
from contextlib import nullcontext
import sys
import types
from pathlib import Path

import pytest


MM_TO_M = 0.001
_MISSING = object()
_ISOLATED_MODULES = (
    "bpy",
    "bmesh",
    "mathutils",
    "pdf_vector_importer.bl_geometry_builder",
    "pdf_vector_importer.bl_import_engine",
)


@pytest.fixture(autouse=True)
def _restore_replaced_modules():
    previous = {name: sys.modules.get(name, _MISSING) for name in _ISOLATED_MODULES}
    package = sys.modules.get("pdf_vector_importer")
    previous_attrs = {
        name: getattr(package, name, _MISSING)
        for name in ("bl_geometry_builder", "bl_import_engine")
        if package is not None
    }
    yield
    for name, module in previous.items():
        if module is _MISSING:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module
    if package is not None:
        for name, value in previous_attrs.items():
            if value is _MISSING:
                try:
                    delattr(package, name)
                except AttributeError:
                    pass
            else:
                setattr(package, name, value)


class _Co32(list):
    """Stand-in for Blender 3.2 ``bpy_float[4]``: item writes work, replacement does not."""


class _Point32:
    def __init__(self, *, replacement_is_noop: bool = False) -> None:
        self._co = _Co32([0.0, 0.0, 0.0, 1.0])
        self._replacement_is_noop = replacement_is_noop

    @property
    def co(self):
        return self._co

    @co.setter
    def co(self, value) -> None:
        if not self._replacement_is_noop:
            self._co[:] = [float(component) for component in value]


class _Points32(list):
    def add(self, count: int) -> None:
        for _ in range(int(count)):
            self.append(_Point32())

    def foreach_set(self, attr: str, data) -> None:
        if attr != "co":
            raise ValueError(attr)
        values = list(data)
        for index, point in enumerate(self):
            if index == 0:
                continue
            base = index * 4
            point._co[:] = [float(values[base + axis]) for axis in range(4)]


class _Spline32:
    def __init__(self, kind: str) -> None:
        self.type = kind
        self.points = _Points32([_Point32(replacement_is_noop=True)])
        self.bezier_points = []
        self.use_cyclic_u = False
        self.order_u = 4


class _Splines32:
    def __init__(self) -> None:
        self._items: list[_Spline32] = []

    def new(self, kind: str) -> _Spline32:
        spline = _Spline32(kind)
        self._items.append(spline)
        return spline

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


class _Curve32:
    def __init__(self, name: str) -> None:
        self.name = name
        self.dimensions = "3D"
        self.fill_mode = "HALF"
        self.resolution_u = 12
        self.bevel_depth = 0.0
        self.materials: list = []
        self.splines = _Splines32()


class _Object32:
    def __init__(self, name: str, data) -> None:
        self.name = name
        self.data = data
        self.type = "CURVE"
        self.location = [0.0, 0.0, 0.0]
        self.matrix_world = _Identity()
        self.bound_box = [(0.0, 0.0, 0.0)] * 8


class _Identity:
    translation = types.SimpleNamespace(x=0.0, y=0.0, z=0.0)

    def __matmul__(self, vec):
        return vec


class _LinkedObjects:
    def __init__(self) -> None:
        self.items: list = []

    def link(self, obj) -> None:
        self.items.append(obj)


class _Collection32:
    def __init__(self) -> None:
        self.objects = _LinkedObjects()


class _NewFactory:
    def __init__(self, factory) -> None:
        self._factory = factory
        self.created: list = []

    def new(self, *args, **kwargs):
        obj = self._factory(*args, **kwargs)
        self.created.append(obj)
        return obj


class _Vector:
    def __init__(self, values) -> None:
        seq = list(values) + [0.0, 0.0, 0.0]
        self.x = float(seq[0])
        self.y = float(seq[1])
        self.z = float(seq[2])

    def __add__(self, other):
        return _Vector((self.x + other.x, self.y + other.y, self.z + other.z))

    def __mul__(self, value):
        return _Vector((self.x * value, self.y * value, self.z * value))


def _install_blender32_curve_host() -> types.SimpleNamespace:
    fake_bpy = types.SimpleNamespace(
        types=types.SimpleNamespace(Collection=object, Material=object, Object=object),
        data=types.SimpleNamespace(
            curves=_NewFactory(lambda name, type="CURVE": _Curve32(name)),
            objects=_NewFactory(lambda name, data: _Object32(name, data)),
        ),
    )
    sys.modules["bpy"] = fake_bpy
    sys.modules.setdefault("bmesh", types.SimpleNamespace())
    fake_mathutils = types.ModuleType("mathutils")
    fake_mathutils.Vector = _Vector
    sys.modules["mathutils"] = fake_mathutils
    return fake_bpy


def _reload_builder():
    _install_blender32_curve_host()
    module_name = "pdf_vector_importer.bl_geometry_builder"
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def _spline_coords(obj) -> list[tuple[float, float, float]]:
    coords = []
    for spline in obj.data.splines:
        for point in spline.points:
            coords.append((float(point.co[0]), float(point.co[1]), float(point.co[2])))
    return coords


def test_batched_polylines_keep_page_span_when_tuple_co_assignment_is_noop():
    """Two distant source runs must not both grow a spoke from (0,0,0)."""
    builder = _reload_builder()
    collection = _Collection32()
    left_border = [(12.0, 12.0), (12.0, 600.0)]
    right_border = [(880.0, 12.0), (880.0, 600.0)]

    obj = builder._create_multi_poly_curve(
        "P1_batch_001",
        [left_border, right_border],
        collection,
        0.25,
        object(),
        z_offset_m=0.0001,
    )

    assert obj is not None
    assert list(obj.location) == [0.0, 0.0, 0.0]
    coords = _spline_coords(obj)
    assert len(coords) == 4
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    assert min(xs) == pytest.approx(12.0 * MM_TO_M)
    assert max(xs) == pytest.approx(880.0 * MM_TO_M)
    assert min(ys) == pytest.approx(12.0 * MM_TO_M)
    assert max(ys) == pytest.approx(600.0 * MM_TO_M)
    first_points = [
        (float(spline.points[0].co[0]), float(spline.points[0].co[1]))
        for spline in obj.data.splines
    ]
    assert first_points[0] == pytest.approx((12.0 * MM_TO_M, 12.0 * MM_TO_M))
    assert first_points[1] == pytest.approx((880.0 * MM_TO_M, 12.0 * MM_TO_M))
    origin_starts = [
        point
        for point in first_points
        if abs(point[0]) < 1e-9 and abs(point[1]) < 1e-9
    ]
    assert origin_starts == []
    origin_any = [
        coord
        for coord in coords
        if abs(coord[0]) < 1e-9 and abs(coord[1]) < 1e-9
    ]
    assert origin_any == [], origin_any


def test_no_spline_point_at_world_origin_when_source_paths_do_not():
    """Source-page ink avoids (0,0); a leftover default point draws the X."""
    builder = _reload_builder()
    collection = _Collection32()
    runs = [
        [(12.0, 12.0), (12.0, 40.0), (80.0, 40.0)],
        [(880.0, 12.0), (898.0, 12.0)],
        [(400.0, 300.0), (410.0, 310.0), (420.0, 300.0)],
    ]
    obj = builder._create_multi_poly_curve(
        "P1_batch_nonorigin",
        runs,
        collection,
        0.25,
        object(),
        z_offset_m=0.0001,
    )
    origin_xy = []
    for spline in obj.data.splines:
        assert len(spline.points) >= 2
        for point in spline.points:
            x, y = float(point.co[0]), float(point.co[1])
            if abs(x) < 1e-9 and abs(y) < 1e-9:
                origin_xy.append((x, y, float(point.co[2])))
    assert origin_xy == []


def test_single_poly_curve_writes_the_source_start_not_the_default_origin():
    builder = _reload_builder()
    collection = _Collection32()
    obj = builder._create_poly_curve(
        "P1_line_1",
        [(250.0, 400.0), (250.0, 500.0)],
        False,
        collection,
        0.25,
        object(),
        z_offset_m=0.0,
    )
    first = next(iter(obj.data.splines)).points[0].co
    assert (float(first[0]), float(first[1])) == pytest.approx(
        (250.0 * MM_TO_M, 400.0 * MM_TO_M)
    )


def test_nurbs_circle_writes_all_points_without_a_default_origin() -> None:
    builder = _reload_builder()
    collection = _Collection32()
    obj = builder._create_nurbs_circle(
        "P1_circle_1",
        (250.0, 400.0),
        50.0,
        collection,
        0.25,
        object(),
        z_offset_m=0.0001,
    )

    spline = next(iter(obj.data.splines))
    coords = _spline_coords(obj)
    assert len(coords) == 8
    assert coords[0] == pytest.approx((0.3, 0.4, 0.0001))
    assert all(abs(x) > 1e-9 or abs(y) > 1e-9 for x, y, _z in coords)
    assert spline.use_cyclic_u is True
    assert spline.order_u == 3


def test_curve_bound_points_are_streamed_instead_of_materialized() -> None:
    _reload_builder()
    engine_name = "pdf_vector_importer.bl_import_engine"
    sys.modules.pop(engine_name, None)
    engine = importlib.import_module(engine_name)
    curve_data = types.SimpleNamespace(
        splines=[
            types.SimpleNamespace(
                points=[types.SimpleNamespace(co=(1.0, 2.0, 3.0, 1.0))],
                bezier_points=[],
            )
        ]
    )

    points = engine._curve_spline_local_points(curve_data)
    assert iter(points) is points
    assert list(points) == [(1.0, 2.0, 3.0)]


def test_world_bounds_include_bezier_handles() -> None:
    _reload_builder()
    engine_name = "pdf_vector_importer.bl_import_engine"
    sys.modules.pop(engine_name, None)
    engine = importlib.import_module(engine_name)
    bezier_points = [
        types.SimpleNamespace(
            co=(0.0, 0.0, 0.0),
            handle_left=(-1.0, 10.0, 0.0),
            handle_right=(1.0, 10.0, 0.0),
        ),
        types.SimpleNamespace(
            co=(2.0, 0.0, 0.0),
            handle_left=(1.0, 10.0, 0.0),
            handle_right=(3.0, 10.0, 0.0),
        ),
    ]
    curve_data = types.SimpleNamespace(
        splines=[types.SimpleNamespace(points=[], bezier_points=bezier_points)]
    )
    obj = _Object32("Bezier", curve_data)

    min_v, max_v = engine._world_bounds_for_objects([obj])
    assert min_v is not None and max_v is not None
    assert (min_v.x, min_v.y) == pytest.approx((-1.0, 0.0))
    assert (max_v.x, max_v.y) == pytest.approx((3.0, 10.0))


def test_world_bounds_use_curve_spline_points_when_bound_box_is_collapsed():
    builder = _reload_builder()
    engine_name = "pdf_vector_importer.bl_import_engine"
    sys.modules.pop(engine_name, None)
    engine = importlib.import_module(engine_name)
    collection = _Collection32()
    obj = builder._create_multi_poly_curve(
        "P1_batch_002",
        [[(12.0, 12.0), (898.0, 12.0)], [(12.0, 602.0), (898.0, 602.0)]],
        collection,
        None,
        object(),
    )
    obj.bound_box = [(0.0, 0.0, 0.0)] * 8

    min_v, max_v = engine._world_bounds_for_objects([obj])
    assert min_v is not None and max_v is not None
    assert max_v.x - min_v.x == pytest.approx(886.0 * MM_TO_M, abs=1e-9)
    assert max_v.y - min_v.y == pytest.approx(590.0 * MM_TO_M, abs=1e-9)
    assert abs(min_v.x) > 0.001
    assert abs(min_v.y) > 0.001


def test_paper_space_curves_are_2d_without_tubes():
    builder = _reload_builder()
    collection = _Collection32()
    obj = builder._create_poly_curve(
        "P1_line_2d",
        [(250.0, 400.0), (250.0, 500.0)],
        False,
        collection,
        0.25,
        object(),
        z_offset_m=0.0,
        use_tubes=False,
    )
    assert obj.data.dimensions == "2D"
    assert obj.data.fill_mode == "NONE"
    assert obj.data.bevel_depth == 0.0


def test_orthogonal_z_leak_points_land_on_sheet_xy():
    builder = _reload_builder()
    collection = _Collection32()
    obj = builder._create_multi_poly_curve(
        "P1_fence",
        [[(12.0, 0.0, 12.0), (12.0, 0.0, 600.0)], [(880.0, 0.0, 12.0), (880.0, 0.0, 600.0)]],
        collection,
        0.25,
        object(),
    )
    coords = _spline_coords(obj)
    ys = [c[1] for c in coords]
    assert min(ys) == pytest.approx(12.0 * MM_TO_M)
    assert max(ys) == pytest.approx(600.0 * MM_TO_M)


def test_off_sheet_stroke_does_not_set_the_view():
    _reload_builder()
    sys.modules.pop("pdf_vector_importer.bl_import_engine", None)
    engine = importlib.import_module("pdf_vector_importer.bl_import_engine")

    class _V:
        def __init__(self, x, y, z) -> None:
            self.x = float(x)
            self.y = float(y)
            self.z = float(z)

    # Sheet is 0.216 x 0.279 m. A stroke to x=0.53 must not become the frame.
    framed_min, framed_max = engine._prefer_sheet_frame(
        _V(-0.268, 0.0, 0.0),
        _V(0.529, 0.279, 0.001),
        _V(0.0, 0.0, -0.0001),
        _V(0.216, 0.279, -0.0001),
    )
    assert framed_min.x == pytest.approx(0.0)
    assert framed_max.x == pytest.approx(0.216)
    assert framed_max.y == pytest.approx(0.279)


def test_sheet_view_radius_ignores_z_fence():
    _reload_builder()
    sys.modules.pop("pdf_vector_importer.bl_import_engine", None)
    engine = importlib.import_module("pdf_vector_importer.bl_import_engine")

    class _V:
        def __init__(self, x, y, z) -> None:
            self.x = float(x)
            self.y = float(y)
            self.z = float(z)

    radius = engine._sheet_view_radius(_V(0.0, 0.0, 0.0), _V(1.22, 0.91, 80.0))
    assert radius == pytest.approx(1.22)


def test_focus_path_does_not_call_view_selected_when_spline_bounds_exist():
    source = (
        Path(__file__).resolve().parents[1]
        / "pdf_vector_importer"
        / "bl_import_engine.py"
    ).read_text(encoding="utf-8")
    assert "if min_v is None or max_v is None:" in source
    assert "bpy.ops.view3d.view_selected" in source
    assert "for start in range(0, len(runs), max_runs)" in (
        Path(__file__).resolve().parents[1]
        / "pdf_vector_importer"
        / "bl_geometry_builder.py"
    ).read_text(encoding="utf-8")
    builder = _reload_builder()
    assert builder._MAX_OPEN_CURVE_RUNS_PER_OBJECT == 400


@pytest.mark.parametrize("projection_xy", [(2.5, 1.0), (1.0, 2.5), (4.0, 0.4)])
@pytest.mark.parametrize("sheet_xy", [(1.2, 0.9), (0.2, 4.0), (8.0, 0.2)])
def test_final_orthographic_view_contains_sheet_for_each_viewport(projection_xy, sheet_xy):
    _reload_builder()
    sys.modules.pop("pdf_vector_importer.bl_import_engine", None)
    engine = importlib.import_module("pdf_vector_importer.bl_import_engine")

    class Region:
        view_distance = 10.0
        view_rotation = (0.7, 0.7, 0.0, 0.0)
        view_perspective = "PERSP"

        def update(self):
            # Native orthographic projection scales inversely with view_distance.
            # Different lens/aspect combinations affect X and Y independently.
            self.window_matrix = (
                (projection_xy[0] / self.view_distance, 0, 0, 0),
                (0, projection_xy[1] / self.view_distance, 0, 0),
                (0, 0, -1, 0), (0, 0, 0, 1),
            )

    region = Region()
    lower = _Vector((12.0, -30.0, 0.0))
    upper = _Vector((12.0 + sheet_xy[0], -30.0 + sheet_xy[1], 0.1))
    engine._frame_sheet_view(region, lower, upper)
    region.update()
    assert region.view_rotation == (1.0, 0.0, 0.0, 0.0)
    assert region.view_perspective == "ORTHO"
    assert region.view_location.x == pytest.approx(12.0 + sheet_xy[0] / 2)
    assert region.view_location.y == pytest.approx(-30.0 + sheet_xy[1] / 2)
    projected = [sheet_xy[i] * abs(region.window_matrix[i][i]) / 2 for i in (0, 1)]
    assert max(projected) == pytest.approx(1 / 1.10)
    assert all(value < 1 for value in projected)


@pytest.mark.parametrize("material_preview", [False, True])
def test_focus_finishes_all_batch_viewports_without_animated_axis_operator(monkeypatch, material_preview):
    _reload_builder()
    sys.modules.pop("pdf_vector_importer.bl_import_engine", None)
    engine = importlib.import_module("pdf_vector_importer.bl_import_engine")
    selected = []

    class Drawing(dict):
        type = "CURVE"
        name = "ImportedBatch"

        def select_set(self, value):
            selected.append(value)

        def hide_set(self, _value):
            pass

    class ObjectList(list):
        active = None

    drawing = Drawing()
    collection = types.SimpleNamespace(all_objects=[drawing])
    viewport_regions = [object(), object()]
    areas = [types.SimpleNamespace(
        type="VIEW_3D", regions=[types.SimpleNamespace(type="WINDOW")],
        spaces=types.SimpleNamespace(active=types.SimpleNamespace(
            region_3d=region, local_view=None, clip_start=0.01, clip_end=1000,
            shading=types.SimpleNamespace(
                type="SOLID", light="STUDIO", color_type="RANDOM",
                show_shadows=True, show_cavity=True, show_object_outline=True,
                show_specular_highlight=True,
            ),
        )),
    ) for region in viewport_regions]
    engine.bpy.context = types.SimpleNamespace(
        view_layer=types.SimpleNamespace(objects=ObjectList([drawing])),
        window_manager=types.SimpleNamespace(windows=[types.SimpleNamespace(
            screen=types.SimpleNamespace(areas=areas),
        )]),
        scene=object(), temp_override=lambda **_kwargs: nullcontext(),
    )
    calls = []
    monkeypatch.setattr(engine, "_unhide_collection_tree", lambda _root: None)
    monkeypatch.setattr(engine, "_world_bounds_for_objects", lambda _objects: (
        _Vector((0, 0, 0)), _Vector((2, 10, 0)),
    ))
    monkeypatch.setattr(engine, "_frame_sheet_view", lambda region, _lo, _hi: calls.append(region) or True)
    # No ops API is provided: framing must finish synchronously without view_axis.
    assert engine._focus_view_on_import(collection, prefer_material_preview=material_preview) is True
    assert calls == viewport_regions
    assert selected[-1] is False
    for area in areas:
        shading = area.spaces.active.shading
        if material_preview:
            assert shading.type == "MATERIAL"
            assert shading.color_type == "TEXTURE"
            assert shading.light == "STUDIO"
        else:
            assert shading.type == "SOLID"
            assert shading.light == "FLAT"
            assert shading.color_type == "MATERIAL"
            assert not any(getattr(shading, name) for name in (
                "show_shadows", "show_cavity", "show_object_outline", "show_specular_highlight"))
