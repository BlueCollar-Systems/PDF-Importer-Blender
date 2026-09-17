"""Preserve final, unclipped PDF rectangle paint above native text.

The bounded suffix proof includes all PDF paints, including images and text.
Source XY stays unchanged; only presentation depth is raised. Final-page raster
text crops already include these paints and are excluded from the overlay fill.
"""
from __future__ import annotations

import math


def _box(points):
    points = list(points)
    if len(points) == 5 and points[0] == points[-1]:
        points.pop()
    if len(points) != 4 or len(set(points)) != 4:
        return None
    if not all(len(p) == 2 and all(math.isfinite(v) for v in p) for p in points):
        return None
    xs, ys = zip(*points, strict=True)
    box = min(xs), min(ys), max(xs), max(ys)
    if set(points) != {(x, y) for x in (box[0], box[2]) for y in (box[1], box[3])}:
        return None
    if not all((a[0] == b[0]) != (a[1] == b[1]) for a, b in zip(points, points[1:] + points[:1], strict=True)):
        return None
    return box


def final_rectangles(page, page_data):
    """Return only the complete final suffix of proven ordinary rect paints."""
    by_order = {}
    for prim in page_data.primitives:
        order = prim.source_draw_order
        if order is not None:
            by_order.setdefault(order, []).append(prim)
    candidates = {}
    for row in getattr(page_data, "_source_drawings", ()):
        order = row.get("seqno")
        if type(order) is not int or len(by_order.get(order, ())) != 1:
            continue
        prim = by_order[order][0]
        items = row.get("items") or ()
        if (row.get("level") != 0 or row.get("type") != "fs"
                or len(items) != 1 or items[0][0] != "re"
                or prim.type not in {"rect", "closed_loop"} or prim.clip_fill_group_id
                or not 0.0 < prim.fill_opacity < 1.0 or prim.stroke_opacity != 1.0
                or prim.source_fill_color is None or prim.source_stroke_color is None
                or prim.dash_pattern or row.get("lineJoin") != 0
                or not prim.line_width or prim.line_width <= 0):
            continue
        box = _box(prim.points)
        if box is not None and prim.line_width < min(box[2]-box[0], box[3]-box[1]):
            candidates[order] = (prim, box, row)
    if not candidates:
        return []
    log = page.get_bboxlog()
    end = len(log)
    result = []
    while end >= 2 and end - 2 in candidates:
        order = end - 2
        if log[order][0] != "fill-path" or log[order + 1][0] != "stroke-path":
            break
        result.append(candidates[order])
        end -= 2
    result.reverse()
    if not result:
        return []
    from .pdf_paint_proof import final_svg_rectangles

    if not final_svg_rectangles(page.get_svg_image(text_as_path=True), [row for _, _, row in result]):
        return []
    return [(prim, box) for prim, box, _ in result]


def subtract_rectangles(bounds, cutters):
    """Exact disjoint axis-aligned rectangles remaining after crop subtraction."""
    remaining = [bounds]
    for cx0, cy0, cx1, cy1 in cutters:
        next_rects = []
        for x0, y0, x1, y1 in remaining:
            ix0, iy0, ix1, iy1 = max(x0, cx0), max(y0, cy0), min(x1, cx1), min(y1, cy1)
            if ix0 >= ix1 or iy0 >= iy1:
                next_rects.append((x0, y0, x1, y1))
                continue
            pieces = ((x0, y0, ix0, y1), (ix1, y0, x1, y1),
                      (ix0, y0, ix1, iy0), (ix0, iy1, ix1, y1))
            next_rects.extend(r for r in pieces if r[0] < r[2] and r[1] < r[3])
        remaining = next_rects
    return remaining


def paint_rectangles(bounds, width):
    """Visible inner fill plus exact opaque centered miter-stroke ring."""
    x0, y0, x1, y1 = bounds
    half = width * .5
    ix0, iy0, ix1, iy1 = x0+half, y0+half, x1-half, y1-half
    ox0, oy0, ox1, oy1 = x0-half, y0-half, x1+half, y1+half
    return [(ix0, iy0, ix1, iy1), (ox0, oy0, ox1, iy0),
            (ox0, iy1, ox1, oy1), (ox0, iy0, ix0, iy1), (ix1, iy0, ox1, iy1)]


