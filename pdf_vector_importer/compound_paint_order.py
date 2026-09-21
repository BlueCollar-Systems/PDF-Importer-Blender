"""Scoped painter order for opaque rectangles completely covering polygon clips.

The complete editable compound and its source-owned same-color outline stay
on one display plane. Unknown later paint prevents qualification.
"""
from __future__ import annotations

import hashlib
import math
import re
import xml.etree.ElementTree as ET

from .image_paint_order import (
    _bounds, _box, _close, _contains, _intersects, _matrix, _neutral,
)
from .opaque_rectangle_order import _same_cycle
from .opaque_rectangle_proof import _clip_excludes_region, _source_path_row, _segments_intersect
from .pdf_paint_proof import _rgb
from .triangle_paint_order import (
    _straight_path, _transformed_point_proof, _source_points_match, _JoinIndex,
    _match_bounds, _raw_stroke_points, _raw_stroke_segments,
    _later_stroke_plan, _stroke_envelope_proof, _source_group_declarations,
    _ulp32,
)


def _point_segment_distance(point, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    length2 = dx*dx+dy*dy
    if length2 == 0:
        return math.hypot(point[0]-a[0], point[1]-a[1])
    t = min(1., max(0., ((point[0]-a[0])*dx+(point[1]-a[1])*dy)/length2))
    return math.hypot(point[0]-(a[0]+t*dx), point[1]-(a[1]+t*dy))


def _round_stroke_misses_box(proof, box):
    """Exact finite-segment separation, expanded for both source uncertainties.

    A convex round pen sweeps a capsule. Segment/rectangle intersection and
    every endpoint/edge distance cover its complete footprint, not bbox holes.
    Tangency and uncertain separation remain overlaps.
    """
    points = proof.get('source_points_pdf', ())
    errors = proof.get('source_point_errors_pdf', ())
    width = proof.get('source_width_pdf', 0.)
    if (proof.get('closed') is not False or len(points) != 2 or len(errors) != 2
            or not math.isfinite(width) or width <= 0
            or any(len(p) != 2 for p in (*points, *errors))
            or any(not math.isfinite(v) for p in (*points, *errors) for v in p)
            or any(v < 0 for p in errors for v in p)):
        return False
    a, b = points
    x0, y0, x1, y1 = box
    if not all(math.isfinite(v) for v in box) or x0 > x1 or y0 > y1:
        return False
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if any(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in points):
        return False
    edges = list(zip(corners, corners[1:]+corners[:1], strict=True))
    if any(_segments_intersect(a, b, c, d) for c, d in edges):
        return False
    distance = min([_point_segment_distance(p, a, b) for p in corners]
                   + [_point_segment_distance(p, c, d) for p in points for c, d in edges])
    # Endpoint displacement bounds the displacement of their entire segment;
    # rectangle edge coordinates carry original MuPDF float32 uncertainty.
    uncertainty = max(math.hypot(*e) for e in errors) + math.hypot(
        max(4*_ulp32(x0), 4*_ulp32(x1)), max(4*_ulp32(y0), 4*_ulp32(y1)))
    return distance > math.nextafter(width/2+4*_ulp32(width)+uncertainty, math.inf)


def _disconnected_strokes(contours_mm, strokes):
    """Attach only actual touching painted capsules, including stroke chains."""
    connected = [(c[i], c[(i+1) % len(c)], 0.) for c in contours_mm for i in range(len(c))]
    remaining = list(strokes)
    while remaining:
        attached = []
        for stroke in remaining:
            p, q = stroke['points_mm']
            radius = stroke['width_mm']/2
            for a, b, other_radius in connected:
                distance = (0. if _segments_intersect(p, q, a, b) else
                            min(_point_segment_distance(v, a, b) for v in (p,q)))
                distance = min(distance, *(_point_segment_distance(v, p, q) for v in (a,b)))
                error = 8*sum(_ulp32(v) for point in (a,b,p,q) for v in point)
                if distance+error < radius+other_radius:
                    attached.append(stroke)
                    break
        if not attached:
            return [s['source_draw_order'] for s in remaining]
        connected.extend((*s['points_mm'], s['width_mm']/2) for s in attached)
        remaining = [s for s in remaining if s not in attached]
    return []


def _contours(items):
    """Only exact straight clip subpaths, retaining implicit PDF closure."""
    output, current = [], []
    for item in items:
        if item[0] == 're' and len(item) == 3:
            if current:
                output.append(current)
                current = []
            x0, y0, x1, y1 = _box(item[1])
            if x0 >= x1 or y0 >= y1:
                return None
            output.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
            continue
        if item[0] != 'l' or len(item) != 3:
            return None
        a, b = tuple(item[1]), tuple(item[2])
        if any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in (a, b)):
            return None
        if current and current[-1] != a:
            output.append(current)
            current = []
        if not current:
            current = [a]
        current.append(b)
    if current:
        output.append(current)
    for row in output:
        if row[-1] == row[0]:
            row.pop()
        if len(row) < 3:
            return None
    return output or None


