"""Apply the PDF viewport to delivered text without changing its representation.

An owned Geometry Nodes intersection keeps FONT bodies, glyph curves, source
transforms and text depth editable. Its hidden page prism follows page stacking.
Only actual evaluated ink crossing a page edge receives the modifier.
"""
from __future__ import annotations

import math


def polygon_area(points):
    if len(points) < 3:
        return 0.0
    # Translation cancels from area. Subtract an anchor before products rather
    # than subtracting large, nearly equal world-coordinate products afterward.
    x0, y0 = points[0][:2]
    return abs(math.fsum((a[0] - x0) * (b[1] - y0) - (b[0] - x0) * (a[1] - y0)
                         for a, b in zip(points, points[1:] + points[:1], strict=True))) / 2.0


def rectangle_intersection(points, bounds):
    """Independent planar intersection used to verify the native operation."""
    result = [tuple(point[:2]) for point in points]
    for axis, limit, sign in ((0, bounds[0], 1), (0, bounds[2], -1),
                              (1, bounds[1], 1), (1, bounds[3], -1)):
        if not result:
            break
        previous = result[-1]
        inside_previous = sign * (previous[axis] - limit) >= 0
        output = []
        for point in result:
            inside = sign * (point[axis] - limit) >= 0
            if inside != inside_previous:
                t = (limit - previous[axis]) / (point[axis] - previous[axis])
                other = 1 - axis
                crossing = [0.0, 0.0]
                crossing[axis] = limit
                crossing[other] = previous[other] + t * (point[other] - previous[other])
                output.append(tuple(crossing))
            if inside:
                output.append(point)
            previous, inside_previous = point, inside
        result = output
    return result


def _evaluated_ink(obj, bpy):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    if mesh is None:
        raise ValueError('Native text viewport clipping has no evaluated mesh')
    try:
        # Blender stores these values as float32. Read them freshly, then apply
        # the affine in Python doubles: mathutils would round each translated
        # world vertex back to float32, losing tiny glyph edges on later sheets.
        matrix = tuple(tuple(float(value) for value in row) for row in evaluated.matrix_world)
        local = [tuple(float(value) for value in vertex.co) for vertex in mesh.vertices]
        points = [tuple(math.fsum((matrix[row][3], *(matrix[row][column] * point[column]
                                                    for column in range(3))))
                        for row in range(3)) for point in local]
        if not all(math.isfinite(value) for point in points for value in point):
            raise ValueError('Native text viewport clipping has non-finite geometry')
        mesh.calc_loop_triangles()
        triangles = [[points[index] for index in triangle.vertices]
                     for triangle in mesh.loop_triangles]
        return points, triangles
    finally:
        evaluated.to_mesh_clear()


def _clip_tree(bpy, guide):
    tree = bpy.data.node_groups.new('PDF text page viewport', 'GeometryNodeTree')
    try:
        if hasattr(tree, 'interface'):
            for direction in ('INPUT', 'OUTPUT'):
                tree.interface.new_socket(name='Geometry', in_out=direction,
                                          socket_type='NodeSocketGeometry')
        else:
            tree.inputs.new('NodeSocketGeometry', 'Geometry')
            tree.outputs.new('NodeSocketGeometry', 'Geometry')
        source = tree.nodes.new('NodeGroupInput')
        output = tree.nodes.new('NodeGroupOutput')
        page = tree.nodes.new('GeometryNodeObjectInfo')
        page.transform_space = 'RELATIVE'
        page.inputs['Object'].default_value = guide
        intersection = tree.nodes.new('GeometryNodeMeshBoolean')
        intersection.operation = 'INTERSECT'
        if hasattr(intersection, 'solver'):
            intersection.solver = 'EXACT'
        for name in ('Self Intersection', 'Hole Tolerant'):
            if intersection.inputs.get(name) is not None:
                intersection.inputs[name].default_value = True
        operands = next(socket for socket in intersection.inputs if socket.is_multi_input)
        tree.links.new(source.outputs['Geometry'], operands)
        tree.links.new(page.outputs['Geometry'], operands)
        result = next(socket for socket in intersection.outputs if socket.type == 'GEOMETRY')
        tree.links.new(result, output.inputs['Geometry'])
        return tree
    except Exception:
        bpy.data.node_groups.remove(tree)
        raise


def _page_prism(bpy, collection, bounds, z_bounds):
    x0, y0, x1, y1 = bounds
    z0, z1 = z_bounds
    mesh = bpy.data.meshes.new('PDF hidden text viewport prism')
    points = [(x, y, z) for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)]
    mesh.from_pydata(points, [], [(0, 2, 3, 1), (4, 5, 7, 6),
                                  (0, 1, 5, 4), (2, 6, 7, 3),
                                  (0, 4, 6, 2), (1, 3, 7, 5)])
    mesh.update()
    guide = bpy.data.objects.new('PDF text viewport helper', mesh)
    collection.objects.link(guide)
    guide['pdf_page_clip_helper'] = True
    guide.hide_render = True
    guide.hide_select = True
    guide.hide_set(True)
    return guide


