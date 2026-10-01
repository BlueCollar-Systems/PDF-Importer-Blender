"""Source-bound MuPDF outlines for positively absent PDF font programs.

This module does not import Blender and does not identify renderer outlines as
an embedded font. Every visible use must have a unique canonical character.
Unsupported SVG paint or incomplete ownership is an explicit unavailable result.
"""
from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
import hashlib
import json
import math
import re
import struct
import xml.etree.ElementTree as ET


class OutlineUnavailable(ValueError):
    """The bounded source-outline route cannot certify this page."""


IDENTITY = (1., 0., 0., 1., 0., 0.)
NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"
TOKEN = re.compile(r"[MLHVQCZmlhvqcz]|" + NUMBER)
GLYPH = re.compile(r"font_[0-9]+_([0-9]+)")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _finite(values):
    values = tuple(float(value) for value in values)
    if not all(math.isfinite(value) and abs(value) <= 1e12 for value in values):
        raise OutlineUnavailable('nonfinite or excessive source coordinate')
    return values


def _numbers(value):
    tokens = re.findall(NUMBER, value)
    if re.sub(NUMBER, '', value).strip(' ,\t\r\n'):
        raise OutlineUnavailable('unsupported source transform token')
    return _finite(tokens)


def _matrix(value, *, exact=False):
    if not value:
        return tuple(Fraction(v) for v in IDENTITY) if exact else IDENTITY
    match = re.fullmatch(r'matrix\(([^)]*)\)', value.strip())
    if not match:
        raise OutlineUnavailable('unsupported source transform')
    values = _numbers(match.group(1))
    if len(values) != 6 or values[0] * values[3] - values[1] * values[2] == 0:
        raise OutlineUnavailable('singular source transform')
    return tuple(_fraction(token) for token in re.findall(NUMBER, match.group(1))) if exact else values


def _fraction(token):
    if len(token) > 128 or ('e' in token.lower() and abs(int(token.lower().split('e')[1])) > 400):
        raise OutlineUnavailable('source number complexity limit')
    return Fraction(token)


def _compose(outer, inner):
    a, b, c, d, e, f = outer
    g, h, i, j, k, l = inner
    return _finite((a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*k+c*l+e, b*k+d*l+f))


def _point(matrix, point):
    a, b, c, d, e, f = matrix
    x, y = point
    return _finite((a*x+c*y+e, b*x+d*y+f))


def _exact_compose(outer, inner):
    a, b, c, d, e, f = outer
    g, h, i, j, k, l = inner
    return (a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*k+c*l+e, b*k+d*l+f)


def _exact_point(matrix, point):
    a, b, c, d, e, f = matrix
    x, y = point
    return (a*x+c*y+e, b*x+d*y+f)


def _local(node):
    return node.tag.rsplit('}', 1)[-1]


def _rgb(value):
    if value in ('black', '#000'):
        return (0., 0., 0.)
    if value in ('white', '#fff'):
        return (1., 1., 1.)
    if re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        return tuple(int(value[i:i+2], 16) / 255 for i in (1, 3, 5))
    if re.fullmatch(r'#[0-9a-fA-F]{3}', value):
        return tuple(int(c*2, 16) / 255 for c in value[1:])
    raise OutlineUnavailable('unsupported source glyph color')


def _style(node, inherited, matrix):
    if any(node.get(key) is not None for key in ('mix-blend-mode', 'isolation')):
        raise OutlineUnavailable('unsupported source glyph style')
    result = dict(inherited)
    declarations = {}
    for entry in node.get('style', '').split(';'):
        if entry.strip():
            key, sep, value = entry.partition(':')
            if not sep or key.strip() in declarations:
                raise OutlineUnavailable('invalid source style')
            declarations[key.strip()] = value.strip()
    allowed = {'fill', 'fill-rule', 'fill-opacity', 'stroke', 'stroke-opacity',
               'opacity', 'clip-path', 'filter', 'mask', 'display', 'visibility'}
    if set(declarations) - allowed:
        raise OutlineUnavailable('unsupported source glyph style')
    for key in allowed:
        value = declarations.get(key, node.get(key))
        if value is not None:
            if key == 'opacity':
                result[key] = float(result.get(key, 1)) * float(value)
            elif key == 'clip-path':
                if value != 'none':
                    result['clips'] = tuple(result.get('clips', ())) + ((value, matrix),)
            else:
                result[key] = value
    if (result.get('filter', 'none') != 'none' or result.get('mask', 'none') != 'none'
            or result.get('display', 'inline') == 'none'
            or result.get('visibility', 'visible') != 'visible'):
        raise OutlineUnavailable('unsupported source glyph effect')
    return result


def _paint(style):
    if (float(style.get('opacity', 1)) != 1 or float(style.get('fill-opacity', 1)) != 1
            or style.get('stroke', 'none') != 'none'
            or style.get('fill', 'black') == 'none'):
        raise OutlineUnavailable('source glyph needs opaque fill-only paint')
    rule = style.get('fill-rule', 'nonzero')
    if rule not in ('nonzero', 'evenodd'):
        raise OutlineUnavailable('unknown source fill rule')
    return {'fill_rule': rule, 'color': _rgb(style.get('fill', 'black'))}