def _svg_covered_clips(svg):
    """Bind live neutral rectangle paints to a complete single polygon clip."""
    if re.search(r'<\?xml-stylesheet(?:\s|\?>)', svg):
        return []
    root = ET.fromstring(svg)
    nodes = list(root.iter())
    identified = [n for n in nodes if n.get('id')]
    ids = {n.get('id'): n for n in identified}
    if (root.tag.rsplit('}', 1)[-1] != 'svg' or len(ids) != len(identified)
            or not _neutral(root, {'version', 'width', 'height', 'viewBox', 'opacity'})
            or any(n.tag.rsplit('}', 1)[-1] == 'style'
                   or (n is not root and n.tag.rsplit('}', 1)[-1] == 'svg') for n in nodes)):
        return []
    output = []
    ordinal = 0

    def clip_rows(name, matrices):
        match = re.fullmatch(r'url\(#([^()]+)\)', name)
        clip = ids.get(match[1]) if match else None
        if (clip is None or clip.tag.rsplit('}', 1)[-1] != 'clipPath'
                or set(clip.attrib) - {'id', 'clipPathUnits'}
                or clip.get('clipPathUnits', 'userSpaceOnUse') != 'userSpaceOnUse'
                or len(clip) != 1):
            raise ValueError('Unsupported compound clip')
        path = clip[0]
        if (path.tag.rsplit('}', 1)[-1] != 'path'
                or set(path.attrib) - {'d', 'transform', 'clip-rule'}
                or path.get('clip-rule') != 'evenodd'):
            raise ValueError('Unproven compound clip properties')
        parts = re.split(r'(?=M)', path.get('d', '').strip())
        if parts and not parts[0]:
            parts.pop(0)
        matrix_chain = matrices + (_matrix(path.get('transform')),)
        result = []
        for part in parts:
            points = _straight_path(part)
            if points is None or len(points) < 3:
                raise ValueError('Unsupported compound clip contour')
            transformed = [_transformed_point_proof(p, matrix_chain) for p in points]
            result.append({'points': [p for p, _ in transformed],
                           'point_errors': [e for _, e in transformed]})
        if not result:
            raise ValueError('Empty compound clip')
        return result

    def visit(node, matrices, clips, safe):
        nonlocal ordinal
        tag = node.tag.rsplit('}', 1)[-1]
        if tag in {'defs', 'clipPath', 'mask', 'symbol'}:
            return
        paint_ordinal = None
        if tag not in {'svg', 'g'}:
            paint_ordinal, ordinal = ordinal, ordinal+1
        if tag == 'g':
            safe = safe and _neutral(node, {'id', 'transform', 'clip-path', 'opacity'})
            try:
                matrices += (_matrix(node.get('transform')),)
                if node.get('clip-path'):
                    clips = clips + [clip_rows(node.get('clip-path'), matrices)]
            except (ValueError, KeyError, OverflowError):
                safe = False
        elif tag == 'path' and safe and len(clips) == 1:
            allowed = {'d', 'transform', 'fill', 'fill-opacity', 'fill-rule'}
            try:
                if not _neutral(node, allowed) or float(node.get('fill-opacity', '1')) != 1:
                    return
                points = _straight_path(node.get('d', ''))
                rgb = _rgb(node.get('fill', '#000000'))
                if points is None or len(points) != 4 or rgb is None:
                    return
                transformed = [_transformed_point_proof(p, matrices + (_matrix(node.get('transform')),)) for p in points]
                output.append({'points': [p for p, _ in transformed],
                               'point_errors': [e for _, e in transformed],
                               'rgb': rgb, 'contours': clips[0], 'paint_ordinal': paint_ordinal})
            except (ValueError, OverflowError):
                return
        elif tag != 'svg':
            return
        for child in node:
            visit(child, matrices, clips, safe)

    visit(root, (), [], True)
    return output


