"""Bind proven source masks to retained native faces and later native text.

Only display Z changes. The original fill, border and text representation stay
editable, with their original local coordinates and source XY unchanged.
"""
from __future__ import annotations

import json
import math

from .image_paint_order import _bounds, _close, _contains, _intersects
from .fill_paint_order import same_native_object


def _same_cycle(actual, expected):
    actual, expected = list(actual), list(expected)
    if len(actual) > 1 and actual[0] == actual[-1]:
        actual.pop()
    if len(expected) > 1 and expected[0] == expected[-1]:
        expected.pop()
    if len(actual) != len(expected):
        return False
    for sequence in (actual, list(reversed(actual))):
        if any(all(_close(sequence[(i + start) % len(sequence)], point)
                   for i, point in enumerate(expected)) for start in range(len(sequence))):
            return True
    return False


def bind_rectangle_plans(proofs, page_data, page_rect, *, user_scale=1.0, flip_y=True):
    """Join original occurrence/character coordinates, never guessed item IDs."""
    if not math.isfinite(user_scale) or user_scale <= 0:
        raise ValueError("Invalid opaque rectangle source scale")
    x0, y0, x1, y1 = tuple(page_rect)
    factor = 25.4 / 72.0 * user_scale

    def model(point):
        return ((point[0] - x0) * factor,
                (y1 - point[1] if flip_y else point[1] - y0) * factor)

    primitives = {}
    for primitive in page_data.primitives:
        primitives.setdefault(primitive.source_draw_order, []).append(primitive)
    plans, unresolved = [], []
    for proof in proofs:
        sequence = proof['source_draw_order']
        record = {'page': page_data.page_number, 'source_draw_order': sequence,
                  'status': 'unqualified'}
        matches = primitives.get(sequence, ())
        if len(matches) != 1:
            record['reason'] = 'source_mask_primitive_not_uniquely_bound'
            unresolved.append(record)
            continue
        primitive = matches[0]
        expected = [model(point) for point in proof['source_quad_pdf']]
        if (primitive.type not in {'rect', 'closed_loop'} or primitive.clip_fill_group_id
                or primitive.fill_opacity != 1 or primitive.source_fill_color is None
                or not _close(primitive.source_fill_color, proof['fill_rgb'])
                or not _same_cycle(primitive.points, expected)
                or primitive.dash_pattern
                or (proof['stroke_rgb'] is not None and (
                    primitive.stroke_opacity != 1 or primitive.source_stroke_color is None
                    or not _close(primitive.source_stroke_color, proof['stroke_rgb'])
                    or not _close((primitive.line_width,), (proof['stroke_width'] * factor,))))):
            record['reason'] = 'source_mask_geometry_or_paint_binding_unsupported'
            unresolved.append(record)
            continue
        later, eligible = [], True
        for event in proof['later_text']:
            ids = []
            for source_item in event['items']:
                candidates = []
                chars = source_item['chars']
                for item in page_data.text_items:
                    layout = item.source_char_layout
                    if (item.text == source_item['text'] and len(layout) == len(chars)
                            and item.source_bbox_pdf is not None
                            and _close(item.source_bbox_pdf, source_item['bbox_pdf'])
                            and all(len(char.text) == 1 and ord(char.text) == code
                                    and _close(char.source_origin_pdf, origin)
                                    for char, (code, origin) in zip(layout, chars, strict=True))):
                        candidates.append(item.id)
                if len(candidates) != 1:
                    eligible = False
                    break
                ids.append(candidates[0])
            if not eligible:
                break
            later.append({'seqno': event['source_paint_order'], 'span_ids': ids})
        if not eligible:
            record['reason'] = 'later_source_text_not_uniquely_bound'
            unresolved.append(record)
            continue
        bx0, by0, bx1, by1 = proof['paint_bounds_pdf']
        plans.append({'primitive_id': primitive.id, 'page': page_data.page_number,
                      'source_proof': proof, 'points_mm': [tuple(p) for p in primitive.points],
                      'fill_rgb': list(proof['fill_rgb']),
                      'has_border': proof['stroke_rgb'] is not None,
                      'later_text': later,
                      'dependency_bounds_mm': _bounds([model((bx0, by0)), model((bx1, by1))])})
    return plans, unresolved