def parse_path(value, *, exact=False):
    """Keep original line/cubic support; exactly elevate quadratic support."""
    tokens, end = [], 0
    for match in TOKEN.finditer(value):
        if value[end:match.start()].strip(' ,\t\r\n'):
            raise OutlineUnavailable('unsupported source glyph path command')
        tokens.append(match.group())
        end = match.end()
    if value[end:].strip(' ,\t\r\n') or len(tokens) > 20000:
        raise OutlineUnavailable('invalid or excessive source glyph path')
    contours, current, origin, segments, command, index = [], None, None, [], None, 0
    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]
            index += 1
            if command.upper() == 'Z':
                if current is None:
                    raise OutlineUnavailable('close without source contour')
                if segments:
                    if current != origin:
                        segments.append(('L', origin))
                    contours.append({'start': origin, 'segments': segments, 'closed': True})
                current, origin, segments, command = None, None, [], None
                continue
        if command is None:
            raise OutlineUnavailable('source path has no command')
        upper = command.upper()
        count = {'M': 2, 'L': 2, 'H': 1, 'V': 1, 'Q': 4, 'C': 6}.get(upper)
        if count is None or index + count > len(tokens):
            raise OutlineUnavailable('incomplete source glyph path')
        raw = tokens[index:index+count]
        values = _finite(raw)
        if exact:
            values = tuple(_fraction(token) for token in raw)
        index += count
        relative = command.islower()
        base = current if relative and current is not None else (0, 0)
        if upper == 'M':
            if segments:
                if current != origin:
                    segments.append(('L', origin))
                contours.append({'start': origin, 'segments': segments, 'closed': True})
                segments = []
            current = origin = (values[0]+base[0], values[1]+base[1])
            command = 'l' if relative else 'L'
            continue
        if current is None:
            raise OutlineUnavailable('source contour needs a move')
        if upper == 'H':
            end_point = (values[0] + (current[0] if relative else 0), current[1])
            segment = ('L', end_point)
        elif upper == 'V':
            end_point = (current[0], values[0] + (current[1] if relative else 0))
            segment = ('L', end_point)
        else:
            points = [(values[i]+base[0], values[i+1]+base[1]) for i in range(0, count, 2)]
            end_point = points[-1]
            if upper == 'Q':
                control = points[0]
                ratio = Fraction(2, 3) if exact else 2/3
                points = [tuple(current[i] + (control[i]-current[i])*ratio for i in (0, 1)),
                          tuple(end_point[i] + (control[i]-end_point[i])*ratio for i in (0, 1)), end_point]
            segment = ('C' if upper in ('C', 'Q') else 'L', *points)
        segments.append(segment)
        current = end_point
    if segments:
        # PDF/SVG fill implicitly closes a final subpath; the closing segment
        # is part of the filled source boundary, never a visible stroke.
        if current != origin:
            segments.append(('L', origin))
        contours.append({'start': origin, 'segments': segments, 'closed': True})
    return contours


def map_contours(contours, matrix):
    return [{'start': _point(matrix, contour['start']), 'closed': True,
             'segments': [(segment[0], *(_point(matrix, p) for p in segment[1:]))
                          for segment in contour['segments']]} for contour in contours]


def control_points(contours):
    return [point for contour in contours for point in
            (contour['start'], *(p for s in contour['segments'] for p in s[1:]))]


def _bbox(points):
    if not points:
        return None
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def _cross(a, b, p):
    return (b[0]-a[0])*(p[1]-a[1]) - (b[1]-a[1])*(p[0]-a[0])


def _contained(points, quad):
    return all(all(v >= 0 for v in sides) or all(v <= 0 for v in sides)
               for sides in ([_cross(a, b, point) for a, b in
                              zip(quad, quad[1:]+quad[:1], strict=True)] for point in points))


def _clip_region(reference, ids, matrix):
    match = re.fullmatch(r'url\(#([^)]*)\)', reference)
    clip = ids.get(match.group(1)) if match else None
    if (clip is None or _local(clip) != 'clipPath' or len(clip) != 1
            or set(clip.attrib) - {'id', 'clipPathUnits'}
            or clip.get('clipPathUnits', 'userSpaceOnUse') != 'userSpaceOnUse'):
        raise OutlineUnavailable('unsupported source glyph clip')
    path = clip[0]
    if (_local(path) != 'path' or set(path.attrib) - {'d', 'transform', 'clip-rule'}
            or path.get('clip-rule', 'nonzero') not in ('nonzero', 'evenodd')):
        raise OutlineUnavailable('unsupported source glyph clip path')
    transform = _exact_compose(matrix, _matrix(path.get('transform'), exact=True))
    holes = []
    try:
        outer = _rectangle_clip_points(path.get('d', ''), exact=True)
    except ValueError as exc:
        if path.get('clip-rule', 'nonzero') != 'evenodd':
            raise OutlineUnavailable('unsupported nonzero source clip region') from exc
        loops, rectangles = _rectangle_clip_loops(path.get('d', ''))
        if not 2 <= len(rectangles) <= 16:
            raise OutlineUnavailable('source clip hole count outside bound') from exc
        largest = max(range(len(rectangles)), key=lambda i:
                      (rectangles[i][2]-rectangles[i][0])*(rectangles[i][3]-rectangles[i][1]))
        bounds = rectangles[largest]
        for i, box in enumerate(rectangles):
            if i == largest:
                continue
            if not (bounds[0] < box[0] < box[2] < bounds[2]
                    and bounds[1] < box[1] < box[3] < bounds[3]):
                raise OutlineUnavailable('source clip hole is not strictly contained') from exc
            holes.append(loops[i][:4])
        hole_boxes = [box for i, box in enumerate(rectangles) if i != largest]
        for i, first in enumerate(hole_boxes):
            for second in hole_boxes[i+1:]:
                if not (first[2] < second[0] or second[2] < first[0]
                        or first[3] < second[1] or second[3] < first[1]):
                    raise OutlineUnavailable('source clip holes overlap or touch') from exc
        outer = loops[largest][:4]
    quads = [[_exact_point(transform, p) for p in points] for points in [outer, *holes]]
    for quad in quads:
        turns = [_cross(quad[i], quad[(i+1) % 4], quad[(i+2) % 4]) for i in range(4)]
        if not (all(v > 0 for v in turns) or all(v < 0 for v in turns)):
            raise OutlineUnavailable('source clip lost its convex bounds')
    return quads[0], quads[1:]


