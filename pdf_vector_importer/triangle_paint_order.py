"""Source-proven terminal triangle fills above earlier compound clip fills.

Only opaque, fully unclipped triangles and a bounded set of uniquely owned
later straight lines can cross the existing native Curve/Mesh depth bands.
Other intersecting later paint rejects the plan. Source geometry stays unchanged.
"""
from __future__ import annotations

import hashlib
import bisect
import json
import math
import re
import xml.etree.ElementTree as ET

from .image_paint_order import (
    _bounds, _box, _close, _contains, _intersects, _matrix, _multiply,
    _neutral, _clip_quad,
    _ulp32,
)
from .opaque_rectangle_order import _same_cycle
from .opaque_rectangle_proof import _clip_excludes_region, _source_path_row, _segments_intersect
from .nontext_composite import _source_group_declarations
from .pdf_paint_proof import _rgb


def _straight_path(value):
    """Read one absolute straight SVG subpath, rejecting every other command."""
    token = r'[MLHVZ]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?'
    words = re.findall(token, value)
    if re.sub(token, '', value).strip(' ,\t\r\n') or not words or words[0] != 'M':
        return None
    points, i, command = [], 0, None
    try:
        while i < len(words):
            if words[i] in {'M', 'L', 'H', 'V', 'Z'}:
                command, i = words[i], i + 1
                if command == 'Z':
                    if i != len(words):
                        return None
                    break
                if command == 'M' and points:
                    return None
            if command in ('M', 'L'):
                point = (float(words[i]), float(words[i + 1]))
                i += 2
                command = 'L'
            elif command == 'H' and points:
                point, i = (float(words[i]), points[-1][1]), i + 1
            elif command == 'V' and points:
                point, i = (points[-1][0], float(words[i])), i + 1
            else:
                return None
            points.append(point)
    except (IndexError, ValueError):
        return None
    if len(points) > 1 and points[-1] == points[0]:
        points.pop()
    if len(points) < 2 or not all(math.isfinite(v) for p in points for v in p):
        return None
    return points


def _triangle_path(value):
    points = _straight_path(value)
    if (points is None or len(points) != 3
            or (points[1][0]-points[0][0])*(points[2][1]-points[0][1])
            == (points[1][1]-points[0][1])*(points[2][0]-points[0][0])):
        return None
    return points  # PDF/SVG fills implicitly close this one subpath.


def _stroke_segments(value):
    """One absolute line/cubic subpath; controls are bounds, never tessellation."""
    token = r'[MLHVCZ]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?'
    words = re.findall(token, value)
    if re.sub(token, '', value).strip(' ,\t\r\n') or not words or words[0] != 'M':
        return None
    segments, point, first, command, i = [], None, None, None, 0
    try:
        while i < len(words):
            if words[i] in {'M', 'L', 'H', 'V', 'C', 'Z'}:
                command, i = words[i], i+1
                if command == 'Z':
                    if i != len(words) or point is None:
                        return None
                    if point != first:
                        segments.append((point, first))
                    break
                if command == 'M' and first is not None:
                    return None
            if command in ('M', 'L'):
                end = (float(words[i]), float(words[i+1]))
                i += 2
                if command == 'M':
                    first = end
                elif point is not None:
                    segments.append((point, end))
                else:
                    return None
                point, command = end, 'L'
            elif command in ('H', 'V') and point is not None:
                end = (float(words[i]), point[1]) if command == 'H' else (point[0], float(words[i]))
                segments.append((point, end))
                point, i = end, i+1
            elif command == 'C' and point is not None:
                controls = tuple((float(words[i+j]), float(words[i+j+1])) for j in (0, 2, 4))
                segments.append((point, *controls))
                point, i = controls[-1], i+6
            else:
                return None
    except (IndexError, ValueError):
        return None
    if not segments or not all(math.isfinite(v) for segment in segments for p in segment for v in p):
        return None
    return segments


def _transformed_point_proof(point, matrices):
    """Propagate the original float32 serialization/operation budget, before cancellation."""
    point = tuple(point)
    error = tuple(4*_ulp32(v) for v in point)
    for matrix in reversed(matrices):
        output, budget = [], []
        for a, b, translation in ((matrix[0], matrix[2], matrix[4]),
                                   (matrix[1], matrix[3], matrix[5])):
            first, second = a*point[0], b*point[1]
            value = first+second+translation
            uncertainty = (abs(a)*error[0] + abs(b)*error[1]
                           + abs(point[0])*4*_ulp32(a) + abs(point[1])*4*_ulp32(b)
                           + 4*sum(_ulp32(v) for v in (translation, first, second, first+second, value)))
            if not math.isfinite(value) or not math.isfinite(uncertainty):
                raise ValueError('Nonfinite SVG transform proof')
            output.append(value)
            budget.append(uncertainty)
        point, error = tuple(output), tuple(budget)
    return point, error


def _source_points_match(svg_row, points, closed=True):
    expected = list(points)
    if len(expected) > 1 and expected[-1] == expected[0]:
        expected.pop()
    if len(expected) != len(svg_row['points']):
        return False
    orders = []
    for sequence in (expected, list(reversed(expected))):
        orders.extend(sequence[i:]+sequence[:i] for i in range(len(sequence))) if closed else orders.append(sequence)
    return any(all(abs(a-b) <= bound+4*_ulp32(b)
                   for actual, limits, wanted in zip(svg_row['points'], svg_row['point_errors'], order, strict=True)
                   for a, bound, b in zip(actual, limits, wanted, strict=True)) for order in orders)


def _match_bounds(points, errors=None):
    """Outward-rounded operand envelopes used only to reject impossible joins."""
    try:
        points = list(points)
        if not points or any(len(p) != 2 for p in points):
            return None
        errors = list(errors) if errors is not None else [tuple(4*_ulp32(v) for v in p) for p in points]
        if len(errors) != len(points) or any(len(e) != 2 for e in errors):
            return None
        if any(not math.isfinite(v) for p in points for v in p) or any(
                not math.isfinite(v) or v < 0 for e in errors for v in e):
            return None
        lower = [tuple(math.nextafter(v-e, -math.inf) for v, e in zip(p, error, strict=True))
                 for p, error in zip(points, errors, strict=True)]
        upper = [tuple(math.nextafter(v+e, math.inf) for v, e in zip(p, error, strict=True))
                 for p, error in zip(points, errors, strict=True)]
        return (min(p[0] for p in lower), min(p[1] for p in lower),
                max(p[0] for p in upper), max(p[1] for p in upper))
    except (TypeError, ValueError, OverflowError):
        return None  # Unknown envelopes must stay in the exact candidate set.


class _JoinIndex:
    """One immutable qualification-call index; exact joins still decide identity."""

    def __init__(self, rows, bounds):
        self.rows = tuple(rows)
        known, unknown = [], []
        for index, row in enumerate(self.rows):
            try:
                box = bounds(row)
            except (KeyError, TypeError, ValueError, OverflowError):
                box = None
            if not self._valid(box):
                unknown.append(index)
            else:
                known.append((box[0], index, tuple(box)))
        self.known = tuple(sorted(known))
        self.starts = tuple(item[0] for item in self.known)
        self.unknown = tuple(unknown)

    @staticmethod
    def _valid(box):
        try:
            return (box is not None and len(box) == 4 and all(math.isfinite(v) for v in box)
                    and box[0] <= box[2] and box[1] <= box[3])
        except (TypeError, ValueError):
            return False

    def candidates(self, box):
        if not self._valid(box):
            return self.rows
        end = bisect.bisect_right(self.starts, box[2])
        selected = list(self.unknown)
        selected.extend(index for _, index, candidate in self.known[:end] if _intersects(candidate, box))
        return tuple(self.rows[index] for index in sorted(selected))