def apply_final_rectangles(page, page_data, collection, builder_config):
    records = final_rectangles(page, page_data)
    if not records:
        return []
    import bpy
    from mathutils import Vector
    from .visual_style import preview_color

    owned = builder_config.get("_source_paint_objects", {})
    # Only freshly built objects in this page collection can supply final crop
    # footprints. The engine has verified their actual packed pixels already.
    bpy.context.view_layer.update()
    cutters = []
    top = 0.0
    for obj in collection.all_objects:
        if obj.type in {"FONT", "CURVE", "MESH"}:
            top = max(top, max((obj.matrix_world @ Vector(p)).z for p in obj.bound_box))
        if not obj.get("pdf_raster_source_item_id"):
            continue
        if (obj.type != "MESH" or len(obj.data.vertices) != 4
                or not obj.get("pdf_image_sha256")
                or obj.get("pdf_image_source_page_number") != page_data.page_number):
            raise ValueError("Final PDF crop has no verified native rectangle identity")
        coords = [obj.matrix_world @ v.co for v in obj.data.vertices]
        box = _box([(v.x, v.y) for v in coords])
        if box is None:
            raise ValueError("Final PDF crop is not an axis-aligned rectangle")
        cutters.append(box)
    output = []
    for index, (prim, box_mm) in enumerate(records):
        objects = owned.get(prim.id)
        if objects is None:
            # A user disabled filled faces. Do not introduce an unsolicited fill.
            continue
        face, outline = objects
        bounds = tuple(v * .001 for v in box_mm)
        paints = paint_rectangles(bounds, prim.line_width * .001)
        vertices, polygons, indices = [], [], []
        visible_fill_area = 0.0
        for paint_index, paint in enumerate(paints):
            pieces = subtract_rectangles(paint, cutters)
            if paint_index == 0:
                visible_fill_area = sum((r[2]-r[0])*(r[3]-r[1]) for r in pieces)
            for x0, y0, x1, y1 in pieces:
                start = len(vertices)
                vertices.extend(((x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)))
                polygons.append(tuple(range(start, start + 4)))
                indices.append(0 if paint_index == 0 else 1)
        face.data.clear_geometry()
        face.data.from_pydata(vertices, [], polygons)
        face.data.update()
        face.data.materials.clear()
        alpha = prim.fill_opacity
        for source_rgb, opacity in ((prim.source_fill_color, alpha), (prim.source_stroke_color, 1.0)):
            rgb = preview_color(source_rgb, builder_config.get("visual_style", "source"))
            material = bpy.data.materials.new("PDF final annotation paint")
            material.diffuse_color = (*rgb, opacity)
            material.use_nodes = True
            if hasattr(material, "surface_render_method"):
                material.surface_render_method = "DITHERED"
            elif hasattr(material, "blend_method"):
                material.blend_method = "HASHED"
            nodes, links = material.node_tree.nodes, material.node_tree.links
            nodes.clear()
            emission = nodes.new("ShaderNodeEmission")
            emission.inputs["Color"].default_value = (*rgb, 1)
            target = nodes.new("ShaderNodeOutputMaterial")
            if opacity < 1:
                clear = nodes.new("ShaderNodeBsdfTransparent")
                mix = nodes.new("ShaderNodeMixShader")
                mix.inputs[0].default_value = opacity
                links.new(clear.outputs[0], mix.inputs[1])
                links.new(emission.outputs[0], mix.inputs[2])
                links.new(mix.outputs[0], target.inputs["Surface"])
            else:
                links.new(emission.outputs[0], target.inputs["Surface"])
            face.data.materials.append(material)
        for polygon, material_index in zip(face.data.polygons, indices, strict=True):
            polygon.material_index = material_index
        depth = top + .00005 + index * .000002
        face.location.z = depth
        face["pdf_source_fill_rgb"] = list(prim.source_fill_color)
        face["pdf_source_fill_opacity"] = alpha
        face["pdf_source_stroke_width_m"] = prim.line_width * .001
        face["pdf_source_paint_order"] = prim.source_draw_order
        face["pdf_display_depth_m"] = depth
        face["pdf_source_geometry_z_m"] = 0.0
        if outline is not None:
            # Keep the exact editable source centerline as a hidden helper;
            # the proven native miter ring supplies its visible paper width.
            outline.hide_render = True
            outline.hide_set(True)
            outline["pdf_display_replaced_by"] = face.name
        output.append({"source_primitive_id": prim.id, "source_paint_order": prim.source_draw_order,
                       "raw_fill_rgb": list(prim.source_fill_color), "fill_opacity": alpha,
                       "display_depth_m": depth, "final_crop_exclusions": len(cutters),
                       "source_fill_area_m2": (bounds[2]-bounds[0])*(bounds[3]-bounds[1]),
                       "visible_fill_area_m2": visible_fill_area,
                       "centered_miter_stroke_width_m": prim.line_width * .001})
    return output