def _clip_quad(reference, ids, matrix):
    outer, holes = _clip_region(reference, ids, matrix)
    if holes:
        raise OutlineUnavailable('source clip has holes')
    return outer


def _strictly_inside(points, quad):
    sign = 1 if _cross(quad[0], quad[1], quad[2]) > 0 else -1
    return all(_cross(a, b, p)*sign > 0 for p in points
               for a, b in zip(quad, quad[1:]+quad[:1], strict=True))


def _strictly_separated(points, quad):
    # All controls on the outside of ONE supporting line prove the entire
    # Bezier convex hull outside, even under a shear or reflection.
    sign = 1 if _cross(quad[0], quad[1], quad[2]) > 0 else -1
    return any(all(_cross(a, b, p)*sign < 0 for p in points)
               for a, b in zip(quad, quad[1:]+quad[:1], strict=True))


def _clip_control_hull(points, region):
    outer, holes = region
    if not points:
        return None
    box, clip_box = _bbox(points), _bbox(outer)
    if (box[2] < clip_box[0] or clip_box[2] < box[0]
            or box[3] < clip_box[1] or clip_box[3] < box[1]):
        return {'control_bounds': [str(v) for v in box],
                'clip_bounds': [str(v) for v in clip_box],
                'proof': 'exact_source_control_hull_strictly_outside_clip'}
    if not _contained(points, outer):
        raise OutlineUnavailable('source glyph partially crosses unsupported clip')
    for hole in holes:
        if _strictly_inside(points, hole):
            return {'control_bounds': [str(v) for v in box],
                    'hole_quad': [[str(v) for v in point] for point in hole],
                    'proof': 'exact_source_control_hull_strictly_inside_evenodd_hole'}
        if not _strictly_separated(points, hole):
            raise OutlineUnavailable('source glyph partially crosses unsupported clip hole')
    return None


def _glyph_reference_graph(root, ids):
    """Authenticate every reference before ignoring non-glyph paint branches."""
    nodes = list(root.iter())
    if len(nodes) > 200000:
        raise OutlineUnavailable('source SVG node count limit')
    references = {}
    for node in nodes:
        if _local(node) in {'text', 'tspan', 'textPath'}:
            raise OutlineUnavailable('unproved non-outline source text')
        href, xlink = node.get('href'), node.get('{http://www.w3.org/1999/xlink}href')
        if href is not None and xlink is not None and href != xlink:
            raise OutlineUnavailable('ambiguous source SVG reference')
        refs = []
        for key, value in node.attrib.items():
            if key.rsplit('}', 1)[-1] == 'href':
                if value.startswith('#'):
                    refs.append(value[1:])
                elif not (_local(node) == 'image' and value.startswith(
                        ('data:image/png;base64,', 'data:image/jpeg;base64,'))):
                    raise OutlineUnavailable('unproved external source reference')
            urls = re.findall(r'url\(([^)]*)\)', value)
            if value.count('url(') != len(urls):
                raise OutlineUnavailable('malformed source reference')
            for ref in urls:
                if not re.fullmatch(r'#[^\s()]+', ref):
                    raise OutlineUnavailable('unproved source paint reference')
                refs.append(ref[1:])
        if any(ref not in ids for ref in refs):
            raise OutlineUnavailable('missing source SVG reference')
        references[node] = [ids[ref] for ref in refs]
    state, live = {}, {}

    def walk(node, depth=0):
        if depth > 64 or state.get(node) == 1:
            raise OutlineUnavailable('cyclic or deep source reference graph')
        if state.get(node) == 2:
            return live[node]
        state[node] = 1
        children = [child for child in node if _local(child) != 'defs']
        result = bool(GLYPH.fullmatch(node.get('id', '')))
        for target in children + references[node]:
            result = walk(target, depth+1) or result
        state[node], live[node] = 2, result
        return result

    for node in nodes:
        walk(node)
    return live