def verify_clipped_ink(obj, bpy, bounds, expected_area, original_z):
    points, triangles = _evaluated_ink(obj, bpy)
    tolerance = max(1e-8, max(abs(value) for value in bounds) * 1e-6)
    if any(not (bounds[0] - tolerance <= p[0] <= bounds[2] + tolerance and
                bounds[1] - tolerance <= p[1] <= bounds[3] + tolerance) for p in points):
        raise ValueError('Native text viewport retained out-of-page ink')
    area = sum(polygon_area([tuple(point[:2]) for point in triangle])
               for triangle in triangles)
    if abs(area - expected_area) > max(1e-12, expected_area * 2e-4):
        raise ValueError('Native text viewport changed the visible source ink area')
    if points and expected_area > 1e-12:
        depth = max(p[2] for p in points) - min(p[2] for p in points)
        if abs(depth - (original_z[1] - original_z[0])) > tolerance:
            raise ValueError('Native text viewport changed source text depth')
    return {'visible_projected_area_m2': area, 'evaluated_vertex_count': len(points),
            'visible_ink_empty': not points, 'inside_page_verified': True,
            'visible_ink_area_verified': True, 'source_depth_verified': True}


def clip_delivered_page_text(collection, records, *, page_number, width_mm, height_mm):
    records = tuple(record for record in records or ()
                    if record.get('page') == page_number and record.get('status') == 'delivered'
                    and record.get('final_representation') != 'raster')
    if not records:
        return []
    import bpy
    from mathutils import Vector

    bounds = (0.0, 0.0, float(width_mm) * 0.001, float(height_mm) * 0.001)
    if not all(math.isfinite(value) for value in bounds) or min(bounds[2:]) <= 0:
        raise ValueError('Native text viewport needs finite source page bounds')
    guide, tree = None, None
    modified = []
    proofs = []
    try:
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for record in records:
            for entity_id in record.get('entity_ids') or ():
                obj = bpy.data.objects.get(entity_id)
                if obj is None:
                    raise ValueError('Delivered text disappeared before viewport clipping')
                evaluated = obj.evaluated_get(depsgraph)
                corners = [tuple(evaluated.matrix_world @ Vector(corner))
                           for corner in evaluated.bound_box]
                if corners and all(bounds[0] <= p[0] <= bounds[2] and
                                   bounds[1] <= p[1] <= bounds[3] for p in corners):
                    continue
                points, triangles = _evaluated_ink(obj, bpy)
                if not points or all(bounds[0] <= p[0] <= bounds[2] and
                                     bounds[1] <= p[1] <= bounds[3] for p in points):
                    continue
                original_type = obj.type
                if not triangles or not any(polygon_area([p[:2] for p in triangle]) > 1e-18
                                            for triangle in triangles):
                    raise ValueError('Boundary text has no proven surface ink for native intersection')
                original_z = (min(p[2] for p in points), max(p[2] for p in points))
                expected_area = sum(polygon_area(rectangle_intersection(triangle, bounds))
                                    for triangle in triangles)
                if guide is None:
                    margin = max(1.0, abs(original_z[0]), abs(original_z[1]), *bounds)
                    guide = _page_prism(bpy, collection, bounds, (-margin * 10, margin * 10))
                    tree = _clip_tree(bpy, guide)
                modifier = obj.modifiers.new('PDF source page viewport', 'NODES')
                modifier.node_group = tree
                modified.append((obj, modifier))
                bpy.context.view_layer.update()
                proof = verify_clipped_ink(obj, bpy, bounds, expected_area, original_z)
                if obj.type != original_type:
                    raise ValueError('Native text viewport changed the selected representation')
                proof.update(entity_id=entity_id, page_number=page_number,
                             page_bounds_m=list(bounds), original_object_type=original_type,
                             expected_projected_area_m2=expected_area,
                             source_z_bounds_m=list(original_z), guide_entity_id=guide.name,
                             node_group=tree.name, operation='exact_native_page_intersection')
                obj['pdf_page_clip_helper_id'] = guide.name
                obj['pdf_page_clip_expected_area_m2'] = expected_area
                obj['pdf_page_clip_source_z_m'] = list(original_z)
                record.setdefault('page_viewport_clips', []).append(proof)
                proofs.append(proof)
        return proofs
    except Exception:
        for obj, modifier in reversed(modified):
            obj.modifiers.remove(modifier)
        if tree is not None:
            bpy.data.node_groups.remove(tree)
        if guide is not None:
            mesh = guide.data
            bpy.data.objects.remove(guide, do_unlink=True)
            bpy.data.meshes.remove(mesh)
        raise