def _join_candidates(rows, box):
    return rows.candidates(box) if isinstance(rows, _JoinIndex) else rows


def _svg_triangles(svg):
    if re.search(r'<\?xml-stylesheet(?:\s|\?>)', svg):
        return []
    root = ET.fromstring(svg)
    if (root.tag.rsplit('}', 1)[-1] != 'svg'
            or not _neutral(root, {'version', 'width', 'height', 'viewBox', 'opacity'})):
        return []
    identified = [node for node in root.iter() if node.get('id')]
    ids = {node.get('id'): node for node in identified}
    if (len(ids) != len(identified) or any(node.tag.rsplit('}', 1)[-1] == 'style'
            or (node is not root and node.tag.rsplit('}', 1)[-1] == 'svg')
            for node in root.iter())):
        return []
    output = []
    paint_ordinal = 0

    def visit(node, matrix, clips, safe, matrices=()):
        nonlocal paint_ordinal
        tag = node.tag.rsplit('}', 1)[-1]
        if tag in {'defs', 'clipPath', 'mask', 'symbol'}:
            return
        ordinal = None
        if tag not in {'svg', 'g'}:
            # Count every live paint/barrier, including rejected paths. Filtered
            # candidate adjacency must never stand in for source adjacency.
            ordinal, paint_ordinal = paint_ordinal, paint_ordinal + 1
        if tag == 'g':
            safe = safe and _neutral(node, {'id', 'transform', 'clip-path', 'opacity'})
            local_matrix = _matrix(node.get('transform'))
            matrix = _multiply(matrix, local_matrix)
            matrices = matrices + (local_matrix,)
            if node.get('clip-path'):
                match = re.fullmatch(r'url\(#([^()]+)\)', node.get('clip-path'))
                try:
                    if not match:
                        raise ValueError('Unknown source clip')
                    clips = clips + [_clip_quad(ids[match[1]], matrix)]
                except (ValueError, KeyError):
                    safe = False
        elif tag == 'path' and safe:
            stroke = node.get('fill') == 'none' and node.get('stroke') is not None
            allowed = {'d', 'transform', 'fill', 'fill-opacity', 'fill-rule'}
            if stroke:
                allowed |= {'stroke', 'stroke-opacity', 'stroke-width', 'stroke-linecap',
                            'stroke-linejoin'}
            if (set(node.attrib) - allowed
                    or float(node.get('fill-opacity', '1')) != 1
                    or (not stroke and node.get('fill', '#000000') == 'none')):
                return
            points = _straight_path(node.get('d', '')) if stroke else _triangle_path(node.get('d', ''))
            segments = _stroke_segments(node.get('d', '')) if stroke else None
            if points is None and segments is None:
                return
            path_matrix = _matrix(node.get('transform'))
            transform = _multiply(matrix, path_matrix)
            proof = [_transformed_point_proof(p, matrices+(path_matrix,))
                     for p in (points if points is not None else [p for s in segments for p in s])]
            points = [p for p, _ in proof]
            bounds = _bounds(points)
            width = 0.
            if stroke:
                # The bounded native outline contract is a solid round join.
                if (node.get('stroke-linejoin') != 'round'
                        or node.get('stroke-linecap') not in ('round', 'butt')
                        or float(node.get('stroke-opacity', '1')) != 1):
                    return
                a, b, c, d, _, _ = transform
                sx, sy = math.hypot(a, b), math.hypot(c, d)
                if not _close((sx, a*c+b*d), (sy, 0.)):
                    return
                width = float(node.get('stroke-width', '0')) * sx
                if not math.isfinite(width) or width <= 0:
                    return
                bounds = (bounds[0]-width/2, bounds[1]-width/2,
                          bounds[2]+width/2, bounds[3]+width/2)
            x0, y0, x1, y1 = bounds
            footprint = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)] if stroke else points
            full_clip = all(_contains(clip, footprint) for clip in clips)
            ordinary_stroke = stroke and _straight_path(node.get('d', '')) is not None and node.get('stroke-linecap') == 'round'
            if full_clip or (stroke and segments):
                # A clipped whole stroke may still supply an overestimate for
                # disjointness. It never qualifies as a movable native line.
                output.append({'points': points, 'kind': ('stroke' if ordinary_stroke and full_clip else 'stroke_bounds') if stroke else 'fill',
                               'rgb': _rgb(node.get('stroke') if stroke else node.get('fill', '#000000')),
                               'width': width, 'paint_bounds': bounds,
                               'paint_ordinal': ordinal, 'matrix': transform,
                               'closed': node.get('d', '').rstrip().endswith('Z'),
                               'point_errors': [error for _, error in proof],
                               'stroke_cap': node.get('stroke-linecap') if stroke else None,
                               'segments': [[_transformed_point_proof(p, matrices+(path_matrix,))
                                             for p in segment] for segment in segments] if segments else None,
                               'clips': tuple(tuple(clip) for clip in clips)})
            return
        elif tag != 'svg':
            return
        for child in node:
            visit(child, matrix, clips, safe, matrices)

    visit(root, (1., 0., 0., 1., 0., 0.), [], True)
    return output


def _raw_triangle(row):
    if row.get('type') not in ('f', 'fs') or row.get('fill_opacity') != 1 or row.get('fill') is None:
        return None
    items = row.get('items', ())
    if not items or any(item[0] != 'l' or len(item) != 3 for item in items):
        return None
    points = [tuple(items[0][1])]
    for _, start, end in items:
        if tuple(start) != points[-1]:
            return None
        points.append(tuple(end))
    if points[-1] == points[0]:
        points.pop()
    if len(points) != 3:
        return None
    if (not all(math.isfinite(v) for p in points for v in p)
            or (points[1][0]-points[0][0])*(points[2][1]-points[0][1])
            == (points[1][1]-points[0][1])*(points[2][0]-points[0][0])):
        return None
    return points


def _raw_stroke_points(row):
    items = row.get('items', ())
    if (row.get('type') not in ('s', 'fs') or len(items) != 1 or row.get('closePath')
            or any(item[0] != 'l' or len(item) != 3 for item in items)):
        return None
    points = [tuple(items[0][1])]
    for _, start, end in items:
        if tuple(start) != points[-1]:
            return None
        points.append(tuple(end))
    if not all(math.isfinite(v) for p in points for v in p) or len(set(points)) < 2:
        return None
    return points


def _raw_stroke_segments(row):
    if row.get('type') not in ('s', 'fs'):
        return None
    segments = []
    for item in row.get('items', ()):
        if not ((item[0] == 'l' and len(item) == 3) or (item[0] == 'c' and len(item) == 5)):
            return None
        segment = tuple(tuple(p) for p in item[1:])
        if any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in segment):
            return None
        if segments and segment[0] != segments[-1][-1]:
            return None
        segments.append(segment)
    if not segments:
        return None
    if row.get('closePath') and segments[-1][-1] != segments[0][0]:
        segments.append((segments[-1][-1], segments[0][0]))
    return segments