def svg_placements(svg):
    if len(svg.encode()) > 32*1024*1024:
        raise OutlineUnavailable('source SVG byte limit')
    root = ET.fromstring(svg)
    ids = {}
    for node in root.iter():
        identity = node.get('id')
        if identity:
            if identity in ids:
                raise OutlineUnavailable('duplicate source SVG identity')
            ids[identity] = node
        if _local(node) in {'style', 'script', 'foreignObject'}:
            raise OutlineUnavailable('unsupported active source SVG')
    viewbox = _numbers(root.get('viewBox', ''))
    if len(viewbox) != 4 or viewbox[2] <= 0 or viewbox[3] <= 0:
        raise OutlineUnavailable('invalid source SVG viewBox')
    live = _glyph_reference_graph(root, ids)
    placements, definitions, clip_cache = [], {}, {}

    def visit(node, inherited, matrix, exact_matrix):
        if _local(node) == 'defs' or not live[node]:
            return
        matrix = _compose(matrix, _matrix(node.get('transform')))
        exact_matrix = _exact_compose(exact_matrix, _matrix(node.get('transform'), exact=True))
        style = _style(node, inherited, exact_matrix)
        if _local(node) == 'use':
            ref = node.get('href', node.get('{http://www.w3.org/1999/xlink}href', ''))
            identity = ref[1:] if ref.startswith('#') else ''
            match = GLYPH.fullmatch(identity)
            if not match:
                # A referenced group can conceal additional glyph paint.
                raise OutlineUnavailable('non-glyph source use needs separate proof')
            definition = ids.get(identity)
            if (definition is None or _local(definition) != 'path'
                    or set(definition.attrib) - {'id', 'd'}):
                raise OutlineUnavailable('unsupported source glyph definition')
            if node.get('x') not in (None, '0') or node.get('y') not in (None, '0'):
                raise OutlineUnavailable('unproved source use translation')
            paint = _paint(style)
            if identity not in definitions:
                definitions[identity] = parse_path(definition.get('d', ''), exact=True)
            contours = map_contours(definitions[identity], matrix)
            points = [_exact_point(exact_matrix, p) for p in control_points(definitions[identity])]
            empty = not points
            outside = []
            for ref, clip_matrix in style.get('clips', ()):
                key = (ref, clip_matrix)
                if key not in clip_cache:
                    clip_cache[key] = _clip_region(ref, ids, clip_matrix)
                excluded = _clip_control_hull(points, clip_cache[key])
                if excluded is not None:
                    outside.append(excluded)
            placements.append({'index': len(placements), 'glyph_id': int(match.group(1)),
                'unicode': node.get('data-text'), 'definition_id': identity,
                'definition_sha256': hashlib.sha256(definition.get('d', '').encode()).hexdigest(),
                'matrix': matrix, 'origin': _point(matrix, (0., 0.)), 'contours_svg': contours,
                'empty': empty, 'fully_clipped': outside, **paint})
            if len(placements) > 200000:
                raise OutlineUnavailable('source placement count limit')
            return
        for child in node:
            visit(child, style, matrix, exact_matrix)

    visit(root, {'fill': 'black', 'stroke': 'none', 'opacity': 1.}, IDENTITY,
          tuple(Fraction(v) for v in IDENTITY))
    return viewbox, placements


def missing_font_evidence(item, page_number):
    """Only positive original-source absence opens this additional route."""
    if getattr(item, 'font_asset', None) is not None:
        return None
    failure = getattr(item, 'font_failure', None)
    if (failure is None or getattr(failure, 'page_number', None) != page_number
            or getattr(failure, 'span_font_name', None) != getattr(item, 'font_name', None)):
        return None
    reason, category = getattr(failure, 'reason', ''), getattr(failure, 'proof_category', '')
    empty = (reason == 'embedded_font_asset_build_failed'
             and category == 'source_specific_impossibility'
             and getattr(failure, 'detail', '') == 'embedded font stream is empty'
             and type(getattr(failure, 'source_xref', None)) is int
             and failure.source_xref > 0)
    absent = reason == 'no_exact_embedded_font_match' and category == 'source_font_absent_for_item'
    if not (empty or absent):
        return None
    return {'reason': reason, 'proof_category': category,
            'detail': getattr(failure, 'detail', ''), 'source_xref': getattr(failure, 'source_xref', None),
            'font_name': item.font_name, 'source_page': page_number,
            'font_program_authenticity': 'absent; outlines are renderer-derived'}


def _float32_ulp(value):
    value = abs(float(value))
    packed = struct.unpack('<I', struct.pack('<f', value))[0]
    if packed >= 0x7f800000:
        raise OutlineUnavailable('source float32 position overflow')
    current = struct.unpack('<f', struct.pack('<I', packed))[0]
    following = struct.unpack('<f', struct.pack('<I', packed+1))[0]
    return following-current


def _float32(value):
    try:
        result = struct.unpack('<f', struct.pack('<f', float(value)))[0]
    except (OverflowError, ValueError) as exc:
        raise OutlineUnavailable('invalid source float32 position') from exc
    if not math.isfinite(result):
        raise OutlineUnavailable('nonfinite source float32 position')
    return result


def _source_character_inventory(page):
    """Keep extraction-inserted spaces separate from actually painted glyphs."""
    synthetic = set()
    for block in page.get_text('rawdict')['blocks']:
        if block.get('type') != 0:
            continue
        for line in block['lines']:
            for span in line['spans']:
                for char in span['chars']:
                    if char.get('synthetic') is True:
                        if char.get('c') != ' ':
                            raise OutlineUnavailable('unknown synthetic extraction character')
                        synthetic.add((span['font'], char['c'], tuple(char['origin']), tuple(char['bbox'])))
    trace = defaultdict(list)
    for span_index, span in enumerate(page.get_texttrace()):
        for character_index, (codepoint, glyph_id, origin, bbox) in enumerate(span['chars']):
            if not isinstance(codepoint, int) or not 0 <= codepoint <= 0x10ffff:
                raise OutlineUnavailable('invalid original character codepoint')
            trace[(chr(codepoint), tuple(origin))].append(
                {'glyph_id': int(glyph_id), 'bbox': tuple(bbox), 'font': span['font'],
                 'trace_id': [span_index, character_index], 'seqno': span.get('seqno'),
                 'unicode': chr(codepoint), 'source_origin': tuple(origin)})
    return synthetic, trace