def _matrix_values(matrix):
    values = tuple(tuple(float(value) for value in row) for row in matrix)
    if len(values) != 4 or any(len(row) != 4 for row in values):
        raise ValueError('Invalid native display matrix')
    if not all(math.isfinite(value) for row in values for value in row):
        raise ValueError('Nonfinite native display matrix')
    return values


def _translate_owned_text(obj, offset):
    """Translate an owned carrier when present, retaining the child's affine."""
    if not math.isfinite(offset) or obj.modifiers or obj.constraints:
        raise ValueError('Unsupported native mask text transform')
    carrier = obj.parent
    target = obj
    if carrier is not None:
        if (not obj.get('pdf_affine_carrier_owned')
                or obj.get('pdf_affine_carrier') != carrier.name
                or carrier.type != 'EMPTY' or carrier.parent is not None
                or carrier.modifiers or carrier.constraints
                or len(carrier.children) != 1 or not same_native_object(carrier.children[0], obj)):
            raise ValueError('Native mask text carrier ownership changed')
        target = carrier
    before = _matrix_values(target.matrix_world)
    child_basis = _matrix_values(obj.matrix_basis) if carrier is not None else None
    parent_inverse = _matrix_values(obj.matrix_parent_inverse) if carrier is not None else None
    data = obj.data
    changed = target.matrix_world.copy()
    changed[2][3] += offset
    target.matrix_world = changed
    after = _matrix_values(target.matrix_world)
    if any(not _close((after[r][c],), (before[r][c] + (offset if (r, c) == (2, 3) else 0),))
           for r in range(4) for c in range(4)):
        raise ValueError('Native text display move changed source affine')
    if (obj.data != data or obj.parent != carrier
            or (carrier is not None and (
                _matrix_values(obj.matrix_basis) != child_basis
                or _matrix_values(obj.matrix_parent_inverse) != parent_inverse))):
        raise ValueError('Native text display move changed source data or parent')
    obj['pdf_mask_order_display_offset_m'] = float(obj.get('pdf_mask_order_display_offset_m', 0)) + offset


def _native_text_for_plan(plan, delivery_records, objects):
    """Require the complete successful per-item delivery's exact entity set."""
    result, used = [], set()
    for event in plan['later_text']:
        event_objects = []
        for span in event['span_ids']:
            records = [row for row in delivery_records if row.get('page') == plan['page']
                       and row.get('source_span_id') == span]
            if len(records) != 1 or records[0].get('status') != 'delivered':
                return None
            record = records[0]
            names = record.get('entity_ids', ())
            if not names or len(set(names)) != len(names):
                return None
            actual = [obj for obj in objects.values() if obj.get('pdf_source_span_id') == span
                      and obj.get('pdf_source_item_id') == record.get('item_id')]
            if {obj.name for obj in actual} != set(names):
                raise ValueError('Later native mask text ownership is incomplete')
            for name in names:
                obj = objects.get(name)
                if (obj is None or name in used or obj.type not in {'FONT', 'CURVE', 'MESH'}
                        or obj.hide_render or obj.get('pdf_text_mode') != record.get('final_representation')):
                    raise ValueError('Later native mask text changed representation or visibility')
                used.add(name)
                event_objects.append(obj)
        result.append((event['seqno'], event_objects))
    return result


