"""Preserve source order between overlapping opaque native fill faces.

This limited pass does not reorder fills against strokes, text or images. It
separates already-built opaque faces within a 0.05 mm display band; unknown
source order or nonopaque overlap prevents a component from being moved.
"""
from __future__ import annotations

import json
import math


def same_native_object(first, second):
    """Blender may expose multiple RNA wrappers for one native ID."""
    if first is second:
        return True
    read_first, read_second = getattr(first, 'as_pointer', None), getattr(second, 'as_pointer', None)
    return bool(callable(read_first) and callable(read_second)
                and read_first() != 0 and read_first() == read_second())


def fill_depth_plan(records, budget_m=0.00005):
    """Rank only overlapping source paints; disjoint faces may share a depth."""
    if not math.isfinite(budget_m) or not 0 < budget_m <= 0.00005:
        raise ValueError("Invalid opaque fill display budget")
    if len({row["id"] for row in records}) != len(records):
        raise ValueError("Duplicate native fill identity")
    for row in records:
        box = row["bounds"]
        if (len(box) != 4 or not all(math.isfinite(v) for v in box)
                or box[0] >= box[2] or box[1] >= box[3]):
            raise ValueError("Invalid native fill footprint")
    overlaps = lambda a, b: min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])
    adjacent = {i: set() for i in range(len(records))}
    # Sweep in X. This remains conservative for nonrectangular faces: false
    # overlaps add only a source-ordered depth; no source contour is altered.
    active = []
    for i in sorted(adjacent, key=lambda index: records[index]["bounds"][0]):
        box = records[i]["bounds"]
        active = [j for j in active if records[j]["bounds"][2] > box[0]]
        for j in active:
            if overlaps(box, records[j]["bounds"]):
                adjacent[i].add(j)
                adjacent[j].add(i)
        active.append(i)
    remaining = set(adjacent)
    output = []
    while remaining:
        component, todo = set(), [min(remaining)]
        while todo:
            index = todo.pop()
            if index in component:
                continue
            component.add(index)
            todo.extend(adjacent[index] - component)
        remaining -= component
        if len(component) == 1:
            continue
        if any(type(records[i].get("seqno")) is not int or records[i]["seqno"] < 0
               or records[i].get("opaque") is not True for i in component):
            output.append({"status": "unqualified", "reason": "unknown_or_nonopaque_overlapping_fill",
                           "ids": [records[i]["id"] for i in sorted(component)]})
            continue
        rank = {}
        for i in sorted(component, key=lambda index: records[index]["seqno"]):
            rank[i] = max((rank[j] + 1 for j in adjacent[i]
                           if records[j]["seqno"] < records[i]["seqno"]), default=0)
        highest = max(rank.values())
        if highest == 0:
            continue  # Same original paint operation: no invented subpath order.
        output.append({"status": "qualified", "budget_m": budget_m,
                       "rows": [{"id": records[i]["id"], "seqno": records[i]["seqno"],
                                 "rank": rank[i], "offset_m": budget_m * rank[i] / highest}
                                for i in sorted(component)]})
    return output