def qualify_page(page, items, *, page_number, width_mm, height_mm, flip_y=True, pdf_sha256):
    """Produce complete canonical ownership using actual renderer character IDs."""
    if not re.fullmatch('[0-9a-f]{64}', pdf_sha256):
        raise OutlineUnavailable('source PDF hash is absent')
    svg = page.get_svg_image(text_as_path=True)
    viewbox, placements = svg_placements(svg)
    x0, y0, w, h = viewbox
    sx, sy = float(width_mm)/w, float(height_mm)/h
    model = (sx, 0., 0., -sy if flip_y else sy, -x0*sx,
             (y0+h)*sy if flip_y else -y0*sy)
    rotation = tuple(page.rotation_matrix)
    synthetic, trace = _source_character_inventory(page)
    consumed_synthetic, canonical_trace_ids, consumed_trace_ids = set(), set(), set()
    trace_by_key = defaultdict(list)
    for occurrences in trace.values():
        for occurrence in occurrences:
            origin = _point(rotation, occurrence['source_origin'])
            key = (occurrence['unicode'], occurrence['glyph_id'], *map(_float32, origin))
            trace_by_key[key].append(occurrence)
    omissions, ownership = [], []
    source_by_key, records = defaultdict(list), {}
    for item in items:
        item_id = f'page:{page_number}:text:{int(item.id)}'
        if item_id in records or getattr(item, 'page_number', None) != page_number:
            raise OutlineUnavailable('duplicate or wrong canonical source item')
        chars = tuple(getattr(item, 'source_char_layout', ()) or ())
        if not chars:
            raise OutlineUnavailable('canonical source character placement is missing')
        if ''.join(char.text for char in chars) != item.text:
            raise OutlineUnavailable('canonical body differs from original character sequence')
        records[item_id] = {'item_id': item_id, 'page_number': page_number,
                           'source_text': item.text, 'pdf_sha256': pdf_sha256,
                           'svg_sha256': hashlib.sha256(svg.encode()).hexdigest(),
                           'source_font_absence': missing_font_evidence(item, page_number),
                           'placements': [], 'empty_placements': [], 'clipped_placements': [],
                           'synthetic_extraction_spaces': []}
        for index, char in enumerate(chars):
            if len(char.text) != 1:
                raise OutlineUnavailable('canonical source character identity is incomplete')
            key = (item.font_name, char.text, tuple(char.source_origin_pdf), tuple(char.source_bbox_pdf))
            if key in synthetic:
                if key in consumed_synthetic or trace.get((char.text, tuple(char.source_origin_pdf))):
                    raise OutlineUnavailable('synthetic extraction space overlaps painted character')
                consumed_synthetic.add(key)
                records[item_id]['synthetic_extraction_spaces'].append({
                    'character_index': index, 'font': item.font_name, 'unicode': char.text,
                    'source_origin': tuple(char.source_origin_pdf), 'source_bbox': tuple(char.source_bbox_pdf),
                    'proof': 'original_rawdict_synthetic_space_without_trace_occurrence'})
                continue
            source = trace.get((char.text, tuple(char.source_origin_pdf)), ())
            if len(source) != 1 or source[0]['font'] != item.font_name:
                raise OutlineUnavailable('canonical character trace identity is missing or ambiguous')
            trace_id = tuple(source[0]['trace_id'])
            if trace_id in canonical_trace_ids:
                raise OutlineUnavailable('original trace occurrence has duplicate canonical owners')
            canonical_trace_ids.add(trace_id)
            origin = _point(rotation, char.source_origin_pdf)
            source_by_key[(char.text, source[0]['glyph_id'])].append(
                {'item_id': item_id, 'character_index': index, 'origin': origin,
                 'target_origin': tuple(char.target_origin), 'matched': False, 'trace_id': trace_id})
    for placement in placements:
        origin = placement['origin']
        candidates = []
        for char in source_by_key.get((placement['unicode'], placement['glyph_id']), ()):
            # Both interfaces expose MuPDF float32 placement. The SVG decimal
            # must round back to the EXACT original native coordinate; this is
            # representational equality, not a distance/box scoring tolerance.
            bounds = [_float32_ulp(char['origin'][i]) * .5 for i in (0, 1)]
            if all(_float32(origin[i]) == _float32(char['origin'][i]) for i in (0, 1)):
                candidates.append((char, bounds))
        if not candidates:
            key = (placement['unicode'], placement['glyph_id'], *map(_float32, origin))
            sources = trace_by_key.get(key, ())
            if (len(sources) != 1 or not (placement['empty'] or placement['fully_clipped'])
                    or tuple(sources[0]['trace_id']) in canonical_trace_ids
                    or tuple(sources[0]['trace_id']) in consumed_trace_ids):
                raise OutlineUnavailable('source glyph ownership is missing or ambiguous')
            source = sources[0]
            consumed_trace_ids.add(tuple(source['trace_id']))
            omissions.append({
                'placement': {k: v for k, v in placement.items() if k != 'contours_svg'},
                'original_trace': source,
                'reason': 'empty_source_definition' if placement['empty'] else 'strictly_clipped_source_hull',
                'binding_method': 'exact_float32_source_origin_roundtrip',
                'native_entities_created': 0,
            })
            continue
        if len(candidates) != 1 or candidates[0][0]['matched']:
            raise OutlineUnavailable('source glyph ownership is missing or ambiguous')
        char, bounds = candidates[0]
        if char['trace_id'] in consumed_trace_ids:
            raise OutlineUnavailable('original trace occurrence was already consumed')
        consumed_trace_ids.add(char['trace_id'])
        ownership.append({'index': placement['index'], 'item_id': char['item_id'],
                          'character_index': char['character_index'], 'trace_id': char['trace_id']})
        char['matched'] = True
        mapped = _point(model, origin)
        if not all(abs(mapped[i]-char['target_origin'][i]) <= abs((sx, sy)[i])*bounds[i]+1e-12
                   for i in (0, 1)):
            raise OutlineUnavailable('canonical source-to-model frame changed')
        record = records[char['item_id']]
        bound = {k: v for k, v in placement.items() if k != 'contours_svg'}
        bound.update(character_index=char['character_index'], canonical_origin=char['origin'],
                     origin_precision_bounds=bounds,
                     binding_method='exact_float32_source_origin_roundtrip')
        if placement['empty']:
            record['empty_placements'].append(bound)
        elif placement['fully_clipped']:
            record['clipped_placements'].append(bound)
        else:
            bound['contours'] = map_contours(placement['contours_svg'], model)
            record['placements'].append(bound)
    unmatched = [row for rows in source_by_key.values() for row in rows if not row['matched']]
    if unmatched:
        raise OutlineUnavailable('canonical characters are absent from source SVG')
    ledger = None
    if omissions:
        ledger = {'schema': 'bcs.blender.source_page_occurrences/1', 'page_number': page_number,
                  'pdf_sha256': pdf_sha256, 'svg_sha256': hashlib.sha256(svg.encode()).hexdigest(),
                  'placement_count': len(placements), 'rotation_matrix': rotation,
                  'canonical_ownership': ownership,
                  'omissions': omissions, 'canonical_item_ids': list(records)}
        reference = page_ledger_reference(ledger)
        for record in records.values():
            record['source_page_ledger'] = reference
    for record in records.values():
        record['source_outline_sha256'] = digest(record)
    return {'schema': 'bcs.blender.source_text_outlines/1', 'pdf_sha256': pdf_sha256,
            'svg_sha256': hashlib.sha256(svg.encode()).hexdigest(), 'records': records,
            'placement_count': len(placements), 'canonical_character_count':
            sum(len(rows) for rows in source_by_key.values()), 'model_matrix': model,
            'page_occurrence_ledger': ledger}