def _anchored_strokes(seq, anchor, log, raw, svg_rows, primitives, model, factor):
    """Pair a contiguous original stroke run with every live SVG paint ordinal.

    Equal paths occurring before and after a fill are distinct. This anchor
    proves occurrence identity without borrowing an earlier unclipped path.
    Rejected/unknown SVG paints still consume ordinals and break the join.
    """
    result = {}
    for order in range(seq+1, len(log)):
        if log[order][0] != 'stroke-path':
            break
        source = raw.get(order)
        points = _raw_stroke_points(source or {})
        matches = [r for r in svg_rows if r['paint_ordinal'] == anchor+(order-seq)]
        if points is None or len(matches) != 1:
            break
        proof = _later_stroke_plan(order, raw, [(source, points)], matches, primitives, model, factor)
        if proof is None:
            break
        proof['source_fill_anchor_seqno'] = seq
        proof['svg_fill_anchor_ordinal'] = anchor
        result[order] = proof
    return result


def plan_compound_fills(page, page_data, source_sha256, *, user_scale=1., flip_y=True,
                        later_rectangle_plans=()):
    """No group-name inference: source paint, clip and all contours must join."""
    from .triangle_paint_order import _svg_triangles

    if not re.fullmatch('[0-9a-f]{64}', source_sha256):
        raise ValueError('Invalid compound source identity')
    if not math.isfinite(user_scale) or user_scale <= 0:
        raise ValueError('Invalid compound source scale')
    compounds = {}
    for p in page_data.primitives:
        if p.clip_fill_group_id:
            compounds.setdefault(p.clip_fill_group_id, []).append(p)
    if not compounds or page.rotation or tuple(page.rect)[:2] != (0., 0.):
        return [], []
    try:
        _source_group_declarations(page)
    except ValueError:
        return [], []
    svg = page.get_svg_image(text_as_path=False)
    svg_fills = _svg_covered_clips(svg)
    svg_strokes = _svg_triangles(svg)
    drawings = page.get_drawings()
    raw = {row['seqno']: row for row in drawings}
    if len(raw) != len(drawings):
        raise ValueError('Ambiguous compound source paint')
    log = [(kind, _box(box)) for kind, box in page.get_bboxlog()]
    active, contexts, groups = [], {}, []
    for row in page.get_drawings(extended=True):
        level = row.get('level', 0)
        active = [old for old in active if old.get('level', 0) < level]
        if row['type'] in ('clip', 'group'):
            active.append(row)
            if row['type'] == 'group' and (row.get('blendmode', 'Normal') != 'Normal'
                    or row.get('opacity', 1) != 1 or row.get('knockout', False)):
                groups.append(row)
        elif 'seqno' in row:
            contexts[row['seqno']] = active[:]
    factor = 25.4/72*user_scale
    model = lambda p: (p[0]*factor, (page.rect.y1-p[1] if flip_y else p[1])*factor)
    stroke_rows = [(row, p) for row in drawings if (p := _raw_stroke_points(row)) is not None]
    segment_rows = [(row, p) for row in drawings if (p := _raw_stroke_segments(row)) is not None]
    stroke_index = _JoinIndex(stroke_rows, lambda pair: _match_bounds(pair[1]))
    segment_index = _JoinIndex(segment_rows, lambda pair: _match_bounds(p for s in pair[1] for p in s))
    svg_index = _JoinIndex(svg_strokes, lambda r: _match_bounds(r['points'], r['point_errors']))
    stroke_cache, envelope_cache = {}, {}
    plans, unresolved = [], []
    rectangle_plans = {}
    for mask in later_rectangle_plans:
        proof = mask['source_proof']
        order = proof['source_draw_order']
        if (order in rectangle_plans or proof['source_sha256'] != source_sha256
                or proof['page'] != page_data.page_number):
            raise ValueError('Compound later rectangle proof identity is ambiguous')
        rectangle_plans[order] = mask
    for members in compounds.values():
        seq = members[0].source_draw_order
        row = raw.get(seq)
        record = {'page': page_data.page_number, 'source_draw_order': seq, 'status': 'unqualified'}
        if (type(seq) is not int or row is None or row.get('type') != 'f'
                or row.get('fill_opacity') != 1 or row.get('fill') is None
                or len(row['items']) != 1 or row['items'][0][0] != 're'
                or seq >= len(log) or log[seq][0] != 'fill-path'):
            continue
        clips = [c for c in contexts.get(seq, ()) if c['type'] == 'clip']
        if len(clips) != 1 or clips[0].get('even_odd') is not True:
            continue
        contours = _contours(clips[0].get('items', ()))
        if contours is None or len(contours) != len(members):
            continue
        rect = tuple(row['items'][0][1])
        quad = [(rect[0], rect[1]), (rect[2], rect[1]), (rect[2], rect[3]), (rect[0], rect[3])]
        if not all(_contains(quad, contour) for contour in contours):
            continue
        matches = [s for s in svg_fills if _source_points_match(s, quad)
                   and _close(s['rgb'], row['fill']) and len(s['contours']) == len(contours)
                   and all(sum(_source_points_match(a, b) for a in s['contours']) == 1 for b in contours)
                   and all(sum(_source_points_match(a, b) for b in contours) == 1 for a in s['contours'])]
        peers = [r for r in drawings if r.get('type') == 'f' and r.get('fill') is not None
                 and len(r['items']) == 1 and r['items'][0][0] == 're'
                 and _close(tuple(r['items'][0][1]), rect) and _close(r['fill'], row['fill'])]
        joins = [[i for i, c in enumerate(contours) if _same_cycle(p.points, [model(v) for v in c])]
                 for p in members]
        if (len(matches) != 1 or len(peers) != 1 or any(len(j) != 1 for j in joins)
                or len({j[0] for j in joins}) != len(contours)
                or len({p.id for p in members}) != len(members)
                or any(p.source_draw_order != seq or p.fill_opacity != 1 or not p.clip_fill_even_odd
                       or p.source_fill_color is None or not _close(p.source_fill_color, row['fill']) for p in members)):
            continue
        region = _bounds(p for c in contours for p in c)
        regions = {region}
        rectangle_regions = {region}
        compound_bounds = region
        anchored = _anchored_strokes(seq, matches[0]['paint_ordinal'], log, raw, svg_strokes,
                                     page_data.primitives, model, factor)
        following, later_masks, blocker = {}, {}, None
        while True:
            previous, exclusions = set(regions), []
            for order in range(seq+1, len(log)):
                kind, box = log[order]
                if kind == 'ignore-text' or not any(_intersects(part, box) for part in regions):
                    continue
                if (not any(_intersects(part, box) for part in rectangle_regions)
                        and all(_round_stroke_misses_box(s, box) for s in following.values())):
                    exclusions.append({'seqno': order, 'owned_round_strokes_disjoint': sorted(following)})
                    continue
                if order in rectangle_plans and kind == 'fill-path' and not rectangle_plans[order]['has_border']:
                    mask = rectangle_plans[order]
                    # This exact later source paint has its own complete,
                    # unchanged rectangle/text consumer. It must apply later;
                    # it is not erased or folded into the black paint cohort.
                    later_masks[order] = mask
                    b = tuple(mask['source_proof']['paint_bounds_pdf'])
                    regions.add(b)
                    rectangle_regions.add(b)
                    region = (min(region[0], b[0]), min(region[1], b[1]),
                              max(region[2], b[2]), max(region[3], b[3]))
                    continue
                if any(order == text['seqno'] and kind == 'fill-text'
                       for mask in later_masks.values() for text in mask['later_text']):
                    continue
                source = _source_path_row(raw, order, kind)
                proofs = [[p for c in (contexts.get(source['seqno'], ()) if source else ())
                           if c['type'] == 'clip' and (p := _clip_excludes_region(c, part)) is not None]
                          for part in sorted(regions)]
                if proofs and all(proofs):
                    exclusions.append({'seqno': order, 'clips_by_dependency_region': proofs})
                    continue
                owned = None
                if kind == 'stroke-path':
                    if order not in envelope_cache:
                        envelope_cache[order] = _stroke_envelope_proof(source, segment_index, svg_index)
                    envelope = envelope_cache[order]
                    if envelope is not None and all(not _intersects(b, part) for b in envelope['bounds'] for part in regions):
                        exclusions.append({'seqno': order, 'swept_bounds': envelope})
                        continue
                    if order in anchored:
                        owned = anchored[order]
                    elif order not in stroke_cache:
                        stroke_cache[order] = _later_stroke_plan(order, raw, stroke_index, svg_index,
                                                               page_data.primitives, model, factor)
                    if owned is None:
                        owned = stroke_cache.get(order)
                if (owned and not owned['closed'] and len(owned['points_mm']) == 2
                        and _close(owned['rgb'], row['fill']) and len(following) < 64):
                    if later_masks and order > min(later_masks):
                        blocker = {'seqno': order, 'kind': kind, 'reason': 'stroke_follows_separate_later_mask'}
                        break
                    following[order] = owned
                    b = tuple(owned['source_bounds_pdf'])
                    regions.add(b)
                    region = (min(region[0], b[0]), min(region[1], b[1]), max(region[2], b[2]), max(region[3], b[3]))
                else:
                    blocker = {'seqno': order, 'kind': kind}
                    break
            if blocker or regions == previous:
                break
        if blocker:
            unresolved.append(dict(record, reason='later_source_paint_overlaps_compound', blocker=blocker))
            continue
        if (any(_intersects(region, tuple(g['rect'])) for g in groups)
                or not _contains([(0, 0), (page.rect.x1, 0), (page.rect.x1, page.rect.y1), (0, page.rect.y1)],
                                 [(region[0], region[1]), (region[2], region[3])])):
            continue
        # Border cohorts must contact the actual owned contour, not its bbox.
        disconnected = _disconnected_strokes([[model(p) for p in c] for c in contours], following.values())
        if disconnected:
            unresolved.append(dict(record, reason='later_stroke_does_not_contact_compound', source_orders=disconnected))
            continue
        plans.append({'page': page_data.page_number, 'source_draw_order': seq,
                      'contours': [{'primitive_id': p.id, 'points_mm': list(p.points)} for p in members],
                      'fill_rgb': list(row['fill']), 'even_odd': True,
                      'dependency_bounds_mm': _bounds([model(region[:2]), model(region[2:])]),
                      'compound_bounds_mm': _bounds([model(compound_bounds[:2]), model(compound_bounds[2:])]),
                      'dependency_regions_mm': [_bounds([model(b[:2]), model(b[2:])]) for b in sorted(regions)],
                      'later_strokes': [following[k] for k in sorted(following)],
                      'later_rectangles': [later_masks[k] for k in sorted(later_masks)],
                      'later_unowned_paint_absent': True, 'later_exclusions': exclusions,
                      'source_sha256': source_sha256, 'source_svg_sha256': hashlib.sha256(svg.encode()).hexdigest()})
    return plans, unresolved