def _segments_match(proof, expected):
    return len(proof) == len(expected) and all(
        len(actual) == len(wanted) and all(
            abs(a-b) <= error+4*_ulp32(b)
            for (point, limits), target in zip(actual, wanted, strict=True)
            for a, error, b in zip(point, limits, target, strict=True))
        for actual, wanted in zip(proof, expected, strict=True))


def _stroke_envelope_proof(source, raw_segments, svg_rows):
    """Conservative swept bounds for a uniquely bound uniform round-join stroke."""
    if (source is None or source.get('type') != 's' or source.get('stroke_opacity') != 1
            or source.get('fill') is not None or source.get('color') is None
            or source.get('lineJoin') != 1
            or tuple(source.get('lineCap', ())) not in ((0, 0, 0), (1, 1, 1))
            or source.get('dashes') not in (None, '', '[] 0')):
        return None
    segments = _raw_stroke_segments(source)
    if segments is None:
        return None
    bounds = _match_bounds(point for segment in segments for point in segment)
    matches = [row for row in _join_candidates(svg_rows, bounds) if row['kind'] in ('stroke', 'stroke_bounds')
               and row['segments'] is not None and row['rgb'] is not None and _close(row['rgb'], source['color'])
               and _close((row['width'],), (source.get('width', 0.),))
               and row['stroke_cap'] == ('round' if source['lineCap'][0] == 1 else 'butt')
               and _segments_match(row['segments'], segments)]
    if len(matches) != 1:
        return None
    match = matches[0]
    peer_bounds = _match_bounds((point for segment in match['segments'] for point, _ in segment),
                               (error for segment in match['segments'] for _, error in segment))
    peers = [row for row, path in _join_candidates(raw_segments, peer_bounds)
             if row.get('color') is not None and _close(row['color'], source['color'])
             and _close((row.get('width', 0.),), (source.get('width', 0.),))
             and _segments_match(match['segments'], path)]
    if len(peers) != 1:
        return None
    width = max(match['width'], source['width'])
    if not math.isfinite(width) or width <= 0:
        return None
    radius = width/2+4*_ulp32(width)
    boxes = []
    for proof, original in zip(match['segments'], segments, strict=True):
        low, high = [], []
        for (point, limits), raw_point in zip(proof, original, strict=True):
            low.append(tuple(min(v-e, r-4*_ulp32(r))-radius
                             for v, e, r in zip(point, limits, raw_point, strict=True)))
            high.append(tuple(max(v+e, r+4*_ulp32(r))+radius
                              for v, e, r in zip(point, limits, raw_point, strict=True)))
        boxes.append((min(p[0] for p in low), min(p[1] for p in low),
                      max(p[0] for p in high), max(p[1] for p in high)))
    # Every cubic lies within its control hull. The expanded boxes also contain
    # its circular pen sweep, round joins, and either accepted cap. Clipping can
    # only remove that paint; no sampling or full-bbox-interior assumption occurs.
    return {'bounds': boxes, 'source_draw_order': source['seqno'],
            'svg_paint_ordinal': match['paint_ordinal'], 'width_pdf': source['width'],
            'segment_count': len(boxes),
            'bounds_sha256': hashlib.sha256(json.dumps(boxes).encode()).hexdigest()}


def _bound_round_paint(svg_row, source_points, source_width):
    x0, y0, x1, y1 = _bounds(source_points)
    half = source_width/2
    a, b, c, d = svg_row['paint_bounds']
    region = (min(a, x0-half), min(b, y0-half), max(c, x1+half), max(d, y1+half))
    x0, y0, x1, y1 = region
    if not all(_contains(clip, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]) for clip in svg_row['clips']):
        return None
    return region


def _later_stroke_plan(order, raw, raw_strokes, svg_rows, primitives, model, factor):
    source = raw.get(order)
    points = _raw_stroke_points(source or {})
    if (points is None or source.get('type') != 's'
            or source.get('stroke_opacity') != 1 or source.get('color') is None
            or source.get('lineJoin') != 1 or tuple(source.get('lineCap', ())) != (1, 1, 1)
            or source.get('dashes') not in (None, '', '[] 0')):
        return None
    closed = bool(source.get('closePath') or points[-1] == points[0])
    matches = [row for row in _join_candidates(svg_rows, _match_bounds(points))
               if row['kind'] == 'stroke' and row['closed'] == closed
               and row['rgb'] is not None and _close(row['rgb'], source['color'])
               and _close((row['width'],), (source.get('width', 0.),))
               and _source_points_match(row, points, closed=closed)]
    if len(matches) != 1:
        return None
    match = matches[0]
    # A clipped occurrence cannot borrow another visible occurrence with the
    # same path. Require the raw path join to be injective before clip proof.
    peers = [row for row, path in _join_candidates(raw_strokes, _match_bounds(match['points'], match['point_errors']))
             if row.get('color') is not None and _close(row['color'], source['color'])
             and _close((row.get('width', 0.),), (source.get('width', 0.),))
             and _source_points_match(match, path, closed=closed)]
    selected = [p for p in primitives if p.source_draw_order == order]
    if len(peers) != 1 or len(selected) != 1:
        return None
    primitive = selected[0]
    expected = [model(p) for p in points]
    if (primitive.type not in ('line', 'polyline', 'closed_loop') or primitive.clip_fill_group_id
            or primitive.fill_color is not None or primitive.stroke_opacity != 1
            or primitive.source_stroke_color is None or primitive.dash_pattern
            or not _close(primitive.source_stroke_color, source['color'])
            or not _close((primitive.line_width,), (match['width']*factor,))
            or (not _same_cycle(primitive.points, expected) if closed else
                not _close(tuple(v for p in primitive.points for v in p), tuple(v for p in expected for v in p)))):
        return None
    region = _bound_round_paint(match, points, source['width'])
    if region is None:
        return None
    return {'primitive_id': primitive.id, 'source_draw_order': order, 'closed': closed,
            'points_mm': [tuple(p) for p in primitive.points], 'rgb': list(source['color']),
            'source_points_pdf': points,
            'source_point_errors_pdf': [tuple(abs(v-r)+e+4*_ulp32(r) for v, r, e in zip(p, q, errors, strict=True))
                                        for p, q, errors in zip(match['points'], points, match['point_errors'], strict=True)],
            'source_width_pdf': max(match['width'], source['width']),
            'width_mm': match['width']*factor, 'source_bounds_pdf': region,
            'paint_bounds_mm': _bounds([model((region[0], region[1])), model((region[2], region[3]))]),
            'svg_paint_ordinal': match['paint_ordinal']}


