"""Bounded proof for opaque source images above earlier native PDF paint.

This does not assign arbitrary PDF painter order to an entire 3D scene. Only an
exact, fully visible opaque image and a closed set of later opaque strokes and
final-page text crops may be reordered. Unsupported paint remains unqualified.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from .pdf_paint_proof import _RECT

_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def _matrix(value):
    if value is None:
        return _IDENTITY
    if not re.fullmatch(
        r"matrix\(\s*" + _NUMBER + r"(?:[ ,]+" + _NUMBER + r"){5}\s*\)", value
    ):
        raise ValueError("Unsupported SVG transform")
    result = tuple(float(v) for v in re.split(r"[ ,]+", value[7:-1].strip()))
    if (
        not all(math.isfinite(v) for v in result)
        or result[0] * result[3] == result[1] * result[2]
    ):
        raise ValueError("Degenerate SVG transform")
    return result


def _point(m, p):
    a, b, c, d, e, f = m
    return a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f


def _multiply(a, b):
    e, f = _point(a, (b[4], b[5]))
    return (
        a[0] * b[0] + a[2] * b[1],
        a[1] * b[0] + a[3] * b[1],
        a[0] * b[2] + a[2] * b[3],
        a[1] * b[2] + a[3] * b[3],
        e,
        f,
    )


def _quad(m, width=1.0, height=1.0):
    return tuple(
        _point(m, p) for p in ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height))
    )


def _bounds(points):
    xs, ys = zip(*points, strict=True)
    return min(xs), min(ys), max(xs), max(ys)


def _box(value):
    if len(value) != 4 or not all(math.isfinite(float(v)) for v in value):
        raise ValueError("Invalid source paint bounds")
    result = tuple(float(v) for v in value)
    if result[0] > result[2] or result[1] > result[3]:
        raise ValueError("Inverted source paint bounds")
    return result


def _ulp32(value):
    value = abs(float(value))
    bits = struct.unpack("I", struct.pack("f", value))[0]
    if bits >= 0x7F7FFFFF:
        raise ValueError("Nonfinite source coordinate")
    return struct.unpack("f", struct.pack("I", bits + 1))[0] - value


def _tolerance(*values):
    # MuPDF coordinates are float32; SVG serializes those same coordinates.
    return max(4 * _ulp32(v) for v in values) + 1e-7


def _close(a, b):
    return len(a) == len(b) and all(
        math.isfinite(x) and math.isfinite(y) and abs(x - y) <= _tolerance(x, y)
        for x, y in zip(a, b, strict=True)
    )


def _intersects(a, b):
    return min(a[2], b[2]) >= max(a[0], b[0]) and min(a[3], b[3]) >= max(a[1], b[1])


def _contains(quad, points):
    """Convex ordered nondegenerate quadrilateral, with source-roundoff only."""
    if len(quad) != 4 or any(len(p) != 2 for p in quad):
        return False
    if not all(math.isfinite(v) for p in quad for v in p):
        return False
    cross = lambda a, b, c: (
        (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    )
    signs = [cross(quad[i], quad[(i + 1) % 4], quad[(i + 2) % 4]) for i in range(4)]
    if not (all(v > 0 for v in signs) or all(v < 0 for v in signs)):
        return False
    direction = 1 if signs[0] > 0 else -1
    for point in points:
        for i in range(4):
            a, b = quad[i], quad[(i + 1) % 4]
            error = _tolerance(*a, *b, *point) * math.hypot(b[0] - a[0], b[1] - a[1])
            if direction * cross(a, b, point) < -error:
                return False
    return True


def _neutral(node, allowed):
    return set(node.attrib) <= allowed and float(node.get("opacity", "1")) == 1


def _clip_quad(node, inherited):
    if (
        set(node.attrib) - {"id", "clipPathUnits"}
        or node.get("clipPathUnits", "userSpaceOnUse") != "userSpaceOnUse"
    ):
        raise ValueError("Unsupported SVG clipping units")
    children = list(node)
    if len(children) != 1 or children[0].tag.rsplit("}", 1)[-1] != "path":
        raise ValueError("Unsupported SVG clip geometry")
    path = children[0]
    if set(path.attrib) - {"d", "transform", "clip-rule"} or path.get(
        "clip-rule", "nonzero"
    ) not in {"nonzero", "evenodd"}:
        raise ValueError("Unsupported SVG clip properties")
    match = _RECT.fullmatch(path.get("d", ""))
    if match is None:
        raise ValueError("Nonrectangular SVG clip")
    x0, y0, x1, y1, end = map(float, match.groups())
    if end != x0 or x0 == x1 or y0 == y1:
        raise ValueError("Degenerate SVG clip")
    matrix = _multiply(inherited, _matrix(path.get("transform")))
    return tuple(_point(matrix, p) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)))


def _svg_images(svg, fitz):
    """Painted, opaque image occurrences with proven full-quad clipping."""
    root = ET.fromstring(svg)
    if not _neutral(root, {"version", "width", "height", "viewBox", "opacity"}):
        return []
    ids = {}
    for node in root.iter():
        if node.get("id"):
            if node.get("id") in ids:
                return []
            ids[node.get("id")] = node
    result = []

    def visit(node, matrix, clips, safe):
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in {"defs", "clipPath", "mask", "symbol"}:
            return
        if tag == "g":
            safe = safe and _neutral(node, {"id", "transform", "clip-path", "opacity"})
            matrix = _multiply(matrix, _matrix(node.get("transform")))
            if node.get("clip-path"):
                match = re.fullmatch(r"url\(#([^()]+)\)", node.get("clip-path"))
                if match is None or match[1] not in ids:
                    safe = False
                else:
                    try:
                        clips = clips + [_clip_quad(ids[match[1]], matrix)]
                    except ValueError:
                        safe = False
        elif tag == "image":
            # Even an unsupported image occurrence is retained as a placeholder
            # so it cannot shift the occurrence join for later images.
            record = None
            if safe and _neutral(
                node,
                {
                    "id",
                    "width",
                    "height",
                    "transform",
                    "opacity",
                    "{http://www.w3.org/1999/xlink}href",
                },
            ):
                try:
                    width, height = (
                        int(node.get("width", "0")),
                        int(node.get("height", "0")),
                    )
                    if width <= 0 or height <= 0:
                        raise ValueError("Invalid source image dimensions")
                    matrix = _multiply(matrix, _matrix(node.get("transform")))
                    quad = _quad(matrix, width, height)
                    href = node.get("{http://www.w3.org/1999/xlink}href", "")
                    prefix = "data:image/png;base64,"
                    if not href.startswith(prefix):
                        raise ValueError("Nonlocal SVG image")
                    encoded = re.sub(r"[\t\r\n ]", "", href[len(prefix) :])
                    pixels = fitz.Pixmap(base64.b64decode(encoded, validate=True))
                    if (
                        pixels.width != width
                        or pixels.height != height
                        or pixels.alpha
                        or pixels.n != 3
                    ):
                        raise ValueError("Image is not demonstrably opaque RGB")
                    if not all(_contains(clip, quad) for clip in clips):
                        raise ValueError("Image footprint is clipped")
                    record = {
                        "quad_pdf": quad,
                        "bbox_pdf": _bounds(quad),
                        "rgb_sha256": hashlib.sha256(pixels.samples).hexdigest(),
                        "pixel_digest": bytes(pixels.digest).hex(),
                        "width": width,
                        "height": height,
                        "clip_quads_pdf": clips,
                        "svg_pixel_matrix": matrix,
                    }
                except (ValueError, TypeError, RuntimeError):
                    record = None
            result.append(record)
            return
        elif tag != "svg":
            # A referenced image hidden in a filter/use is not guessed to be a
            # direct paint. The complete image count/order join must also pass.
            return
        for child in node:
            visit(child, matrix, clips, safe)

    try:
        visit(root, _IDENTITY, [], True)
    except (TypeError, ValueError):
        return []
    return result


def _text_item_for_trace(trace, items):
    chars = [(int(c[0]), tuple(c[2])) for c in trace.get("chars", ())]
    if not chars:
        return None
    matches = []
    for item in items:
        layout = item.source_char_layout
        if len(layout) != len(chars):
            continue
        if all(
            len(c.text) == 1
            and ord(c.text) == code
            and _close(c.source_origin_pdf, origin)
            for c, (code, origin) in zip(layout, chars, strict=True)
        ):
            matches.append(item.id)
    return matches[0] if len(matches) == 1 else None


def plan_opaque_images(page, page_data, *, user_scale=1.0, flip_y=True):
    """Return source-only plans; native crop/ownership checks happen separately."""
    from .pdfcadcore.fitz_loader import import_fitz

    fitz = import_fitz()
    inventory = page.get_image_info(hashes=True, xrefs=True)
    if not inventory or int(getattr(page, "rotation", 0)) != 0:
        return []
    from .nontext_composite import _source_group_declarations

    try:
        _source_group_declarations(page)
    except ValueError:
        return []  # A flattened renderer flag cannot disprove original knockout.
    if not math.isfinite(user_scale) or user_scale <= 0:
        raise ValueError("Invalid source-to-model scale")
    log = [(kind, _box(bounds)) for kind, bounds in page.get_bboxlog()]
    image_orders = [
        (i, box) for i, (kind, box) in enumerate(log) if kind == "fill-image"
    ]
    if len(inventory) != len(image_orders):
        return []
    svg = page.get_svg_image(text_as_path=False)
    svg_images = _svg_images(svg, fitz)
    if len(svg_images) != len(inventory):
        return []
    raw = {
        r.get("seqno"): r
        for r in page_data._source_drawings
        if type(r.get("seqno")) is int
    }
    prims = {}
    for primitive in page_data.primitives:
        prims.setdefault(primitive.source_draw_order, []).append(primitive)
    traces = {r["seqno"]: r for r in page.get_texttrace()}
    nonnormal = [
        r
        for r in page.get_drawings(extended=True)
        if r.get("type") == "group"
        and (
            r.get("blendmode", "Normal") != "Normal"
            or r.get("opacity", 1) != 1
            or r.get("knockout", False)
        )
    ]
    result = []
    page_quad = tuple(
        (x, y)
        for x, y in (
            (page.rect.x0, page.rect.y0),
            (page.rect.x1, page.rect.y0),
            (page.rect.x1, page.rect.y1),
            (page.rect.x0, page.rect.y1),
        )
    )
    for info, (order, bounds), proof in zip(
        inventory, image_orders, svg_images, strict=True
    ):
        if proof is None or info.get("has-mask") or not _close(info["bbox"], bounds):
            continue
        if bytes(info["digest"]).hex() != proof["pixel_digest"] or (
            info["width"],
            info["height"],
        ) != (proof["width"], proof["height"]):
            continue
        quad = _quad(tuple(info["transform"]))
        if not all(
            _close(a, b) for a, b in zip(quad, proof["quad_pdf"], strict=True)
        ) or not _contains(page_quad, quad):
            continue
        region = bounds
        later_strokes, later_text, stroke_specs = {}, {}, {}
        eligible = True
        while True:
            previous = region
            for seq in range(order + 1, len(log)):
                kind, box = log[seq]
                if not _intersects(region, box):
                    continue
                if kind == "ignore-text":
                    continue
                if kind in {"fill-text", "stroke-text"}:
                    item = _text_item_for_trace(
                        traces.get(seq, {}), page_data.text_items
                    )
                    if item is None or kind != "fill-text":
                        eligible = False
                        break
                    later_text[seq] = item
                    continue
                source = raw.get(seq) or raw.get(seq - 1)
                source_order = source.get("seqno") if source else None
                if source is None or len(prims.get(source_order, ())) != 1:
                    eligible = False
                    break
                primitive = prims[source_order][0]
                if (
                    kind == "fill-path"
                    and seq == source_order
                    and source.get("fill_opacity") == 0
                ):
                    continue
                expected_stroke_order = source_order + (source.get("type") == "fs")
                if (
                    kind != "stroke-path"
                    or seq != expected_stroke_order
                    or source.get("stroke_opacity") != 1
                    or primitive.type not in {"line", "polyline", "rect", "closed_loop"}
                    or primitive.clip_fill_group_id
                    or primitive.dash_pattern
                    or primitive.source_stroke_color is None
                    or not primitive.points
                ):
                    eligible = False
                    break
                later_strokes[seq] = primitive.id
                stroke_specs[seq] = {
                    "points_mm": tuple(tuple(p) for p in primitive.points),
                    "closed": primitive.type in {"rect", "closed_loop"},
                    "source_paint_bounds_pdf": box,
                }
                region = (
                    min(region[0], box[0]),
                    min(region[1], box[1]),
                    max(region[2], box[2]),
                    max(region[3], box[3]),
                )
            if not eligible or region == previous:
                break
        if not eligible or any(_intersects(region, _box(r["rect"])) for r in nonnormal):
            continue

        def model_point(point):
            x, y = point
            x, y = x - page.rect.x0, y - page.rect.y0
            if flip_y:
                y = page.rect.height - y
            return x * 25.4 / 72 * user_scale, y * 25.4 / 72 * user_scale

        model_quad = [model_point(p) for p in reversed(quad)]
        for spec in stroke_specs.values():
            x0, y0, x1, y1 = spec["source_paint_bounds_pdf"]
            spec["paint_bounds_mm"] = _bounds(
                (model_point((x0, y0)), model_point((x1, y1)))
            )
        result.append(
            dict(
                proof,
                source_image_number=int(info["number"]),
                source_xref=int(info["xref"]),
                source_transform_pdf=list(info["transform"]),
                source_paint_order=order,
                model_quad_mm=model_quad,
                page_number=int(page_data.page_number),
                later_strokes=later_strokes,
                later_stroke_specs=stroke_specs,
                later_text_items=later_text,
                source_svg_sha256=hashlib.sha256(svg.encode("utf-8")).hexdigest(),
            )
        )
    return result


def placement_matches(plan, placement):
    """Bind a newly constructed image's actual file/ordered footprint to proof."""
    from .pdfcadcore.fitz_loader import import_fitz

    fitz = import_fitz()
    try:
        if placement.get("source_kind") not in {"xobject", "inline"}:
            return False
        if (
            int(placement["xref"]) != plan["source_xref"]
            or int(placement["page_number"]) != plan["page_number"]
        ):
            return False
        if len(placement.get("quad_mm", ())) != 4 or not all(
            _close(a, b)
            for a, b in zip(placement["quad_mm"], plan["model_quad_mm"], strict=True)
        ):
            return False
        if (
            placement.get("source_kind") == "inline"
            and placement.get("source_image_number") != plan["source_image_number"]
        ):
            return False
        pixels = fitz.Pixmap(placement["path"])
        return (
            not pixels.alpha
            and pixels.n == 3
            and pixels.width == plan["width"]
            and pixels.height == plan["height"]
            and hashlib.sha256(pixels.samples).hexdigest() == plan["rgb_sha256"]
        )
    except (KeyError, ValueError, TypeError, RuntimeError, OSError):
        return False