def _native_compound(spec, plan, members, graph):
    from .fill_paint_order import same_native_object
    from .image_paint_order import _world_corners
    from .visual_style import preview_color

    obj = spec['object']
    expected = {c['primitive_id']: c['points_mm'] for c in plan['contours']}
    if (len(expected) != len(plan['contours']) or set(spec['primitive_ids']) != set(expected)
            or len(spec['primitive_ids']) != len(expected)
            or spec['source_draw_order'] != plan['source_draw_order'] or spec['fill_opacity'] != 1
            or spec['even_odd'] is not True or not _close(spec['fill_rgb'], plan['fill_rgb'])
            or not any(same_native_object(obj, member) for member in members)
            or obj.type != 'CURVE' or obj.parent is not None or obj.modifiers or obj.constraints
            or obj.hide_render or obj.data.dimensions != '2D' or obj.data.fill_mode != 'BOTH'
            or obj.data.bevel_depth != 0 or obj.data.extrude != 0
            or len(obj.data.splines) != len(expected)):
        raise ValueError('Compound fill native ownership or source style changed')
    for identity, contour, spline in zip(spec['primitive_ids'], spec['contours_mm'], obj.data.splines, strict=True):
        points = list(contour)
        if points[-1] == points[0]:
            points.pop()
        actual = [obj.matrix_world @ p.co.to_3d() for p in spline.points]
        if (not _same_cycle(points, expected[identity]) or spline.type != 'POLY'
                or not spline.use_cyclic_u or len(actual) != len(points)
                or any(p.co[2] != 0 or p.co[3] != 1 for p in spline.points)
                or getattr(spline, 'material_index', 0) != 0
                or not _close([v for p in actual for v in (p.x, p.y)], [v*.001 for p in points for v in p])
                or not _close([p.z for p in actual], [actual[0].z]*len(actual))):
            raise ValueError('Compound fill complete source contours changed')
    if len(obj.data.materials) != 1:
        raise ValueError('Compound fill material ownership changed')
    material = obj.data.materials[0]
    rgb = preview_color(spec['fill_rgb'], spec['visual_style'])
    nodes = {n.type: n for n in material.node_tree.nodes} if material.use_nodes else {}
    if (not material.use_nodes or len(material.node_tree.nodes) != 2
            or set(nodes) != {'EMISSION', 'OUTPUT_MATERIAL'}
            or not _close(material.diffuse_color, (*rgb, 1.))
            or not _close(nodes['EMISSION'].inputs['Color'].default_value, (*rgb, 1.))
            or nodes['EMISSION'].inputs['Strength'].default_value != 1
            or len(material.node_tree.links) != 1
            or material.node_tree.links[0].from_socket != nodes['EMISSION'].outputs['Emission']
            or material.node_tree.links[0].to_socket != nodes['OUTPUT_MATERIAL'].inputs['Surface']):
        raise ValueError('Compound fill actual opaque emission changed')
    paint = _world_corners(obj, graph)
    if not paint or not _close([p.z for p in paint], [paint[0].z]*len(paint)):
        raise ValueError('Compound fill has no planar evaluated geometry')
    return paint


