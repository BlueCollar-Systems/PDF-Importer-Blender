"""Editable exact cap boundaries plus bounded original non-text display paint.

Only source-certified Multiply capsules with text/image-free final pixel
coverage qualify. Original source centerline objects stay intact. Packed display
patches can be hidden to expose the editable rational quadratic boundaries.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .capsule_nurbs import capsule_controls, native_float_close
from .image_paint_order import _close
from .nontext_composite import (
    qualify_recipes,
    pixel_count,
    render_recipes,
    multiply_modes,
)
from .stroke_footprint import bind_similarity_strokes, unclipped_capsules

MAX_IMPORT_PIXELS = 64_000_000


def model_point(point, height, scale, flip_y):
    return (point[0] * scale, (height - point[1] if flip_y else point[1]) * scale)


def prepare_capsules(
    page,
    page_data,
    *,
    source_sha256,
    page_number,
    user_scale=1.0,
    flip_y=True,
    used_pixels=0,
):
    """Bind recipes to one exact normalized source centerline each, before build."""
    if int(page.rotation):
        return [], []
    rows = page.get_drawings(extended=True)
    if not rows:
        return [], []
    proofs = unclipped_capsules(rows, tuple(page.rect))
    proofs = {
        seq: proof
        for seq, proof in proofs.items()
        if multiply_modes(proof.get("source_blend_modes"))
    }
    if not proofs:
        return [], []
    proofs = bind_similarity_strokes(
        proofs, rows, page.get_svg_image(text_as_path=True)
    )
    recipes = qualify_recipes(
        page, proofs, source_sha256=source_sha256, page_number=page_number
    )
    accepted = {r["source_paint_order"] for r in recipes}
    unresolved = [
        {
            "page": page_number,
            "source_paint_order": seq,
            "status": "unqualified",
            "reason": "capsule_blend_clip_or_nontext_composite_not_proven",
        }
        for seq in proofs
        if seq not in accepted
    ]
    if sum(pixel_count(r) for r in recipes) + used_pixels > MAX_IMPORT_PIXELS:
        return [], unresolved + [
            dict(
                page=page_number,
                source_paint_order=r["source_paint_order"],
                status="unqualified",
                reason="whole_import_composite_pixel_budget",
            )
            for r in recipes
        ]
    scale = 25.4 / 72 * float(user_scale)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("Capsule requires a positive finite source scale")
    if page.number + 1 != page_number:
        raise ValueError("Capsule source page identity changed")
    prepared = []
    for recipe in recipes:
        seq = recipe["source_paint_order"]
        matches = [p for p in page_data.primitives if p.source_draw_order == seq]
        capsule = recipe["source_capsule_proof"]["capsule"]
        points = [
            model_point(p, page.rect.height, scale, flip_y)
            for p in (capsule["start"], capsule["end"])
        ]
        if (
            len(matches) != 1
            or matches[0].type not in ("line", "polyline")
            or len(matches[0].points) != 2
            or not all(
                _close(p, q) for p, q in zip(matches[0].points, points, strict=True)
            )
        ):
            raise ValueError(
                "Source capsule has no unique unchanged normalized centerline"
            )
        prepared.append(
            dict(
                recipe=recipe,
                primitive_id=matches[0].id,
                points_mm=points,
                scale_mm=scale,
                flip_y=bool(flip_y),
                page_height=page.rect.height,
            )
        )
    return prepared, unresolved


def _native_capsule(spec, collection, material):
    import bpy

    capsule = spec["recipe"]["source_capsule_proof"]["capsule"]
    origin, controls = capsule_controls(capsule)
    scale = spec["scale_mm"] / 1000
    sign = -1 if spec["flip_y"] else 1
    expected = [(x * scale, y * scale * sign, 0.0, weight) for x, y, weight in controls]
    curve = bpy.data.curves.new("PDF_Source_Capsule", "CURVE")
    obj = None
    try:
        curve.dimensions = "2D"
        curve.fill_mode = "BOTH"
        curve.bevel_depth = 0
        curve.extrude = 0
        curve.resolution_u = 24
        spline = curve.splines.new("NURBS")
        spline.points.add(len(expected) - 1)
        for point, co in zip(spline.points, expected, strict=True):
            point.co = co
        spline.order_u = 3
        spline.use_endpoint_u = False
        spline.use_bezier_u = True
        spline.use_cyclic_u = True
        if (
            spline.order_u != 3
            or spline.use_endpoint_u
            or not spline.use_bezier_u
            or not spline.use_cyclic_u
            or len(spline.points) != len(expected)
            or any(
                not native_float_close(actual, wanted)
                for point, co in zip(spline.points, expected, strict=True)
                for actual, wanted in zip(point.co, co, strict=True)
            )
            or curve.dimensions != "2D"
            or curve.fill_mode != "BOTH"
            or curve.bevel_depth != 0
            or curve.extrude != 0
        ):
            raise ValueError("Native capsule rational boundary construction changed")
        curve.materials.append(material)
        obj = bpy.data.objects.new(
            f"PDF_Source_Capsule_{spec['recipe']['source_paint_order']}", curve
        )
        x, y = model_point(
            origin, spec["page_height"], spec["scale_mm"], spec["flip_y"]
        )
        obj.location = (x / 1000, y / 1000, 0.0)
        if not all(
            native_float_close(actual, wanted)
            for actual, wanted in zip(
                obj.location, (x / 1000, y / 1000, 0.0), strict=True
            )
        ):
            raise ValueError("Native capsule source origin changed")
        collection.objects.link(obj)
        obj["pdf_source_capsule"] = json.dumps(spec, sort_keys=True)
        obj["pdf_source_primitive_id"] = spec["primitive_id"]
        obj["pdf_geometry_representation"] = "exact_periodic_rational_quadratic_capsule"
        obj["pdf_boundary_controls_source_local"] = json.dumps(controls)
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            if (
                mesh is None
                or not mesh.polygons
                or not any(p.area > 0 for p in mesh.polygons)
            ):
                raise ValueError("Native capsule produced no filled surface")
        finally:
            evaluated.to_mesh_clear()
        return obj
    except Exception:
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.curves.remove(curve)
        raise


def _file_sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def apply_capsules(
    page,
    prepared,
    collection,
    builder_config,
    *,
    source_path,
    image_dir,
    create_image_plane,
    image_cache,
    fitz,
):
    """Preserve canonical curves, pack exact local final paint, verify readback."""
    if not prepared:
        return []
    import bpy
    from .image_paint_order import (
        _verify_native_image,
        _verify_native_stroke,
        _world_corners,
    )

    recipes = [s["recipe"] for s in prepared]
    sha = recipes[0]["source_sha256"]
    if page.number + 1 != recipes[0]["page"] or _file_sha(source_path) != sha:
        raise ValueError("Capsule original source identity changed before render")
    owned = builder_config.get("_image_order_stroke_objects", {})
    centerlines = []
    for spec in prepared:
        matches = owned.get(spec["primitive_id"], ())
        if len(matches) != 1 or matches[0].type != "CURVE":
            raise ValueError("Capsule source centerline lacks unique native ownership")
        centerline = matches[0]
        _verify_native_stroke(
            centerline, dict(points_mm=spec["points_mm"], closed=False)
        )
        if len(centerline.data.materials) != 1:
            raise ValueError("Capsule source centerline material ownership changed")
        centerlines.append(centerline)
    rendered = render_recipes(page, recipes, fitz)
    if _file_sha(source_path) != sha:
        raise ValueError("Capsule original source identity changed during render")
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    top = max(
        0.0,
        max(
            (
                p.z
                for obj in collection.all_objects
                if obj.type in {"FONT", "CURVE", "MESH"} and not obj.hide_render
                for p in _world_corners(obj, graph)
            ),
            default=0.0,
        ),
    )
    records = []
    for spec, centerline, (encoded, pixels) in zip(
        prepared, centerlines, rendered, strict=True
    ):
        recipe = spec["recipe"]
        capsule = _native_capsule(spec, collection, centerline.data.materials[0])
        x0, y0, x1, y1 = recipe["coverage_bounds_pdf"]
        # PNG v=0 is its lower edge; PDF y increases downward.
        quad = [
            model_point(p, spec["page_height"], spec["scale_mm"], spec["flip_y"])
            for p in ((x0, y1), (x1, y1), (x1, y0), (x0, y0))
        ]
        path = (
            Path(image_dir)
            / f"nontext-{recipe['page']}-{recipe['source_paint_order']}.png"
        )
        path.write_bytes(encoded)
        placement = dict(
            path=str(path),
            page_number=recipe["page"],
            xref=-2,
            quad_mm=quad,
            source_kind="nontext_composite",
            source_paint_order=recipe["source_paint_order"],
        )
        image = create_image_plane(
            placement,
            collection,
            z_offset_m=top + 0.00005,
            image_cache=image_cache,
            style_identity=("source-nontext-composite", "emission", "opaque"),
        )
        if image is None:
            raise ValueError(
                "Qualified non-text composite native image construction failed"
            )
        bpy.context.view_layer.update()
        packed_sha = _verify_native_image(
            image, dict(model_quad_mm=quad), placement, bpy.data.images
        )
        if (
            packed_sha != pixels["png_sha256"]
            or min(p.z for p in _world_corners(image, graph)) <= top
        ):
            raise ValueError(
                "Qualified non-text composite pixel or display-depth readback failed"
            )
        image["pdf_nontext_composite_recipe"] = json.dumps(recipe, sort_keys=True)
        image["pdf_nontext_composite_pixels"] = json.dumps(pixels, sort_keys=True)
        image["pdf_nontext_composite_source_curve"] = capsule.name
        centerline["pdf_source_capsule_footprint"] = capsule.name
        records.append(
            dict(
                page=recipe["page"],
                source_paint_order=recipe["source_paint_order"],
                status="applied",
                source_pdf_sha256=sha,
                primitive_id=spec["primitive_id"],
                source_centerline=centerline.name,
                exact_capsule_curve=capsule.name,
                display_image=image.name,
                recipe=recipe,
                pixels=pixels,
                display_depth_m=float(image.location.z),
            )
        )
    return records