def _local_geometry(obj):
    if obj.type == "MESH":
        return (
            tuple(tuple(v.co) for v in obj.data.vertices),
            tuple(tuple(e.vertices) for e in obj.data.edges),
            tuple(tuple(p.vertices) for p in obj.data.polygons),
            tuple(tuple(p.uv) for p in obj.data.uv_layers.active.data),
        )
    if obj.type == "CURVE":
        return tuple(
            (
                s.type,
                tuple(tuple(p.co) for p in s.points),
                tuple(
                    (tuple(p.co), tuple(p.handle_left), tuple(p.handle_right))
                    for p in s.bezier_points
                ),
            )
            for s in obj.data.splines
        )
    raise ValueError("Image paint-order ownership has an unsupported native type")


def _verify_native_image(obj, plan, placement, images):
    """Verify actual host pixels, material binding, mesh and UV before movement."""
    from .packed_assets import verify_packed_sha256

    if (
        obj.type != "MESH"
        or obj.parent is not None
        or obj.modifiers
        or obj.constraints
        or obj.hide_render
        or len(obj.data.vertices) != 4
        or len(obj.data.polygons) != 1
    ):
        raise ValueError("Source-qualified image has unexpected native geometry")
    mesh = obj.data
    world = [obj.matrix_world @ v.co for v in mesh.vertices]
    if not all(
        _close((p.x * 1000, p.y * 1000), xy)
        for p, xy in zip(world, plan["model_quad_mm"], strict=True)
    ):
        raise ValueError("Source-qualified image native XY changed")
    if len({float(v.co.z) for v in mesh.vertices}) != 1:
        raise ValueError("Source-qualified image is not a planar native image")
    uv = mesh.uv_layers.active
    polygon = mesh.polygons[0]
    if (
        uv is None
        or len(uv.data) != 4
        or len(polygon.loop_indices) != 4
        or set(polygon.vertices) != {0, 1, 2, 3}
    ):
        raise ValueError("Source-qualified image has invalid native UV topology")
    expected_uv = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    for index in polygon.loop_indices:
        if tuple(uv.data[index].uv) != expected_uv[mesh.loops[index].vertex_index]:
            raise ValueError("Source-qualified image native UV changed")
    image = images.get(str(obj.get("pdf_image_datablock", "")))
    expected_sha = hashlib.sha256(Path(placement["path"]).read_bytes()).hexdigest()
    verify_packed_sha256(image, expected_sha)
    if (
        len(mesh.materials) != 1
        or not mesh.materials[0].use_nodes
        or image.colorspace_settings.name != "Non-Color"
    ):
        raise ValueError("Source-qualified image material binding changed")
    tree = mesh.materials[0].node_tree
    nodes = {node.type: node for node in tree.nodes}
    if len(tree.nodes) != 3 or set(nodes) != {
        "TEX_IMAGE",
        "BSDF_PRINCIPLED",
        "OUTPUT_MATERIAL",
    }:
        raise ValueError("Source-qualified image material graph changed")
    tex, shader, output = (
        nodes["TEX_IMAGE"],
        nodes["BSDF_PRINCIPLED"],
        nodes["OUTPUT_MATERIAL"],
    )
    from .image_materials import source_image_sockets

    _, emission = source_image_sockets(shader)
    links = [(link.from_socket, link.to_socket) for link in tree.links]
    expected_links = [
        (tex.outputs["Color"], emission),
        (tex.outputs["Alpha"], shader.inputs["Alpha"]),
        (shader.outputs["BSDF"], output.inputs["Surface"]),
    ]
    if (
        tex.image != image
        or len(links) != 3
        or any(link not in links for link in expected_links)
        or tuple(shader.inputs["Base Color"].default_value) != (0, 0, 0, 1)
        or shader.inputs["Emission Strength"].default_value != 1
    ):
        raise ValueError(
            "Source-qualified image is not the verified opaque source material"
        )
    return expected_sha