def _native_round_stroke_profile(obj, proof, paint):
    """A local bevel radius certifies world width only for this exact profile."""
    matrix = [tuple(float(v) for v in row) for row in obj.matrix_world]
    data = obj.data
    if (len(matrix) != 4 or any(len(row) != 4 for row in matrix)
            or any(not math.isfinite(v) for row in matrix for v in row)
            or any(matrix[r][c] != float(r == c) for r in range(3) for c in range(3))
            or matrix[3] != (0., 0., 0., 1.) or data.dimensions != '3D'
            or data.bevel_mode != 'ROUND' or data.bevel_object is not None
            or data.taper_object is not None or data.offset != 0 or data.extrude != 0
            or data.bevel_factor_start != 0 or data.bevel_factor_end != 1
            or len(data.splines) != 1 or len(data.splines[0].points) != 2
            or any(p.radius != 1 or p.tilt != 0 for p in data.splines[0].points)):
        raise ValueError('Compound border world round-stroke profile changed')
    a, b = [tuple(v*.001 for v in point) for point in proof['points_mm']]
    radius = proof['width_mm']*.0005
    # The builder uses float32 coordinates. The geometric distance operation
    # propagates their endpoint/vertex and radius roundoff conservatively.
    for point in paint:
        p = (point.x, point.y)
        budget = 8*sum(_ulp32(v) for row in (a, b, p) for v in row) + 8*_ulp32(radius)
        if _point_segment_distance(p, a, b) > radius+budget:
            raise ValueError('Compound evaluated border leaves its source capsule')