def _compound_disjoint_proof(primitives, original, clips, region, model):
    """Bind the complete normalized even-odd group to its original empty clip region."""
    if (not primitives or original is None or original.get('type') != 'f'
            or original.get('fill_opacity') != 1 or original.get('fill') is None):
        return None
    seq = original.get('seqno')
    if (type(seq) is not int or len({p.id for p in primitives}) != len(primitives)
            or any(p.source_draw_order != seq or p.fill_opacity != 1
                   or p.clip_fill_even_odd is not True or p.source_fill_color is None
                   or not _close(p.source_fill_color, original['fill']) for p in primitives)):
        return None
    matches = []
    for clip in clips:
        if clip.get('even_odd') is not True:
            continue
        proof = _clip_excludes_region(clip, region)
        if proof is None or len(proof['contours']) != len(primitives):
            continue
        cycles = [[model(point) for point in contour] for contour in proof['contours']]
        joins = [[i for i, contour in enumerate(cycles) if _same_cycle(p.points, contour)]
                 for p in primitives]
        if (any(len(join) != 1 for join in joins)
                or len({join[0] for join in joins}) != len(cycles)):
            continue
        matches.append(proof)
    if len(matches) != 1:
        return None
    return {'source_draw_order': seq, 'source_clip': matches[0],
            'contours': [{'primitive_id': p.id, 'source_draw_order': seq,
                          'points_mm': [tuple(point) for point in p.points],
                          'fill_rgb': list(p.fill_color), 'source_fill_rgb': list(original['fill']),
                          'even_odd': True} for p in primitives]}