def apply_rectangle_order(plans, collection, builder_config, delivery_records):
    """Lift only source-qualified masks with complete native paint ownership."""
    if not plans:
        return []
    import bpy
    from .fill_paint_order import validate_source_fill
    from .image_paint_order import _move_display_z, _verify_native_stroke, _world_corners

    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    objects = {obj.name: obj for obj in collection.all_objects}
    source_fills = {}
    for spec in builder_config.get('_source_fill_objects', ()):
        source_fills.setdefault(spec['primitive_id'], []).append(spec)
    source_borders = builder_config.get('_image_order_stroke_objects', {})
    # Disjoint masks can share a display depth. Measure once, then update only
    # moved native objects, so a page of separate labels is not a tall Z stack.
    measured = {obj.name: _world_corners(obj, graph) for obj in objects.values()
                if obj.type in {'FONT', 'CURVE', 'MESH'} and not obj.hide_render}
    records = []
    for plan in sorted(plans, key=lambda p: p['source_proof']['source_draw_order']):
        record = {'page': plan['page'], 'primitive_id': plan['primitive_id'],
                  'status': 'unqualified', 'source_proof': plan['source_proof']}
        records.append(record)
        fills = source_fills.get(plan['primitive_id'], ())
        if len(fills) != 1:
            record['reason'] = 'source_mask_has_no_unique_native_fill'
            continue
        spec = fills[0]
        face = spec['object']
        validate_source_fill(spec, objects.values(), graph)
        if spec['fill_opacity'] != 1 or not _close(spec['fill_rgb'], plan['fill_rgb']):
            raise ValueError('Source mask native fill binding changed')
        groups = _native_text_for_plan(plan, delivery_records, objects)
        if groups is None:
            record['reason'] = 'later_text_delivery_not_complete'
            continue
        borders = []
        if plan['has_border']:
            borders = list(source_borders.get(plan['primitive_id'], ()))
            if len(borders) != 1:
                record['reason'] = 'source_mask_has_no_unique_native_border'
                continue
            if not same_native_object(objects.get(borders[0].name), borders[0]):
                raise ValueError('Source mask border left its owned page collection')
            _verify_native_stroke(borders[0], {'closed': True, 'points_mm': plan['points_mm']})
        x0, y0, x1, y1 = plan['dependency_bounds_mm']
        dependency = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        participants = [face, *borders, *(obj for _, group in groups for obj in group)]
        for obj in participants:
            points = _world_corners(obj, graph)
            if not points:
                # Whitespace has no paint; it remains owned and moves with text.
                if obj.type == 'FONT' and not obj.data.body.strip():
                    continue
                record['reason'] = 'native_mask_paint_has_no_evaluated_geometry'
                break
            if not _contains(dependency, [(p.x * 1000, p.y * 1000) for p in points]):
                record['reason'] = 'native_mask_paint_exceeds_source_dependency_bounds'
                break
        if record.get('reason'):
            continue
        # Validate all carriers before moving the first face.
        for _, group in groups:
            for obj in group:
                if obj.parent is not None and not same_native_object(objects.get(obj.parent.name), obj.parent):
                    raise ValueError('Source mask text carrier left its owned page collection')
                _translate_owned_text(obj, 0.0)
        top = max((point.z for points in measured.values() if points
                   and _intersects((x0, y0, x1, y1),
                                   _bounds([(p.x * 1000, p.y * 1000) for p in points]))
                   for point in points), default=0.0)

        def raise_group(group, preceding, text=False):
            points = [point for obj in group for point in _world_corners(obj, graph)]
            if not points:
                return preceding
            offset = preceding + 0.00005 - min(point.z for point in points)
            before_xy = {obj.name: [(p.x, p.y) for p in _world_corners(obj, graph)] for obj in group}
            for obj in group:
                if text:
                    _translate_owned_text(obj, offset)
                else:
                    _move_display_z(obj, float(obj.location.z) + offset)
            bpy.context.view_layer.update()
            for obj in group:
                measured[obj.name] = _world_corners(obj, graph)
            actual = [point for obj in group for point in measured[obj.name]]
            for obj in group:
                after_xy = [(p.x, p.y) for p in measured[obj.name]]
                if len(after_xy) != len(before_xy[obj.name]) or not all(
                    _close(a, b) for a, b in zip(before_xy[obj.name], after_xy, strict=True)
                ):
                    raise ValueError('Native mask display move changed source XY')
            if min(point.z for point in actual) <= preceding:
                raise ValueError('Native mask failed to clear earlier painted geometry')
            return max(point.z for point in actual)

        top = raise_group([face], top)
        if borders:
            top = raise_group(borders, top)
        for _, group in groups:
            top = raise_group(group, top, text=True)
        face['pdf_opaque_mask_source_proof'] = json.dumps(plan['source_proof'], sort_keys=True)
        record.update(status='applied', native_fill=face.name,
                      native_border=[obj.name for obj in borders],
                      later_native_text=[{'seqno': seq, 'objects': [obj.name for obj in group]}
                                         for seq, group in groups],
                      final_display_top_m=top)
    return records