def validate_source_fill(spec, native_members, graph):
    """Verify actual vertices, opaque emission and importer-owned source data."""
    from .image_paint_order import _close, _world_corners
    from .visual_style import preview_color

    obj = spec["object"]
    if not any(same_native_object(obj, member) for member in native_members) or obj.type != "MESH" or len(obj.data.polygons) != 1:
        raise ValueError("Opaque fill native ownership changed")
    if obj.parent is not None or obj.modifiers or obj.constraints or obj.hide_render:
        raise ValueError("Opaque fill has an unsupported native transform or visibility")
    points = [tuple(vertex.co) for vertex in obj.data.vertices]
    if len(points) < 3:
        raise ValueError("Opaque fill polygon has fewer than three source vertices")
    expected = [(x * 0.001, y * 0.001) for x, y in spec["points_mm"]]
    if len(expected) > 1 and expected[0] == expected[-1]:
        expected.pop()
    world = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    if (len(world) != len(expected) or
            not all(_close((p.x, p.y), q) for p, q in zip(world, expected, strict=True)) or
            len({p[2] for p in points}) != 1 or
            not _close([p.z for p in world], [world[0].z] * len(world))):
        raise ValueError("Opaque fill source footprint changed")
    loop = tuple(obj.data.polygons[0].vertices)
    expected_loop = tuple(range(len(points)))
    if (len(points) < 3 or len(loop) != len(points)
            or not any(loop == sequence[start:] + sequence[:start]
                       for sequence in (expected_loop, expected_loop[::-1])
                       for start in range(len(points)))):
        raise ValueError("Opaque fill polygon no longer owns the complete source loop")
    area = math.fsum(world[a].x * world[b].y - world[b].x * world[a].y
                     for a, b in zip(loop, loop[1:] + loop[:1], strict=True)) * .5
    if not math.isfinite(area) or area == 0.0:
        raise ValueError("Opaque fill polygon has no finite projected area")
    if len(obj.data.materials) != 1 or obj.data.polygons[0].material_index != 0:
        raise ValueError("Opaque fill material ownership changed")
    expected_rgb = preview_color(tuple(spec["fill_rgb"]), spec["visual_style"])
    material = obj.data.materials[0]
    if not _close(tuple(material.diffuse_color), (*expected_rgb, 1.0)):
        raise ValueError("Opaque fill material differs from its source style")
    if not material.use_nodes:
        raise ValueError("Opaque fill source emission material is unavailable")
    tree = material.node_tree
    nodes = {node.type: node for node in tree.nodes}
    if len(tree.nodes) != 2 or set(nodes) != {'EMISSION', 'OUTPUT_MATERIAL'}:
        raise ValueError("Opaque fill material graph changed")
    emission, output = nodes['EMISSION'], nodes['OUTPUT_MATERIAL']
    if (not _close(tuple(emission.inputs['Color'].default_value), (*expected_rgb, 1.0))
            or emission.inputs['Strength'].default_value != 1
            or len(tree.links) != 1
            or tree.links[0].from_socket != emission.outputs['Emission']
            or tree.links[0].to_socket != output.inputs['Surface']):
        raise ValueError("Opaque fill actual emission differs from source style")
    corners = _world_corners(obj, graph)
    if not corners:
        raise ValueError("Opaque fill has no evaluated native geometry")
    return world, points, corners


def apply_fill_depths(collection, owned):
    """Move only validated importer-owned fill objects; local geometry stays exact."""
    if not owned:
        return []
    import bpy
    from .image_paint_order import _close, _move_display_z, _world_corners

    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    records, objects = [], {}
    native_members = list(collection.all_objects)
    for spec in owned:
        obj = spec["object"]
        world, points, corners = validate_source_fill(spec, native_members, graph)
        bounds = (min(p.x for p in world), min(p.y for p in world),
                  max(p.x for p in world), max(p.y for p in world))
        record = {"id": spec["primitive_id"], "seqno": spec["source_draw_order"],
                  "opaque": spec["fill_opacity"] == 1.0, "bounds": bounds}
        records.append(record)
        objects[record["id"]] = (obj, points, float(obj.location.z), min(p.z for p in corners))
    result = fill_depth_plan(records)
    for component in result:
        if component["status"] != "qualified":
            continue
        entries = component["rows"]
        bottoms = [objects[row["id"]][3] for row in entries]
        if not _close(bottoms, [bottoms[0]] * len(bottoms)):
            component.update(status="unqualified", reason="fill_faces_do_not_share_original_plane")
            continue
        for row in entries:
            obj, original_points, old_location, _ = objects[row["id"]]
            _move_display_z(obj, old_location + row["offset_m"])
            if [tuple(v.co) for v in obj.data.vertices] != original_points:
                raise ValueError("Opaque fill display move changed local source geometry")
            obj["pdf_fill_order_primitive_id"] = row["id"]
            obj["pdf_fill_order_source_seqno"] = row["seqno"]
            obj["pdf_fill_order_display_offset_m"] = row["offset_m"]
            row["object"] = obj.name
        bpy.context.view_layer.update()
        actual = {row["id"]: min(p.z for p in _world_corners(objects[row["id"]][0], graph)) for row in entries}
        for first in entries:
            for second in entries:
                if first["rank"] < second["rank"] and not actual[first["id"]] < actual[second["id"]]:
                    raise ValueError("Native precision did not preserve source fill order")
        for row in entries:
            obj = objects[row["id"]][0]
            obj["pdf_fill_order_component"] = json.dumps(component, sort_keys=True)
    return result