def _verify_native_stroke(obj, spec):
    """Only the exact single source centerline may become a later-paint object."""
    if (
        obj.parent is not None
        or obj.modifiers
        or obj.constraints
        or obj.hide_render
        or len(obj.data.splines) != 1
    ):
        raise ValueError("Later stroke contains unrelated or transformed native paint")
    spline = obj.data.splines[0]
    if (
        spline.type != "POLY"
        or bool(spline.use_cyclic_u) != spec["closed"]
        or len(spline.points) != len(spec["points_mm"])
    ):
        raise ValueError("Later stroke source topology changed")
    from mathutils import Vector

    for point, expected in zip(spline.points, spec["points_mm"], strict=True):
        world = obj.matrix_world @ Vector(tuple(point.co)[:3])
        if not _close((world.x * 1000, world.y * 1000), expected[:2]):
            raise ValueError("Later stroke source XY changed")


def _world_corners(obj, depsgraph):
    """Measure rendered geometry, avoiding stale evaluated curve bound boxes.

    Native curves can expose a default-radius evaluated bound box during import,
    even after a graph update. Mesh vertices include the actual bevel/extrusion.
    Empty evaluated geometry contributes no bounds; no source object is removed.
    """
    from mathutils import Vector

    evaluated = obj.evaluated_get(depsgraph)
    matrix = evaluated.matrix_world.copy()
    try:
        mesh = evaluated.to_mesh()
        if mesh is None:
            raise ValueError("Native display geometry could not be evaluated")
        if not mesh.vertices:
            return []
        low = [math.inf] * 3
        high = [-math.inf] * 3
        for vertex in mesh.vertices:
            point = matrix @ vertex.co
            if not all(math.isfinite(v) for v in point):
                raise ValueError("Native display geometry has nonfinite coordinates")
            for axis in range(3):
                low[axis] = min(low[axis], point[axis])
                high[axis] = max(high[axis], point[axis])
        return [Vector((x, y, z)) for x in (low[0], high[0])
                for y in (low[1], high[1]) for z in (low[2], high[2])]
    finally:
        evaluated.to_mesh_clear()