def page_ledger_reference(ledger):
    """Bind a complete page census once, without replicating it per item."""
    if (ledger.get('schema') != 'bcs.blender.source_page_occurrences/1'
            or type(ledger.get('page_number')) is not int or ledger['page_number'] < 1
            or type(ledger.get('placement_count')) is not int
            or not 0 < ledger['placement_count'] <= 200000):
        raise OutlineUnavailable('invalid source page occurrence ledger')
    for field in ('pdf_sha256', 'svg_sha256'):
        if not re.fullmatch('[0-9a-f]{64}', str(ledger.get(field, ''))):
            raise OutlineUnavailable('source page ledger byte binding is absent')
    item_ids = ledger['canonical_item_ids']
    if (len(set(item_ids)) != len(item_ids) or not item_ids
            or any(not re.fullmatch(f"page:{ledger['page_number']}:text:[0-9]+", value)
                   for value in item_ids)):
        raise OutlineUnavailable('source page ledger canonical roster differs')
    indices, traces, characters = [], [], set()
    for row in ledger['canonical_ownership']:
        pair = (row['item_id'], row['character_index'])
        if (row['item_id'] not in item_ids or type(row['character_index']) is not int
                or row['character_index'] < 0 or pair in characters):
            raise OutlineUnavailable('duplicate source page ledger canonical owner')
        characters.add(pair)
        indices.append(row['index'])
        traces.append(tuple(row['trace_id']))
    rotation = _finite(ledger['rotation_matrix'])
    if len(rotation) != 6:
        raise OutlineUnavailable('source page ledger rotation is invalid')
    if not ledger['omissions']:
        raise OutlineUnavailable('empty orphan ledger is unnecessary')
    for row in ledger['omissions']:
        placed, original = row['placement'], row['original_trace']
        if (row.get('native_entities_created') != 0 or type(row['native_entities_created']) is not int
                or row.get('binding_method') != 'exact_float32_source_origin_roundtrip'
                or original['unicode'] != placed['unicode'] or original['glyph_id'] != placed['glyph_id']
                or not isinstance(original['font'], str) or not original['font']
                or not re.fullmatch('[0-9a-f]{64}', str(placed.get('definition_sha256', '')))
                or not GLYPH.fullmatch(placed['definition_id'])):
            raise OutlineUnavailable('orphan source occurrence binding differs')
        source_origin = _point(rotation, original['source_origin'])
        if any(_float32(source_origin[i]) != _float32(placed['origin'][i]) for i in (0, 1)):
            raise OutlineUnavailable('orphan source occurrence origin differs')
        if row['reason'] == 'empty_source_definition':
            valid = placed.get('empty') is True and not placed.get('fully_clipped')
        elif row['reason'] == 'strictly_clipped_source_hull':
            valid = placed.get('empty') is False and bool(placed.get('fully_clipped'))
            valid = valid and all(proof.get('proof') in {
                'exact_source_control_hull_strictly_outside_clip',
                'exact_source_control_hull_strictly_inside_evenodd_hole',
            } for proof in placed['fully_clipped'])
        else:
            valid = False
        if not valid:
            raise OutlineUnavailable('orphan source occurrence has no invisible-ink proof')
        indices.append(placed['index'])
        traces.append(tuple(original['trace_id']))
    if (any(type(index) is not int for index in indices)
            or sorted(indices) != list(range(ledger['placement_count']))
            or any(len(key) != 2 or any(type(v) is not int or v < 0 for v in key) for key in traces)
            or len(set(traces)) != len(traces)):
        raise OutlineUnavailable('source page occurrence census is incomplete or reused')
    return {'schema': 'bcs.blender.source_page_occurrences_ref/1',
            'page_number': ledger['page_number'], 'pdf_sha256': ledger['pdf_sha256'],
            'svg_sha256': ledger['svg_sha256'], 'ledger_sha256': digest(ledger)}


def persist_page_ledger(collection, ledger):
    reference = page_ledger_reference(ledger)
    wrapper = {'page_collection': collection.name, 'ledger': ledger}
    raw = json.dumps(wrapper, sort_keys=True, separators=(',', ':'), allow_nan=False)
    old = collection.get('pdf_source_page_ledger_json')
    if old is not None and old != raw:
        raise RuntimeError('source page occurrence ledger already has different ownership')
    collection['pdf_source_page_ledger_json'] = raw
    collection['pdf_source_page_ledger_sha256'] = digest(wrapper)
    verify_page_ledger_collection(collection, reference)