def plan_terminal_triangles(page, page_data, source_sha256, *, user_scale=1., flip_y=True):
    """Join raw/SVG/normalized occurrences and prove complete later-paint absence."""
    if re.fullmatch('[0-9a-f]{64}', source_sha256) is None:
        raise ValueError('Invalid triangle source identity')
    if not math.isfinite(user_scale) or user_scale <= 0:
        raise ValueError('Invalid triangle source scale')
    if int(page.rotation) or tuple(page.rect)[:2] != (0., 0.):
        return [], []
    try:
        _source_group_declarations(page)
    except ValueError:
        return [], []
    svg = page.get_svg_image(text_as_path=False)
    svg_rows = _svg_triangles(svg)
    drawings = page.get_drawings()
    raw = {row['seqno']: row for row in drawings}
    if len(raw) != len(drawings) or any(type(seq) is not int or seq < 0 for seq in raw):
        raise ValueError('Original triangle drawing events have ambiguous paint order')
    log = [(kind, _box(box)) for kind, box in page.get_bboxlog()]
    active, clip_map, groups = [], {}, []
    for row in page.get_drawings(extended=True):
        level = row.get('level', 0)
        active = [old for old in active if old.get('level', 0) < level]
        if row.get('type') in ('clip', 'group'):
            active.append(row)
            if row['type'] == 'group' and (row.get('blendmode', 'Normal') != 'Normal'
                    or row.get('opacity', 1) != 1 or row.get('knockout', False)):
                groups.append(row)
        elif 'seqno' in row:
            clip_map[row['seqno']] = [c for c in active if c['type'] == 'clip']
    factor = 25.4 / 72 * user_scale
    raw_strokes = [(row, points) for row in drawings if (points := _raw_stroke_points(row)) is not None]
    raw_segments = [(row, path) for row in drawings if (path := _raw_stroke_segments(row)) is not None]
    stroke_index = _JoinIndex(raw_strokes, lambda pair: _match_bounds(pair[1]))
    segment_index = _JoinIndex(raw_segments, lambda pair: _match_bounds(p for segment in pair[1] for p in segment))
    svg_index = _JoinIndex(svg_rows, lambda row: _match_bounds(row['points'], row['point_errors']))
    stroke_cache, envelope_cache = {}, {}
    compound_groups = {}
    for primitive in page_data.primitives:
        if primitive.clip_fill_group_id:
            compound_groups.setdefault(primitive.clip_fill_group_id, []).append(primitive)

    def model(point):
        return (point[0]*factor, (page.rect.y1-point[1] if flip_y else point[1])*factor)

    candidates = []
    for seq, row in raw.items():
        points = _raw_triangle(row)
        if points is None:
            continue
        matches = [p for p in svg_rows if p['kind'] == 'fill' and _source_points_match(p, points)
                   and p['rgb'] is not None and all(abs(a-b) <= 1/255+1e-6
                       for a, b in zip(p['rgb'], row['fill'], strict=True))]
        if len(matches) == 1:
            if not all(_contains(clip, points) for clip in matches[0]['clips']):
                continue
            candidates.append((seq, row, points, matches[0]))
    counts = {}
    for _, _, _, match in candidates:
        counts[id(match)] = counts.get(id(match), 0)+1
    plans, unresolved = [], []
    for seq, row, points, match in candidates:
        region = _bounds(points)
        record = {'page': page_data.page_number, 'source_draw_order': seq, 'status': 'unqualified'}
        fill_event, stroke_event, outline = seq, None, None
        if row.get('type') == 'fs':
            # MuPDF versions expose either member of this one combined paint.
            # Require an exact adjacent f/s event pair owned by the raw fs row.
            if seq < len(log) and log[seq][0] == 'stroke-path':
                fill_event, stroke_event = seq-1, seq
            else:
                stroke_event = seq+1
            strokes = [p for p in svg_rows if p['kind'] == 'stroke'
                       and p['closed']
                       and p['paint_ordinal'] == match['paint_ordinal']+1
                       and p['matrix'] == match['matrix'] and p['clips'] == match['clips']
                       and _source_points_match(p, points) and p['rgb'] is not None
                       and row.get('color') is not None and _close(p['rgb'], row['color'])
                       and _close((p['width'],), (row.get('width', 0.),))]
            if (len(strokes) != 1 or row.get('stroke_opacity') != 1
                    or row.get('lineJoin') != 1 or tuple(row.get('lineCap', ())) != (1, 1, 1)
                    or row.get('dashes') not in (None, '', '[] 0')
                    or not (row.get('closePath') or tuple(row['items'][-1][2]) == tuple(row['items'][0][1]))
                    or stroke_event >= len(log) or log[stroke_event][0] != 'stroke-path'
                    or (stroke_event != seq and stroke_event in raw)):
                record['reason'] = 'triangle_source_outline_not_uniquely_bound'
                unresolved.append(record)
                continue
            outline = strokes[0]
            region = _bound_round_paint(outline, points, row['width'])
            if region is None:
                record['reason'] = 'triangle_source_outline_not_fully_unclipped'
                unresolved.append(record)
                continue
        if (fill_event < 0 or fill_event >= len(log) or log[fill_event][0] != 'fill-path'
                or not _close(log[fill_event][1], _bounds(points))
                or (fill_event != seq and fill_event in raw)):
            record['reason'] = 'triangle_source_paint_event_not_bound'
            unresolved.append(record)
            continue
        if counts[id(match)] != 1 or any(_intersects(region, tuple(g['rect'])) for g in groups):
            continue
        if not _contains([(0., 0.), (page.rect.x1, 0.), (page.rect.x1, page.rect.y1),
                          (0., page.rect.y1)], [(region[0], region[1]), (region[2], region[3])]):
            continue
        matches = [p for p in page_data.primitives if p.source_draw_order == seq]
        if (len(matches) != 1 or matches[0].clip_fill_group_id
                or matches[0].fill_opacity != 1 or matches[0].source_fill_color is None
                or not _close(matches[0].source_fill_color, row['fill'])
                or not _same_cycle(matches[0].points, [model(p) for p in points])
                or (outline is not None and (
                    matches[0].source_stroke_color is None or matches[0].stroke_opacity != 1
                    or not _close(matches[0].source_stroke_color, outline['rgb'])
                    or not _close((matches[0].line_width,), (outline['width']*factor,))))):
            record['reason'] = 'triangle_normalized_occurrence_not_unique'
            unresolved.append(record)
            continue
        later_strokes, blocker = {}, None
        while True:
            previous, exclusions, bound_exclusions = region, [], []
            for order in range((stroke_event if stroke_event is not None else fill_event)+1, len(log)):
                kind, box = log[order]
                if kind == 'ignore-text' or not _intersects(region, box):
                    continue
                source = _source_path_row(raw, order, kind)
                proofs = [proof for clip in (clip_map.get(source['seqno'], ()) if source else ())
                          if (proof := _clip_excludes_region(clip, region)) is not None]
                if proofs:
                    exclusions.append({'seqno': order, 'clips': proofs})
                    continue
                owned = None
                if kind == 'stroke-path':
                    if order not in envelope_cache:
                        envelope_cache[order] = _stroke_envelope_proof(source, segment_index, svg_index)
                    envelope = envelope_cache[order]
                    if envelope is not None and all(not _intersects(box, region) for box in envelope['bounds']):
                        bound_exclusions.append({key: value for key, value in envelope.items() if key != 'bounds'})
                        continue
                    # These joins depend only on this immutable source page,
                    # normalized primitives and source transform, not the region.
                    # Cache failures too; never persist across another call.
                    if order not in stroke_cache:
                        stroke_cache[order] = _later_stroke_plan(order, raw, stroke_index, svg_index, page_data.primitives, model, factor)
                    owned = stroke_cache[order]
                if owned is not None and (order in later_strokes or len(later_strokes) < 64):
                    later_strokes[order] = owned
                    x0, y0, x1, y1 = owned['source_bounds_pdf']
                    region = (min(region[0], x0), min(region[1], y0),
                              max(region[2], x1), max(region[3], y1))
                    continue
                blocker = {'seqno': order, 'kind': kind, 'bounds_pdf': box}
                break
            if blocker or region == previous:
                break
        if blocker:
            record.update(reason='later_source_paint_overlaps_triangle', blocker=blocker)
            unresolved.append(record)
            continue
        if any(_intersects(region, tuple(group['rect'])) for group in groups):
            record['reason'] = 'expanded_triangle_dependency_has_unknown_compositing'
            unresolved.append(record)
            continue
        if not _contains([(0., 0.), (page.rect.x1, 0.), (page.rect.x1, page.rect.y1), (0., page.rect.y1)],
                         [(region[0], region[1]), (region[2], region[3])]):
            record['reason'] = 'expanded_triangle_dependency_exceeds_page_clip'
            unresolved.append(record)
            continue
        compounds, disjoint_compounds = [], []
        bad = False
        model_region = _bounds([model((region[0], region[1])), model((region[2], region[3]))])
        for group in compound_groups.values():
            if not any(_intersects(_bounds(p.points), model_region) for p in group):
                continue
            original = raw.get(group[0].source_draw_order)
            exclusion = _compound_disjoint_proof(group, original,
                clip_map.get(group[0].source_draw_order, ()), region, model)
            if exclusion is not None:
                disjoint_compounds.append(exclusion)
                continue
            for primitive in group:
                if not _intersects(_bounds(primitive.points), model_region):
                    continue
                original = raw.get(primitive.source_draw_order)
                if (original is None or type(primitive.source_draw_order) is not int
                    or primitive.source_draw_order >= seq or primitive.fill_opacity != 1
                    or original.get('fill_opacity') != 1 or original.get('type') not in ('f', 'fs')
                    or original.get('fill') is None or primitive.source_fill_color is None
                    or not _close(primitive.source_fill_color, original['fill'])):
                    bad = True
                    record['compound_blocker'] = {'primitive_id': primitive.id,
                        'source_draw_order': primitive.source_draw_order,
                        'group_primitive_ids': [p.id for p in group],
                        'normalized_contour_sizes': [len(p.points) for p in group],
                        'source_empty_clip_contour_sizes': [
                            [len(contour) for contour in proof['contours']]
                            for clip in clip_map.get(primitive.source_draw_order, ())
                            if (proof := _clip_excludes_region(clip, region)) is not None]}
                    break
                compounds.append({'primitive_id': primitive.id, 'source_draw_order': primitive.source_draw_order,
                                  'points_mm': [tuple(p) for p in primitive.points],
                                  'fill_rgb': list(primitive.fill_color), 'source_fill_rgb': list(original['fill']),
                                  'even_odd': primitive.clip_fill_even_odd})
            if bad:
                break
        if bad:
            record['reason'] = 'overlapping_compound_source_order_or_opacity_unknown'
            unresolved.append(record)
            continue
        if not compounds:
            continue  # No crossing of the compound-Curve band is needed.
        plans.append({'page': page_data.page_number, 'primitive_id': matches[0].id,
                      'source_draw_order': seq, 'points_mm': [tuple(p) for p in matches[0].points],
                      'fill_event': fill_event, 'stroke_event': stroke_event,
                      'outline': ({'rgb': list(outline['rgb']), 'width_pdf': outline['width'],
                                   'width_mm': outline['width']*factor,
                                   'points_mm': [tuple(p) for p in matches[0].points]}
                                  if outline is not None else None),
                      'dependency_bounds_mm': _bounds([model((region[0], region[1])), model((region[2], region[3]))]),
                      'later_strokes': [later_strokes[key] for key in sorted(later_strokes)],
                      'fill_rgb': list(row['fill']), 'earlier_compound_contours': compounds,
                      'disjoint_compound_groups': disjoint_compounds,
                      'source_sha256': source_sha256, 'source_svg_sha256': hashlib.sha256(svg.encode()).hexdigest(),
                      'source_points_pdf': points, 'later_clip_exclusions': exclusions,
                      'later_stroke_bound_exclusions': bound_exclusions,
                      'later_source_paint_absent': not bool(later_strokes),
                      'later_unowned_paint_absent': True})
    return plans, unresolved