def _move_display_z(obj, depth):
    """Only pure display Z changes; preserve all local source geometry and XY."""
    if obj.parent is not None or not math.isfinite(depth):
        raise ValueError("Unexpected native image-order parent/depth")
    xy = tuple(obj.location)[:2]
    original_z = float(obj.location.z)
    rotation, scale = tuple(obj.rotation_euler), tuple(obj.scale)
    geometry = _local_geometry(obj)
    obj.location.z = depth
    if (
        float(obj.location.z) != depth
        and abs(float(obj.location.z) - depth) > _tolerance(depth)
        or tuple(obj.location)[:2] != xy
        or tuple(obj.rotation_euler) != rotation
        or tuple(obj.scale) != scale
        or _local_geometry(obj) != geometry
    ):
        raise ValueError("Native display-depth readback changed source geometry")
    obj["pdf_source_geometry_z_m"] = original_z
    obj["pdf_image_order_display_depth_m"] = float(obj.location.z)


def apply_opaque_image_order(plans, collection, native_images, builder_config):
    """Apply only fully owned plans, before final-page crops are positioned."""
    if not plans:
        return []
    import bpy
    from .late_paint import _has_final_page_pixels

    bpy.context.view_layer.update()
    owned = builder_config.get("_image_order_stroke_objects", {})
    crops = {}
    for obj in collection.all_objects:
        if _has_final_page_pixels(obj):
            crops.setdefault(str(obj.get("pdf_raster_source_item_id")), []).append(obj)
    records = []
    for plan in plans:
        record = {
            "source_image_number": plan["source_image_number"],
            "source_paint_order": plan["source_paint_order"],
            "page": plan["page_number"],
            "status": "unqualified",
            "source_proof": plan,
        }
        records.append(record)
        matched = [
            (obj, placement)
            for obj, placement in native_images
            if placement_matches(plan, placement)
        ]
        if len(matched) != 1:
            record["reason"] = (
                "image_occurrence_not_uniquely_bound_to_native_pixels_and_xy"
            )
            continue
        if any(
            len(crops.get(f"page:{plan['page_number']}:text:{item}", ())) != 1
            for item in plan["later_text_items"].values()
        ):
            record["reason"] = "later_text_is_not_a_verified_final_page_pixel_crop"
            continue
        image, placement = matched[0]
        record["native_packed_image_sha256"] = _verify_native_image(
            image, plan, placement, bpy.data.images
        )
        depsgraph = bpy.context.evaluated_depsgraph_get()
        strokes = []
        for seq, primitive_id in sorted(plan["later_strokes"].items()):
            objects = owned.get(primitive_id, ())
            if len(objects) != 1 or objects[0].type != "CURVE":
                raise ValueError(
                    "Source-qualified later stroke lacks unique native ownership"
                )
            obj = objects[0]
            if int(obj.get("pdf_image_order_primitive_id", -1)) != primitive_id:
                raise ValueError("Source-qualified later stroke identity changed")
            spec = plan["later_stroke_specs"][seq]
            _verify_native_stroke(obj, spec)
            x0, y0, x1, y1 = spec["paint_bounds_mm"]
            native_xy = [
                (p.x * 1000, p.y * 1000) for p in _world_corners(obj, depsgraph)
            ]
            if not native_xy:
                record["reason"] = "native_stroke_has_no_evaluated_geometry"
                break
            if not _contains(((x0, y0), (x1, y0), (x1, y1), (x0, y1)), native_xy):
                record["reason"] = (
                    "native_stroke_footprint_exceeds_source_dependency_bounds"
                )
                break
            strokes.append((seq, obj))
        if record.get("reason"):
            continue

        def top_of(obj, graph=depsgraph):
            return max((p.z for p in _world_corners(obj, graph)), default=-math.inf)

        top = max(
            (
                top_of(obj)
                for obj in collection.all_objects
                if obj.type in {"FONT", "CURVE", "MESH"} and not obj.hide_render
            ),
            default=0.0,
        )

        def raise_above(obj, preceding_top, graph=depsgraph, read_top=top_of):
            bottom = min(p.z for p in _world_corners(obj, graph))
            _move_display_z(
                obj, float(obj.location.z) + preceding_top + 0.00005 - bottom
            )
            bpy.context.view_layer.update()
            if min(p.z for p in _world_corners(obj, graph)) <= preceding_top:
                raise ValueError("Native display depth failed to clear preceding paint")
            return read_top(obj)

        top = raise_above(image, top)
        image["pdf_image_order_source_proof"] = json.dumps(plan, sort_keys=True)
        record["native_image"] = image.name
        record["native_image_display_depth_m"] = float(image.location.z)
        record["later_native_strokes"] = []
        for seq, obj in strokes:
            # The source line center remains at its exact local XY. Its tube
            # radius must also clear the preceding opaque surface.
            top = raise_above(obj, top)
            record["later_native_strokes"].append(
                {"seqno": seq, "object": obj.name, "depth_m": float(obj.location.z)}
            )
        record["later_final_crop_items"] = list(plan["later_text_items"].values())
        record["status"] = "applied"
        bpy.context.view_layer.update()
    return records