def _actual_paint_vertices(obj, graph):
    """Keep actual evaluated vertices; bounding-box corners are not paint."""
    evaluated = obj.evaluated_get(graph)
    matrix = evaluated.matrix_world.copy()
    try:
        mesh = evaluated.to_mesh()
        if mesh is None:
            raise ValueError('Compound stroke has no evaluated mesh')
        points = [matrix @ vertex.co for vertex in mesh.vertices]
        if not points or any(not math.isfinite(v) for point in points for v in point):
            raise ValueError('Compound stroke has no finite evaluated paint')
        return points
    finally:
        evaluated.to_mesh_clear()


def _paint_inside_box(paint, bounds):
    x0, y0, x1, y1 = (v*.001 for v in bounds)
    return bool(paint) and _contains([(x0,y0), (x1,y0), (x1,y1), (x0,y1)],
                                   [(p.x,p.y) for p in paint])


def apply_compound_fills(plans, collection, config):
    """Move only exact editable source paint, with one cohesive border plane."""
    if not plans:
        return []
    import bpy
    from mathutils import Vector
    from .fill_paint_order import same_native_object, validate_source_fill
    from .image_paint_order import _world_corners, _local_geometry, _move_display_z
    from .triangle_paint_order import _validate_following_stroke

    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    members = list(collection.all_objects)
    result = []
    identities = [s['primitive_id'] for p in plans for s in p['later_strokes']]
    for plan in plans:
        row = {'page': plan['page'], 'source_draw_order': plan['source_draw_order'], 'status': 'unqualified'}
        if plan.get('later_unowned_paint_absent') is not True:
            raise ValueError('Compound fill has no later-paint closure proof')
        if any(identities.count(s['primitive_id']) != 1 for s in plan['later_strokes']):
            result.append(dict(row, reason='later_stroke_shared_by_compound_plans'))
            continue
        expected = {c['primitive_id'] for c in plan['contours']}
        selected = [s for s in config.get('_source_compound_fill_objects', ()) if set(s['primitive_ids']) == expected]
        if len(selected) != 1:
            result.append(dict(row, reason='native_compound_fill_not_uniquely_owned'))
            continue
        spec, region = selected[0], tuple(v*.001 for v in plan['dependency_bounds_mm'])
        regions = [tuple(v*.001 for v in box) for box in plan.get('dependency_regions_mm', [plan['dependency_bounds_mm']])]
        obj = spec['object']
        paint = _native_compound(spec, plan, members, graph)
        if not _paint_inside_box(paint, plan.get('compound_bounds_mm', plan['dependency_bounds_mm'])):
            raise ValueError('Compound native paint exceeds its own source footprint')
        future = []
        for mask in plan.get('later_rectangles', ()):
            future_spans = {span for event in mask['later_text'] for span in event['span_ids']}
            # This phase runs before text construction. Do not accidentally
            # treat a future text extrusion as earlier paint if called later.
            if any(member.get('pdf_source_span_id') in future_spans for member in members):
                raise ValueError('Compound ordering must precede later mask text construction')
            matches = [s for s in config.get('_source_fill_objects', ())
                       if s['primitive_id'] == mask['primitive_id']]
            if (len(matches) != 1 or matches[0]['source_draw_order'] != mask['source_proof']['source_draw_order']
                    or matches[0]['fill_opacity'] != 1 or not _same_cycle(matches[0]['points_mm'], mask['points_mm'])
                    or not _close(matches[0]['fill_rgb'], mask['fill_rgb'])):
                raise ValueError('Later compound rectangle native ownership changed')
            validate_source_fill(matches[0], members, graph)
            if not _paint_inside_box(_world_corners(matches[0]['object'], graph), mask['dependency_bounds_mm']):
                raise ValueError('Later compound rectangle leaves its own source footprint')
            future.append(matches[0]['object'])
            if mask['has_border']:
                # A later border would also need native/source ownership here.
                # Keep this bounded dependency path to fill-only text masks.
                raise ValueError('Later compound rectangle border is unsupported')
        own = [(obj, _local_geometry(obj), paint)]
        planes = []
        for proof in plan['later_strokes']:
            rows = config.get('_image_order_stroke_objects', {}).get(proof['primitive_id'], ())
            if len(rows) != 1:
                raise ValueError('Compound border native occurrence is not unique')
            stroke = rows[0]
            local, bounds = _validate_following_stroke(stroke, proof, members, spec['visual_style'], graph)
            _native_round_stroke_profile(stroke, proof, _actual_paint_vertices(stroke, graph))
            own.append((stroke, local, bounds))
            planes.append((stroke.matrix_world @ Vector(tuple(stroke.data.splines[0].points[0].co)[:3])).z)
        if planes and not _close(planes, [planes[0]]*len(planes)):
            raise ValueError('Compound border source planes differ')
        x0, y0, x1, y1 = region
        if not all(_contains([(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                             [(p.x, p.y) for p in bounds]) for _, _, bounds in own):
            raise ValueError('Compound native paint exceeds source dependency')
        top = -math.inf
        for member in members:
            if (member.hide_render or any(same_native_object(member, own_obj) for own_obj, _, _ in own)
                    or any(same_native_object(member, f) for f in future)):
                continue
            bounds = _world_corners(member, graph)
            if bounds and any(_intersects(part, _bounds((p.x, p.y) for p in bounds)) for part in regions):
                top = max(top, max(p.z for p in bounds))
        top = top if math.isfinite(top) else 0.
        plane = planes[0] if planes else paint[0].z
        alignment = plane-paint[0].z
        bottom = min(plane, *(p.z for _, _, bounds in own[1:] for p in bounds)) if planes else plane
        delta = max(0., top+.00005-bottom)
        moves = [(obj, own[0][1], paint, alignment+delta)] + [(*entry, delta) for entry in own[1:]]
        for member, _, _, offset in moves:
            _move_display_z(member, float(member.location.z)+offset)
        bpy.context.view_layer.update()
        graph = bpy.context.evaluated_depsgraph_get()
        for member, local, before, offset in moves:
            after = _world_corners(member, graph)
            if (_local_geometry(member) != local or not after or min(p.z for p in after) <= top
                    or not _close(_bounds((p.x, p.y) for p in before), _bounds((p.x, p.y) for p in after))):
                raise ValueError('Compound source geometry or earlier-paint clearance changed')
            member['bcs_compound_paint_display_z_offset_m'] = offset
        row.update(status='applied', source_sha256=plan['source_sha256'],
                   policy='complete_opaque_clip_and_owned_border_source_plane',
                   native_object=obj.name, source_plane_display_z_m=plane+delta,
                   earlier_native_top_m=top, source_proof=plan,
                   moves=[{'object': member.name, 'offset_m': offset} for member, _, _, offset in moves])
        result.append(row)
        config.setdefault('_required_compound_later_rectangle_orders', set()).update(
            mask['source_proof']['source_draw_order'] for mask in plan.get('later_rectangles', ()))
    return result


def verify_required_later_masks(config, results):
    required = config.get('_required_compound_later_rectangle_orders', set())
    applied = {row['source_proof']['source_draw_order'] for row in results
               if row.get('status') == 'applied'}
    if not required <= applied:
        raise ValueError('Compound later rectangle/text dependency did not apply')