def _validate_following_stroke(obj, proof, members, style, graph):
    from mathutils import Vector
    from .fill_paint_order import same_native_object
    from .image_paint_order import _verify_native_stroke, _world_corners, _local_geometry
    from .visual_style import preview_color

    if (obj.type != 'CURVE' or not any(same_native_object(obj, member) for member in members)
            or obj.get('pdf_image_order_primitive_id') != proof['primitive_id']
            or obj.data.dimensions != '3D' or obj.data.extrude != 0
            or not _close((obj.data.bevel_depth*2,), (proof['width_mm']*.001,))):
        raise ValueError('Later triangle stroke ownership or source width changed')
    _verify_native_stroke(obj, proof)
    centerline = [obj.matrix_world @ Vector(tuple(p.co)[:3]) for p in obj.data.splines[0].points]
    if not _close([p.z for p in centerline], [centerline[0].z]*len(centerline)):
        raise ValueError('Later triangle stroke source plane is tilted')
    color = preview_color(proof['rgb'], style)
    if len(obj.data.materials) != 1:
        raise ValueError('Later triangle stroke material ownership changed')
    material = obj.data.materials[0]
    if not material.use_nodes or not _close(material.diffuse_color, (*color, 1.)):
        raise ValueError('Later triangle stroke opaque source material changed')
    nodes, links = {node.type: node for node in material.node_tree.nodes}, material.node_tree.links
    if (len(material.node_tree.nodes) != 2 or set(nodes) != {'EMISSION', 'OUTPUT_MATERIAL'}
            or not _close(nodes['EMISSION'].inputs['Color'].default_value, (*color, 1.))
            or nodes['EMISSION'].inputs['Strength'].default_value != 1 or len(links) != 1
            or links[0].from_socket != nodes['EMISSION'].outputs['Emission']
            or links[0].to_socket != nodes['OUTPUT_MATERIAL'].inputs['Surface']):
        raise ValueError('Later triangle stroke actual opaque emission changed')
    paint = _world_corners(obj, graph)
    x0, y0, x1, y1 = proof['paint_bounds_mm']
    if not paint or not _contains([(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                                  [(p.x*1000, p.y*1000) for p in paint]):
        raise ValueError('Later triangle stroke exceeds its proven source footprint')
    return _local_geometry(obj), paint


def _verify_evaluated_fill_disjoint(obj, graph, region):
    """Every actual filled triangle must miss the complete dependency rectangle."""
    evaluated = obj.evaluated_get(graph)
    matrix = evaluated.matrix_world.copy()
    try:
        mesh = evaluated.to_mesh()
        if mesh is None:
            raise ValueError('Native disjoint compound has no evaluated mesh')
        mesh.calc_loop_triangles()
        vertices = [matrix @ vertex.co for vertex in mesh.vertices]
        triangles = list(mesh.loop_triangles)
        if (not vertices or not triangles
                or any(not math.isfinite(value) for point in vertices for value in point)
                or not _close([p.z for p in vertices], [vertices[0].z]*len(vertices))):
            raise ValueError('Native disjoint compound has no finite planar triangles')
        payload = []
        for triangle in triangles:
            indices = list(triangle.vertices)
            if (len(indices) != 3 or len(set(indices)) != 3
                    or any(type(i) is not int or i < 0 or i >= len(vertices) for i in indices)):
                raise ValueError('Native disjoint compound triangle indices changed')
            points = [(vertices[i].x, vertices[i].y) for i in indices]
            area = ((points[1][0]-points[0][0])*(points[2][1]-points[0][1])
                    -(points[1][1]-points[0][1])*(points[2][0]-points[0][0]))
            if area == 0:
                raise ValueError('Native disjoint compound has a degenerate painted triangle')
            if _intersects(_bounds(points), region):
                path = {'even_odd': False, 'items': [('l', points[i], points[(i+1) % 3])
                                                    for i in range(3)]}
                if _clip_excludes_region(path, region) is None:
                    raise ValueError('Native compound paints the source-proven empty dependency region')
            payload.append(points)
        return {'triangle_count': len(triangles),
                'world_triangles_sha256': hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()}
    finally:
        evaluated.to_mesh_clear()


def _cohesive_same_color_plan(plan):
    """A connected opaque paint union needs no invented internal depth order.

    Qualification already closes over all intervening/later source paint.
    Here contact is actual centerline/fill intersection, never bbox contact.
    An unconnected or differently colored participant retains separate order.
    """
    outline = plan.get('outline')
    if (not outline or plan.get('later_unowned_paint_absent') is not True
            or tuple(outline['rgb']) != tuple(plan['fill_rgb'])):
        return False
    polygon = [tuple(p) for p in plan['points_mm']]
    if polygon[0] == polygon[-1]:
        polygon.pop()
    if len(polygon) != 3:
        return False
    edges = [(polygon[i], polygon[(i+1) % 3]) for i in range(3)]

    def inside(point):
        values = [(b[0]-a[0])*(point[1]-a[1])-(b[1]-a[1])*(point[0]-a[0])
                  for a, b in edges[:3]]
        return all(v >= 0 for v in values) or all(v <= 0 for v in values)

    remaining = []
    for stroke in plan.get('later_strokes', ()):
        points = stroke['points_mm']
        if (stroke['closed'] or len(points) != 2
                or tuple(stroke['rgb']) != tuple(plan['fill_rgb'])):
            return False
        remaining.append(tuple(tuple(p) for p in points))
    while remaining:
        attached = [line for line in remaining if any(inside(p) for p in line)
                    or any(_segments_intersect(*line, a, b) for a, b in edges)]
        if not attached:
            return False
        edges.extend(attached)
        remaining = [line for line in remaining if line not in attached]
    return True


def apply_terminal_triangles(plans, collection, config):
    """Raise retained source meshes only after exact native compound ownership."""
    import bpy
    from .fill_paint_order import same_native_object, validate_source_fill
    from .image_paint_order import _world_corners, _local_geometry, _move_display_z, _verify_native_stroke
    from .visual_style import preview_color

    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    members = list(collection.all_objects)
    fills = config.get('_source_fill_objects', ())
    compounds = config.get('_source_compound_fill_objects', ())
    result = []
    followers = [s['primitive_id'] for plan in plans for s in plan.get('later_strokes', ())]
    for plan in plans:
        if plan.get('later_unowned_paint_absent') is not True:
            raise ValueError('Terminal triangle has no unowned later-paint absence proof')
        row = {'page': plan['page'], 'primitive_id': plan['primitive_id'],
               'source_draw_order': plan['source_draw_order'], 'status': 'unqualified'}
        if any(followers.count(s['primitive_id']) != 1 for s in plan.get('later_strokes', ())):
            result.append(dict(row, reason='later_stroke_shared_by_triangle_plans'))
            continue
        selected = [s for s in fills if s['primitive_id'] == plan['primitive_id']]
        if len(selected) != 1:
            row['reason'] = 'native_triangle_fill_not_uniquely_owned'
            result.append(row)
            continue
        spec = selected[0]
        if (spec['source_draw_order'] != plan['source_draw_order'] or spec['fill_opacity'] != 1
                or not _same_cycle(spec['points_mm'], plan['points_mm'])
                or not _close(spec['fill_rgb'], plan['fill_rgb'])):
            raise ValueError('Triangle source/native ownership changed')
        world, _, corners = validate_source_fill(spec, members, graph)
        local = _local_geometry(spec['object'])
        region = _bounds([(p.x, p.y) for p in world])
        fill_region = region
        if plan.get('dependency_bounds_mm'):
            region = tuple(v*.001 for v in plan['dependency_bounds_mm'])
        outline, outline_before = None, None
        if plan.get('outline'):
            outline_rows = config.get('_image_order_stroke_objects', {}).get(plan['primitive_id'], ())
            if len(outline_rows) != 1:
                row['reason'] = 'native_triangle_outline_not_uniquely_owned'
                result.append(row)
                continue
            outline = outline_rows[0]
            if outline.type != 'CURVE' or not any(same_native_object(outline, obj) for obj in members):
                raise ValueError('Native triangle outline left its owned collection')
            _verify_native_stroke(outline, {'closed': True, 'points_mm': plan['outline']['points_mm']})
            source_width = plan['outline']['width_mm']*.001
            if (outline.data.dimensions != '3D'
                    or not _close((outline.data.bevel_depth*2,), (source_width,))
                    or outline.data.extrude != 0):
                row['reason'] = 'native_triangle_outline_width_differs_from_source'
                result.append(row)
                continue
            from mathutils import Vector
            centerline = [outline.matrix_world @ Vector(tuple(p.co)[:3])
                          for p in outline.data.splines[0].points]
            if not _close([p.z for p in centerline], [centerline[0].z]*len(centerline)):
                raise ValueError('Native triangle outline source plane is tilted')
            color = preview_color(plan['outline']['rgb'], spec['visual_style'])
            if len(outline.data.materials) != 1:
                raise ValueError('Native triangle outline material ownership changed')
            material = outline.data.materials[0]
            nodes = {node.type: node for node in material.node_tree.nodes} if material.use_nodes else {}
            if (not _close(material.diffuse_color, (*color, 1.))
                    or len(material.node_tree.nodes) != 2 or set(nodes) != {'EMISSION', 'OUTPUT_MATERIAL'}
                    or not _close(nodes['EMISSION'].inputs['Color'].default_value, (*color, 1.))
                    or nodes['EMISSION'].inputs['Strength'].default_value != 1
                    or len(material.node_tree.links) != 1
                    or material.node_tree.links[0].from_socket != nodes['EMISSION'].outputs['Emission']
                    or material.node_tree.links[0].to_socket != nodes['OUTPUT_MATERIAL'].inputs['Surface']):
                raise ValueError('Native triangle outline opaque source material changed')
            outline_before = _local_geometry(outline)
            footprint = _world_corners(outline, graph)
            if not footprint:
                raise ValueError('Native triangle outline has no evaluated paint')
            region = tuple(v*.001 for v in plan['dependency_bounds_mm'])
            x0, y0, x1, y1 = region
            if not _contains([(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                             [(p.x, p.y) for p in footprint]):
                row['reason'] = 'native_triangle_outline_exceeds_source_dependency'
                result.append(row)
                continue
        later_objects = []
        later_seq = plan.get('stroke_event', plan['source_draw_order'])
        if later_seq is None:
            later_seq = plan['source_draw_order']
        used = {plan['primitive_id']}
        for proof in plan.get('later_strokes', ()):
            rows = config.get('_image_order_stroke_objects', {}).get(proof['primitive_id'], ())
            if (type(proof['source_draw_order']) is not int or proof['source_draw_order'] <= later_seq
                    or proof['primitive_id'] in used or len(rows) != 1):
                raise ValueError('Later triangle stroke order or unique ownership changed')
            later_seq = proof['source_draw_order']
            used.add(proof['primitive_id'])
            local_stroke, paint = _validate_following_stroke(rows[0], proof, members, spec['visual_style'], graph)
            later_objects.append((proof, rows[0], local_stroke, paint))
        required = {c['primitive_id']: c for c in plan['earlier_compound_contours']}
        disjoint_groups = plan.get('disjoint_compound_groups', ())
        excluded = {c['primitive_id']: c for group in disjoint_groups for c in group['contours']}
        if len(excluded) != sum(len(group['contours']) for group in disjoint_groups) or set(required) & set(excluded):
            raise ValueError('Compound source exclusion ownership is ambiguous')
        all_required = {**required, **excluded}
        found = set()
        owned_compound_ids = set()
        disjoint_objects, disjoint_readbacks = [], []
        for compound in compounds:
            obj = compound['object']
            if (len(compound['primitive_ids']) != len(compound['contours_mm'])
                    or len(set(compound['primitive_ids'])) != len(compound['primitive_ids'])):
                raise ValueError('Native compound contour identities are ambiguous')
            if not any(_intersects(_bounds(contour), tuple(v*1000 for v in region))
                       for contour in compound['contours_mm']):
                continue
            if owned_compound_ids.intersection(compound['primitive_ids']):
                raise ValueError('Native compound source ownership is duplicated')
            owned_compound_ids.update(compound['primitive_ids'])
            expected = [all_required.get(i) for i in compound['primitive_ids']]
            relevant = [item for item in expected if item is not None]
            if not relevant:
                raise ValueError('Unexpected native compound overlaps terminal triangle')
            exclusions = [group for group in disjoint_groups
                          if set(compound['primitive_ids']) == {c['primitive_id'] for c in group['contours']}]
            is_disjoint = len(exclusions) == 1
            if any(i in excluded for i in compound['primitive_ids']) and not is_disjoint:
                raise ValueError('Native disjoint compound contour group is incomplete')
            seq = compound['source_draw_order']
            if (type(seq) is not int or (not is_disjoint and seq >= plan['source_draw_order']) or compound['fill_opacity'] != 1
                    or any(item['source_draw_order'] != seq for item in relevant)
                    or any(item['even_odd'] != compound['even_odd'] for item in relevant)
                    or any(not _close(item['fill_rgb'], compound['fill_rgb']) for item in relevant)):
                raise ValueError('Native compound source paint changed')
            if (not any(same_native_object(obj, member) for member in members) or obj.type != 'CURVE'
                    or obj.parent is not None or obj.modifiers or obj.constraints or obj.hide_render
                    or obj.data.dimensions != '2D' or obj.data.fill_mode != 'BOTH'
                    or obj.data.bevel_depth != 0 or obj.data.extrude != 0
                    or len(obj.data.splines) != len(compound['contours_mm'])):
                raise ValueError('Native compound geometry ownership changed')
            for identity, contour, spline in zip(compound['primitive_ids'], compound['contours_mm'], obj.data.splines, strict=True):
                points = list(contour)
                if points[-1] == points[0]:
                    points.pop()
                actual = [obj.matrix_world @ p.co.to_3d() for p in spline.points]
                if (spline.type != 'POLY' or not spline.use_cyclic_u or len(points) != len(actual)
                        or any(p.co[2] != 0. or p.co[3] != 1. for p in spline.points)
                        or getattr(spline, 'material_index', 0) != 0
                        or not _close([v for p in actual for v in (p.x, p.y)],
                                      [v*.001 for p in points for v in p])
                        or not _close([p.z for p in actual], [actual[0].z]*len(actual))):
                    raise ValueError('Native compound source contour changed')
                if identity in all_required:
                    if not _same_cycle(contour, all_required[identity]['points_mm']):
                        raise ValueError('Native compound does not match source proof')
                    found.add(identity)
            native_compound = _world_corners(obj, graph)
            if (not native_compound or not _close([p.z for p in native_compound],
                                                  [native_compound[0].z]*len(native_compound))):
                raise ValueError('Native compound has no finite planar evaluated fill')
            if len(obj.data.materials) != 1:
                raise ValueError('Native compound material ownership changed')
            material = obj.data.materials[0]
            color = preview_color(compound['fill_rgb'], compound['visual_style'])
            if not _close(material.diffuse_color, (*color, 1.)) or not material.use_nodes:
                raise ValueError('Native compound opaque source material changed')
            nodes = {node.type: node for node in material.node_tree.nodes}
            links = material.node_tree.links
            if (len(material.node_tree.nodes) != 2 or set(nodes) != {'EMISSION', 'OUTPUT_MATERIAL'}
                    or not _close(nodes['EMISSION'].inputs['Color'].default_value, (*color, 1.))
                    or nodes['EMISSION'].inputs['Strength'].default_value != 1
                    or len(links) != 1
                    or links[0].from_socket != nodes['EMISSION'].outputs['Emission']
                    or links[0].to_socket != nodes['OUTPUT_MATERIAL'].inputs['Surface']):
                raise ValueError('Native compound actual opaque emission changed')
            if is_disjoint:
                readback = _verify_evaluated_fill_disjoint(obj, graph, region)
                disjoint_objects.append(obj)
                disjoint_readbacks.append({'native_object': obj.name, 'source_draw_order': seq, **readback})
        if found != set(all_required):
            row['reason'] = 'native_earlier_compound_contours_incomplete'
            result.append(row)
            continue
        cohesive = _cohesive_same_color_plan(plan)
        cohort = ([spec['object'], outline, *(obj for _, obj, _, _ in later_objects)]
                  if cohesive else [spec['object']])
        top = -math.inf
        for member in members:
            if (member.hide_render or any(same_native_object(member, own) for own in cohort)
                    or any(same_native_object(member, excluded_obj) for excluded_obj in disjoint_objects)):
                continue
            evaluated = _world_corners(member, graph)
            if evaluated and _intersects(region, _bounds([(p.x, p.y) for p in evaluated])):
                top = max(top, max(p.z for p in evaluated))
        if not math.isfinite(top):
            top = 0.0
        obj = spec['object']
        if cohesive:
            # Source PDF fill and its same-color outline occupy one plane.
            # Remove only the builder's decorative fill setback, then give
            # the entire verified union one translation. Tubes keep their
            # actual radius; their centerline is not stacked above each other.
            from mathutils import Vector
            ordered = [(outline, outline_before),
                       *((following, original) for _, following, original, _ in later_objects)]
            planes = []
            for member, _ in ordered:
                line = [member.matrix_world @ Vector(tuple(p.co)[:3])
                        for p in member.data.splines[0].points]
                planes.append(line[0].z)
            if not _close(planes, [planes[0]]*len(planes)):
                raise ValueError('Connected triangle source centerlines no longer share a plane')
            before = [(member, geometry, _world_corners(member, graph))
                      for member, geometry in ordered]
            fill_plane = min(p.z for p in corners)
            alignment = planes[0]-fill_plane
            bottom = min(planes[0], *(p.z for _, _, paint in before for p in paint))
            common_delta = max(0.0, top+.00005-bottom)
            moves = [(obj, local, corners, alignment+common_delta),
                     *((member, geometry, paint, common_delta) for member, geometry, paint in before)]
            for member, _, _, offset in moves:
                _move_display_z(member, float(member.location.z)+offset)
            bpy.context.view_layer.update()
            graph = bpy.context.evaluated_depsgraph_get()
            for member, geometry, paint, offset in moves:
                actual = _world_corners(member, graph)
                if (_local_geometry(member) != geometry or not actual
                        or min(p.z for p in actual) <= top
                        or not _close(_bounds((p.x, p.y) for p in paint),
                                      _bounds((p.x, p.y) for p in actual))):
                    raise ValueError('Triangle cohesive plane changed geometry or failed earlier-paint clearance')
                member['bcs_terminal_triangle_display_z_offset_m'] = offset
            target = planes[0]+common_delta
            actual_fill = _world_corners(obj, graph)
            if not _close([p.z for p in actual_fill], [target]*len(actual_fill)):
                raise ValueError('Triangle cohesive fill failed source centerplane alignment')
            obj['bcs_terminal_triangle_source_proof'] = json.dumps(plan, sort_keys=True)
            row.update(status='applied', native_object=obj.name, native_outline=outline.name,
                       display_policy='connected_opaque_same_color_source_plane',
                       original_local_geometry_unchanged=True, source_xy_unchanged=True,
                       display_z_offset_m=alignment+common_delta,
                       outline_display_z_offset_m=common_delta, source_plane_display_z_m=target,
                       later_native_strokes=[{'source_draw_order': proof['source_draw_order'],
                           'primitive_id': proof['primitive_id'], 'native_object': following.name,
                           'display_z_offset_m': common_delta}
                           for proof, following, _, _ in later_objects],
                       earlier_compound_contours=sorted(required),
                       disjoint_compound_readbacks=disjoint_readbacks)
            result.append(row)
            continue
        delta = top + .00005 - min(p.z for p in corners)
        _move_display_z(obj, float(obj.location.z) + delta)
        bpy.context.view_layer.update()
        after = _world_corners(obj, bpy.context.evaluated_depsgraph_get())
        if (_local_geometry(obj) != local or not after
                or min(p.z for p in after) <= top
                or not _close(_bounds([(p.x, p.y) for p in after]), fill_region)):
            raise ValueError('Native terminal triangle display lift failed readback')
        if outline is not None:
            before = _world_corners(outline, graph)
            preceding = max(p.z for p in after)
            outline_delta = preceding + .00005 - min(p.z for p in before)
            _move_display_z(outline, float(outline.location.z) + outline_delta)
            bpy.context.view_layer.update()
            after_outline = _world_corners(outline, bpy.context.evaluated_depsgraph_get())
            if (_local_geometry(outline) != outline_before or not after_outline
                    or min(p.z for p in after_outline) <= preceding
                    or not _close(_bounds([(p.x, p.y) for p in before]),
                                  _bounds([(p.x, p.y) for p in after_outline]))):
                raise ValueError('Native triangle outline display move failed readback')
            outline['bcs_terminal_triangle_display_z_offset_m'] = outline_delta
            row.update(native_outline=outline.name, outline_display_z_offset_m=outline_delta)
        preceding = max(p.z for p in (after_outline if outline is not None else after))
        row['later_native_strokes'] = []
        for proof, following, original_local, before in later_objects:
            offset = preceding + .00005 - min(p.z for p in before)
            _move_display_z(following, float(following.location.z)+offset)
            bpy.context.view_layer.update()
            actual = _world_corners(following, bpy.context.evaluated_depsgraph_get())
            if (_local_geometry(following) != original_local or not actual
                    or min(p.z for p in actual) <= preceding
                    or not _close(_bounds([(p.x, p.y) for p in before]),
                                  _bounds([(p.x, p.y) for p in actual]))):
                raise ValueError('Later triangle stroke display move failed readback')
            preceding = max(p.z for p in actual)
            following['bcs_terminal_triangle_display_z_offset_m'] = offset
            row['later_native_strokes'].append({'source_draw_order': proof['source_draw_order'],
                'primitive_id': proof['primitive_id'], 'native_object': following.name,
                'display_z_offset_m': offset})
        obj['bcs_terminal_triangle_source_proof'] = json.dumps(plan, sort_keys=True)
        obj['bcs_terminal_triangle_display_z_offset_m'] = delta
        row.update(status='applied', native_object=obj.name, original_local_geometry_unchanged=True,
                   source_xy_unchanged=True, display_z_offset_m=delta,
                   earlier_compound_contours=sorted(required),
                   disjoint_compound_readbacks=disjoint_readbacks)
        result.append(row)
    return result
