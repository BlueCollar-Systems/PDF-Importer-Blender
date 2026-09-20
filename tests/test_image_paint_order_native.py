"""Host-contract regressions for source-bound opaque image display ordering."""

import importlib
import sys
from types import SimpleNamespace as NS

import pymupdf as fitz
import pytest

from pdf_vector_importer import image_paint_order as order
from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page


class Vec(list):
    x = property(lambda self: self[0])
    y = property(lambda self: self[1])
    z = property(lambda self: self[2], lambda self, value: self.__setitem__(2, value))


class Translation:
    def __init__(self, obj):
        self.obj = obj

    def __matmul__(self, point):
        return Vec(a + b for a, b in zip(point, self.obj.location, strict=True))

    def copy(self):
        return Translation(NS(location=tuple(self.obj.location)))


class Object(dict):
    def __init__(self, name, kind, data):
        super().__init__()
        self.name, self.type, self.data = name, kind, data
        self.location = Vec((0.0, 0.0, 0.0))
        self.rotation_euler, self.scale = (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
        self.parent = None
        self.modifiers, self.constraints = [], []
        self.hide_render = False
        self.matrix_world = Translation(self)

    def evaluated_get(self, _):
        return self

    def to_mesh(self):
        return NS(vertices=[NS(co=Vec(p)) for p in self.bound_box])

    def to_mesh_clear(self):
        self.mesh_cleared = True

    @property
    def bound_box(self):
        points = (
            [v.co for v in self.data.vertices]
            if self.type == "MESH"
            else [p.co[:3] for s in self.data.splines for p in s.points]
        )
        radius = getattr(self.data, "bevel_depth", 0.0)
        return [
            (x, y, z)
            for x in (
                min(p[0] for p in points) - radius,
                max(p[0] for p in points) + radius,
            )
            for y in (
                min(p[1] for p in points) - radius,
                max(p[1] for p in points) + radius,
            )
            for z in (
                min(p[2] for p in points) - radius,
                max(p[2] for p in points) + radius,
            )
        ]


def material(image):
    socket = lambda value=None: NS(default_value=value)
    tex = NS(
        type="TEX_IMAGE", image=image, outputs={"Color": socket(), "Alpha": socket()}
    )
    shader = NS(
        type="BSDF_PRINCIPLED",
        inputs={
            "Specular IOR Level": socket(0),
            "Emission Color": socket(),
            "Alpha": socket(1),
            "Base Color": socket((0, 0, 0, 1)),
            "Emission Strength": socket(1),
        },
        outputs={"BSDF": socket()},
    )
    output = NS(type="OUTPUT_MATERIAL", inputs={"Surface": socket()})
    pairs = [
        (tex.outputs["Color"], shader.inputs["Emission Color"]),
        (tex.outputs["Alpha"], shader.inputs["Alpha"]),
        (shader.outputs["BSDF"], output.inputs["Surface"]),
    ]
    return NS(
        use_nodes=True,
        node_tree=NS(
            nodes=[tex, shader, output],
            links=[NS(from_socket=a, to_socket=b) for a, b in pairs],
        ),
    )


@pytest.fixture
def native_case(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "mathutils", NS(Vector=Vec))
    pixels = fitz.Pixmap(fitz.csRGB, (0, 0, 4, 3), False)
    pixels.clear_with(255)
    payload = pixels.tobytes("png")
    path = tmp_path / "image.png"
    path.write_bytes(payload)
    with fitz.open() as doc:
        page = doc.new_page(width=200, height=150)
        page.draw_line((0, 40), (180, 40))
        page.insert_image((20, 20, 120, 95), stream=payload)
        page.draw_line((15, 40), (130, 40), color=(1, 0, 0), width=2)
        page.insert_text((35, 50), "DATE", fontsize=12)
        plan = order.plan_opaque_images(page, extract_page(page, 1, detect_arcs=False))[
            0
        ]
    quad = plan["model_quad_mm"]
    mesh = NS(
        vertices=[NS(co=Vec((x * 0.001, y * 0.001, 0.0))) for x, y in quad],
        edges=[NS(vertices=(i, (i + 1) % 4)) for i in range(4)],
        polygons=[NS(vertices=(0, 1, 2, 3), loop_indices=(0, 1, 2, 3))],
        loops=[NS(vertex_index=i) for i in range(4)],
        uv_layers=NS(
            active=NS(
                data=[
                    NS(uv=uv) for uv in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
                ]
            )
        ),
    )
    image = NS(packed_file=NS(data=payload), colorspace_settings=NS(name="Non-Color"))
    mesh.materials = [material(image)]
    plane = Object("Source image", "MESH", mesh)
    plane["pdf_image_datablock"] = "pixels"
    placement = {
        "xref": plan["source_xref"],
        "page_number": 1,
        "source_kind": "xobject",
        "quad_mm": quad,
        "path": str(path),
    }
    seq, primitive_id = next(iter(plan["later_strokes"].items()))
    points = plan["later_stroke_specs"][seq]["points_mm"]
    stroke = Object(
        "Later red line",
        "CURVE",
        NS(
            bevel_depth=0.0003527778,
            splines=[
                NS(
                    type="POLY",
                    points=[
                        NS(co=Vec((x * 0.001, y * 0.001, 0.0001, 1.0)))
                        for x, y in points
                    ],
                    bezier_points=[],
                    use_cyclic_u=False,
                )
            ],
        ),
    )
    stroke["pdf_image_order_primitive_id"] = primitive_id
    # A separate earlier object supplies actual tall geometry in front of the image.
    older = Object(
        "Earlier 3D",
        "CURVE",
        NS(
            bevel_depth=0.0,
            splines=[
                NS(
                    type="POLY",
                    points=[
                        NS(co=Vec((0.03, 0.02, 0.01, 1.0))),
                        NS(co=Vec((0.04, 0.02, 0.01, 1.0))),
                    ],
                    bezier_points=[],
                    use_cyclic_u=False,
                )
            ],
        ),
    )
    item = next(iter(plan["later_text_items"].values()))
    crop_values = {
        "pdf_raster_source_item_id": f"page:1:text:{item}",
        "pdf_raster_final_page_composite": True,
    }
    x0, y0, x1, y1 = plan['later_text_crop_bounds_mm'][item]
    crop = Object('Later source pixels', 'MESH', NS(
        vertices=[NS(co=Vec((x*.001,y*.001,0.))) for x,y in ((x0,y0),(x1,y0),(x1,y1),(x0,y1))],
        edges=[],polygons=[],uv_layers=NS(active=None)))
    crop.update(crop_values)
    collection = NS(all_objects=[plane, stroke, older, crop])
    bpy = NS(
        context=NS(
            view_layer=NS(update=lambda: None), evaluated_depsgraph_get=lambda: None
        ),
        data=NS(images={"pixels": image}),
    )
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    return NS(
        plan=plan,
        plane=plane,
        stroke=stroke,
        older=older,
        crop=crop,
        image=image,
        mesh=mesh,
        placement=placement,
        collection=collection,
        config={"_image_order_stroke_objects": {primitive_id: [stroke]}},
        bpy=bpy,
    )


def apply(case):
    return order.apply_opaque_image_order(
        [case.plan], case.collection, [(case.plane, case.placement)], case.config
    )[0]


def test_source_image_and_later_stroke_clear_earlier_3d_without_geometry_changes(
    native_case,
):
    case = native_case
    old_image, old_stroke = (
        order._local_geometry(case.plane),
        order._local_geometry(case.stroke),
    )
    assert apply(case)["status"] == "applied"
    assert case.plane.location.z > 0.01
    assert (
        min(p.z for p in order._world_corners(case.stroke, None))
        > case.plane.location.z
    )
    assert order._local_geometry(case.plane) == old_image
    assert order._local_geometry(case.stroke) == old_stroke
    assert case.plane.location[:2] == case.stroke.location[:2] == [0.0, 0.0]
    assert case.older.location == [0.0, 0.0, 0.0]


def test_distant_tall_geometry_does_not_lift_source_image(native_case):
    case = native_case
    distant = Object('distant-title', 'MESH', NS(vertices=[NS(co=Vec((2., 2., .100)))],
        edges=[], polygons=[], uv_layers=NS(active=None)))
    case.collection.all_objects.append(distant)
    assert apply(case)['status'] == 'applied'
    assert case.plane.location.z == pytest.approx(.01005)


def test_image_future_owned_stroke_is_not_counted_as_earlier_paint(native_case):
    case = native_case
    case.stroke.location.z = .250
    assert apply(case)['status'] == 'applied'
    assert case.plane.location.z == pytest.approx(.01005)
    assert max(p.z for p in order._world_corners(case.stroke, None)) < .012


def test_default_radius_cached_bounds_cannot_reject_source_stroke(native_case):
    case = native_case
    stroke = case.stroke
    actual = stroke.to_mesh()
    cleared = []
    evaluated = NS(
        bound_box=[(-1, -1, -1), (1, 1, 1)],
        matrix_world=stroke.matrix_world,
        to_mesh=lambda: actual,
        to_mesh_clear=lambda: cleared.append(True),
    )
    stroke.evaluated_get = lambda _: evaluated
    assert apply(case)["status"] == "applied"
    assert 0.01 < case.plane.location.z < 0.02
    assert cleared


def test_empty_later_stroke_cannot_claim_source_paint(native_case):
    native_case.stroke.to_mesh = lambda: NS(vertices=[])
    assert apply(native_case)["reason"] == "native_stroke_has_no_evaluated_geometry"
    assert native_case.plane.location.z == native_case.stroke.location.z == 0
    assert native_case.stroke.mesh_cleared


@pytest.mark.parametrize("kind", ["FONT", "CURVE", "MESH"])
def test_bounds_use_actual_evaluated_mesh_and_matching_transform(monkeypatch, kind):
    monkeypatch.setitem(sys.modules, "mathutils", NS(Vector=Vec))
    cleared = []
    evaluated = NS(
        bound_box=[(-1, -1, -1), (1, 1, 1)],
        matrix_world=Translation(NS(location=(1., 2., .003))),
        to_mesh=lambda: NS(vertices=[NS(co=Vec((0, 0, -.0002))), NS(co=Vec((.04, .02, .0004)))]),
        to_mesh_clear=lambda: cleared.append(True),
    )
    obj = NS(type=kind, matrix_world=Translation(NS(location=(9, 9, 9))), evaluated_get=lambda _: evaluated)
    points = order._world_corners(obj, None)
    assert min(p.z for p in points) == pytest.approx(.0028)
    assert max(p.z for p in points) == pytest.approx(.0034)
    assert max(p.x for p in points) == pytest.approx(1.04)
    assert cleared == [True]


@pytest.mark.parametrize("coordinates", [[], [(0., 0., 0.)], [(float("nan"), 0., 0.)], None])
def test_empty_and_failed_mesh_bounds_release_temporary_geometry(monkeypatch, coordinates):
    monkeypatch.setitem(sys.modules, "mathutils", NS(Vector=Vec))
    cleared = []
    mesh = None if coordinates is None else NS(vertices=[NS(co=Vec(p)) for p in coordinates])
    evaluated = NS(matrix_world=Translation(NS(location=(0., 0., 0.))), to_mesh=lambda: mesh, to_mesh_clear=lambda: cleared.append(True))
    obj = NS(evaluated_get=lambda _: evaluated)
    if coordinates is None or (coordinates and coordinates[0][0] != coordinates[0][0]):
        with pytest.raises(ValueError):
            order._world_corners(obj, None)
    else:
        points = order._world_corners(obj, None)
        assert len(points) == (8 if coordinates else 0)
    assert cleared == [True]


def test_failed_mesh_conversion_still_releases_native_temporary_geometry(monkeypatch):
    monkeypatch.setitem(sys.modules, "mathutils", NS(Vector=Vec))
    cleared = []
    error = RuntimeError("Native conversion failed")
    def fail():
        raise error
    evaluated = NS(matrix_world=Translation(NS(location=(0., 0., 0.))), to_mesh=fail, to_mesh_clear=lambda: cleared.append(True))
    with pytest.raises(RuntimeError) as caught:
        order._world_corners(NS(evaluated_get=lambda _: evaluated), None)
    assert caught.value is error and cleared == [True]


@pytest.mark.parametrize(
    "mutation",
    ["pixels", "xy", "uv", "material", "material-link", "opacity", "extra-face"],
)
def test_native_image_tampering_fails_before_any_depth_change(native_case, mutation):
    case = native_case
    if mutation == "pixels":
        case.image.packed_file.data = b"not source pixels"
    elif mutation == "xy":
        case.mesh.vertices[0].co[0] += 0.001
    elif mutation == "uv":
        case.mesh.uv_layers.active.data[0].uv = (1.0, 0.0)
    elif mutation == "material":
        case.mesh.materials[0].node_tree.nodes[0].image = None
    elif mutation == "material-link":
        case.mesh.materials[0].node_tree.links.pop()
    elif mutation == "opacity":
        case.image.colorspace_settings.name = "sRGB"
    else:
        case.mesh.polygons.append(case.mesh.polygons[0])
    with pytest.raises((ValueError, RuntimeError)):
        apply(case)
    assert case.plane.location.z == case.stroke.location.z == 0


def test_missing_later_final_pixels_retains_existing_objects(native_case):
    case = native_case
    case.collection.all_objects.remove(case.crop)
    row = apply(case)
    assert row["status"] == "unqualified"
    assert row["reason"] == "later_text_is_not_a_verified_final_page_pixel_crop"
    assert case.plane.location.z == case.stroke.location.z == 0


def test_actual_later_crop_outside_certified_dependency_prevents_every_move(native_case):
    case = native_case
    case.crop.data.vertices[0].co[0] += 1.
    row = apply(case)
    assert row['status'] == 'unqualified'
    assert row['reason'] == 'owned_native_paint_exceeds_source_dependency_bounds'
    assert case.plane.location.z == case.stroke.location.z == 0


def test_later_stroke_cannot_hide_unaccounted_source_paint_outside_bounds(native_case):
    case = native_case
    case.stroke.data.bevel_depth = 0.1
    assert (
        apply(case)["reason"]
        == "native_stroke_footprint_exceeds_source_dependency_bounds"
    )
    assert case.plane.location.z == case.stroke.location.z == 0


@pytest.mark.parametrize("mutation", ["point", "extra-spline", "ownership"])
def test_later_stroke_native_ownership_is_verified_before_movement(
    native_case, mutation
):
    case = native_case
    if mutation == "point":
        case.stroke.data.splines[0].points[0].co[0] += 0.001
    elif mutation == "extra-spline":
        case.stroke.data.splines.append(case.stroke.data.splines[0])
    else:
        case.config["_image_order_stroke_objects"].clear()
    with pytest.raises(ValueError):
        apply(case)
    assert case.plane.location.z == case.stroke.location.z == 0


def test_source_qualified_stroke_never_batches_with_earlier_same_style(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "bpy",
        NS(types=NS(Collection=object, Material=object, Object=object)),
    )
    monkeypatch.setitem(sys.modules, "bmesh", NS())
    builder = importlib.import_module("pdf_vector_importer.bl_geometry_builder")
    from pdf_vector_importer.pdfcadcore.primitives import PageData, Primitive

    page = PageData(
        page_number=1,
        width=100,
        height=100,
        primitives=[
            Primitive(id=1, type="line", points=[(0.0, 0.0), (10.0, 0.0)]),
            Primitive(id=2, type="line", points=[(0.0, 1.0), (10.0, 1.0)]),
            Primitive(id=3, type="line", points=[(0.0, 2.0), (10.0, 2.0)]),
        ],
    )
    target, mat, built = object(), object(), []
    monkeypatch.setattr(builder, "_resolve_collection", lambda *a, **kw: target)
    monkeypatch.setattr(builder, "_get_or_create_material", lambda *a, **kw: mat)

    def create(name, runs, *a, **kw):
        obj = {"name": name, "runs": runs}
        built.append(obj)
        return obj

    monkeypatch.setattr(builder, "_create_multi_poly_curve", create)
    config = {"_image_order_isolated_stroke_ids": {2}}
    builder.build_page(page, target, config)
    owned = config["_image_order_stroke_objects"][2]
    assert len(owned) == 1 and owned[0]["runs"] == [[(0.0, 1.0), (10.0, 1.0)]]
    batches = [obj for obj in built if obj is not owned[0]]
    assert len(batches) == 1
    assert batches[0]["runs"] == [[(0.0, 0.0), (10.0, 0.0)], [(0.0, 2.0), (10.0, 2.0)]]