def verify_page_ledger_collection(collection, reference, *, collections=()):
    """Re-read the owned ledger, rejecting copies of the same owner after reopen."""
    raw = collection.get('pdf_source_page_ledger_json')
    if not isinstance(raw, str):
        raise OutlineUnavailable('source page occurrence ledger is missing')
    wrapper = json.loads(raw)
    if (wrapper.get('page_collection') != collection.name
            or digest(wrapper) != collection.get('pdf_source_page_ledger_sha256')
            or page_ledger_reference(wrapper['ledger']) != reference):
        raise OutlineUnavailable('source page occurrence ledger ownership or hash differs')
    for other in collections:
        if other.name == collection.name:
            continue
        duplicate = other.get('pdf_source_page_ledger_json')
        if duplicate is not None:
            content = json.loads(duplicate)
            if content.get('page_collection') == collection.name:
                raise OutlineUnavailable('duplicate source page occurrence ledger carrier')
    return {'verified': True, **reference, 'page_collection': collection.name,
            'orphan_omissions': len(wrapper['ledger']['omissions'])}


def page_record_provider(page, items, *, collection=None, **options):
    """Lazily qualify once; every later rung uses the same complete page proof."""
    qualified, unavailable = None, None

    def record_for(item):
        nonlocal qualified, unavailable
        if missing_font_evidence(item, options['page_number']) is None:
            raise OutlineUnavailable('source font absence is not proved')
        if unavailable is not None:
            raise OutlineUnavailable(unavailable)
        if qualified is None:
            try:
                qualified = qualify_page(page, items, **options)
            except (ValueError, ET.ParseError, OverflowError) as exc:
                unavailable = f'{type(exc).__name__}: {exc}'
                raise OutlineUnavailable(unavailable) from exc
            if collection is not None and qualified['page_occurrence_ledger'] is not None:
                try:
                    persist_page_ledger(collection, qualified['page_occurrence_ledger'])
                except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
                    raise RuntimeError('source page occurrence ledger persistence failed') from exc
        key = f"page:{options['page_number']}:text:{int(item.id)}"
        record = qualified['records'][key]
        if record['source_font_absence'] != missing_font_evidence(item, options['page_number']):
            raise OutlineUnavailable('source font absence changed after qualification')
        return record

    return record_for


def zero_ink_proof(record):
    """Authenticate complete whitespace occurrences; never infer ink from text alone."""
    raw = {key: value for key, value in record.items() if key != 'source_outline_sha256'}
    if digest(raw) != record.get('source_outline_sha256'):
        raise OutlineUnavailable('zero-ink source record hash changed')
    body = record.get('source_text')
    absence = record.get('source_font_absence') or {}
    empty_font = (absence.get('reason') == 'embedded_font_asset_build_failed'
                  and absence.get('proof_category') == 'source_specific_impossibility'
                  and absence.get('detail') == 'embedded font stream is empty'
                  and type(absence.get('source_xref')) is int and absence['source_xref'] > 0)
    absent_font = (absence.get('reason') == 'no_exact_embedded_font_match'
                   and absence.get('proof_category') == 'source_font_absent_for_item')
    if (not isinstance(body, str) or not body or not body.isspace()
            or record.get('placements') or record.get('clipped_placements')
            or not (empty_font or absent_font)
            or absence.get('source_page') != record.get('page_number') or not absence.get('font_name')):
        raise OutlineUnavailable('source zero-ink eligibility is not proved')
    for field in ('pdf_sha256', 'svg_sha256'):
        if not re.fullmatch('[0-9a-f]{64}', str(record.get(field, ''))):
            raise OutlineUnavailable('zero-ink source bytes are unbound')
    indices = []
    for occurrence in record.get('empty_placements', ()):
        if (occurrence.get('empty') is not True or occurrence.get('fully_clipped')
                or occurrence.get('binding_method') != 'exact_float32_source_origin_roundtrip'
                or not re.fullmatch('[0-9a-f]{64}', str(occurrence.get('definition_sha256', '')))):
            raise OutlineUnavailable('unproved empty source occurrence')
        indices.append((occurrence.get('character_index'), occurrence.get('unicode')))
    for occurrence in record.get('synthetic_extraction_spaces', ()):
        if (occurrence.get('proof') != 'original_rawdict_synthetic_space_without_trace_occurrence'
                or occurrence.get('font') != absence['font_name'] or occurrence.get('unicode') != ' '):
            raise OutlineUnavailable('unproved synthetic extraction space')
        _finite(occurrence['source_origin'])
        _finite(occurrence['source_bbox'])
        indices.append((occurrence.get('character_index'), occurrence.get('unicode')))
    if (len(indices) != len(body) or any(type(index) is not int for index, _ in indices)
            or sorted(index for index, _ in indices) != list(range(len(body)))
            or any(body[index] != value for index, value in indices)):
        raise OutlineUnavailable('zero-ink character coverage is incomplete')
    return {'schema': 'bcs.blender.source_zero_ink/1', 'source_record': record,
            'source_outline_sha256': record['source_outline_sha256'],
            'native_entities_created': 0, 'source_character_count': len(body)}


def verify_zero_ink_delivery(delivery):
    """Recheck persisted/report evidence before including it in READY accounting."""
    proof = delivery['zero_ink_proof']
    source = proof['source_record']
    if (proof != zero_ink_proof(source) or delivery.get('zero_ink_proof_sha256') != digest(proof)
            or delivery.get('status') != 'verified_zero_ink' or delivery.get('entity_ids') != []
            or delivery.get('final_representation') is not None
            or delivery.get('item_id') != source.get('item_id')
            or delivery.get('page') != source.get('page_number')
            or delivery.get('source_body') != source.get('source_text')
            or delivery.get('item_id') != f"page:{delivery['page']}:text:{delivery['source_span_id']}"):
        raise OutlineUnavailable('zero-ink delivery binding changed')
    return True


def verify_zero_ink_collection(collection, records):
    selected = [row for row in records if row.get('status') == 'verified_zero_ink']
    expected = {row['item_id']: row for row in selected}
    if len(selected) != len(expected):
        raise OutlineUnavailable('duplicate zero-ink ledger identity')
    raw = collection.get('pdf_verified_zero_ink_json', '[]')
    ledger = json.loads(raw)
    if not isinstance(ledger, list) or len(ledger) != len(expected):
        raise OutlineUnavailable('persisted zero-ink roster differs')
    if ledger and collection.get('pdf_verified_zero_ink_sha256') != digest(ledger):
        raise OutlineUnavailable('persisted zero-ink ledger hash differs')
    seen = set()
    for row in ledger:
        verify_zero_ink_delivery(row)
        identity = row['item_id']
        if identity in seen or row != expected.get(identity) or row['page_collection'] != collection.name:
            raise OutlineUnavailable('persisted zero-ink owner or source binding differs')
        seen.add(identity)
    return {'verified': True, 'verified_zero_ink_items': len(ledger), 'ledger_sha256': digest(ledger)}


# Exact rectangular-union qualification shared by reviewed FreeCAD source.
def _rectangle_clip_loops(path_data):
    """Read one rectangle or an exact rectangular tiling, plus move-only tails.

    A move with no drawing segments contributes no clip area. Every drawn
    subpath must be a closed rectangle. Disjoint interiors and exact area prove
    that multiple rectangles cover their entire bounding rectangle; this is
    not a bounding-box approximation of a more complicated clipping region.
    """
    token_re = re.compile(r"[MLHVZ]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?")
    tokens, end = [], 0
    for match in token_re.finditer(path_data):
        if path_data[end:match.start()].strip(" \t\r\n,"):
            raise ValueError("nonrectangular source glyph clip is unsupported")
        tokens.append(match.group())
        end = match.end()
    if path_data[end:].strip(" \t\r\n,") or not tokens or len(tokens) > 128:
        raise ValueError("unsupported source glyph clip path")
    loops, current, command, index, drawn = [], None, None, 0, False
    while index < len(tokens):
        if tokens[index] in ("M", "L", "H", "V", "Z"):
            command = tokens[index]
            index += 1
            if command == "Z":
                if current is None or len(current) < 4:
                    raise ValueError("invalid source glyph rectangle clip")
                loops.append(current)
                current, command, drawn = None, None, False
                continue
        count = 2 if command in ("M", "L") else 1
        if command is None or index + count > len(tokens):
            raise ValueError("incomplete source glyph clip path")
        try:
            raw = tokens[index:index + count]
            if any(len(value) > 128 or (
                "e" in value.lower() and abs(int(value.lower().split("e")[1])) > 400
            ) for value in raw):
                raise ValueError("source glyph clip numeric limit exceeded")
            if not all(math.isfinite(float(value)) for value in raw):
                raise ValueError("nonfinite source glyph clip coordinate")
            values = [Fraction(value) for value in raw]
        except (ValueError, OverflowError) as exc:
            raise ValueError("incomplete source glyph clip path") from exc
        index += count
        if command == "M":
            if current is not None and drawn:
                raise ValueError("open source glyph clip is unsupported")
            current, command, drawn = [tuple(values)], "L", False
        else:
            if current is None:
                raise ValueError("source glyph clip must start with a move")
            drawn = True
            previous = current[-1]
            point = (values[0], previous[1]) if command == "H" else (
                (previous[0], values[0]) if command == "V" else tuple(values)
            )
            if point != previous:
                current.append(point)
    if current is not None and drawn:
        raise ValueError("open source glyph clip is unsupported")
    if not loops:
        raise ValueError("source glyph clip must contain one rectangle")
    rectangles = []
    for points in loops:
        if points[-1] == points[0]:
            points = points[:-1]
        xs, ys = {p[0] for p in points}, {p[1] for p in points}
        if (
            len(points) != 4 or len(xs) != 2 or len(ys) != 2
            or set(points) != {(x, y) for x in xs for y in ys}
            or any((a[0] == b[0]) == (a[1] == b[1])
                   for a, b in zip(points, points[1:] + points[:1], strict=True))
        ):
            raise ValueError("nonrectangular source glyph clip is unsupported")
        rectangles.append((min(xs), min(ys), max(xs), max(ys)))
    return loops, rectangles


def _rectangle_clip_points(path_data, *, exact=False):
    loops, rectangles = _rectangle_clip_loops(path_data)
    for index, first in enumerate(rectangles):
        for second in rectangles[index + 1:]:
            if (
                max(first[0], second[0]) < min(first[2], second[2])
                and max(first[1], second[1]) < min(first[3], second[3])
            ):
                raise ValueError("overlapping source glyph clip rectangles are unsupported")
    x0 = min(row[0] for row in rectangles)
    y0 = min(row[1] for row in rectangles)
    x1 = max(row[2] for row in rectangles)
    y1 = max(row[3] for row in rectangles)
    area = sum((row[2] - row[0]) * (row[3] - row[1]) for row in rectangles)
    if area != (x1 - x0) * (y1 - y0):
        raise ValueError("source glyph clip rectangles do not cover one rectangle")
    # Preserve the existing single-path ordering. Only a proved subdivision
    # needs a reconstructed outer boundary; no source segment is snapped.
    points = loops[0] if len(loops) == 1 else [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if points[-1] == points[0]:
        points = points[:-1]
    result = [(float(x), float(y)) for x, y in points]
    if len(set(result)) != 4:
        raise ValueError("source glyph clip cannot retain its rectangle coordinates")
    return points if exact else result
