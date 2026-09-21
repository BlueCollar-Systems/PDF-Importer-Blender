"""Bounded opaque source rectangles followed only by independently owned text.

The rectangle and its original border stay editable. Unknown later overlap is
not reordered. This is not a general PDF painter or transparency compositor.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET

from .image_paint_order import (
    _matrix, _multiply, _point, _bounds, _box, _close,
    _intersects, _contains, _clip_quad, _neutral,
)
from .nontext_composite import _source_group_declarations
from .pdf_paint_proof import _RECT, _rgb

def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on(a, b, p):
    return (
        _cross(a, b, p) == 0.0
        and min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
    )


def _segments_intersect(a, b, c, d):
    if (
        max(a[0], b[0]) < min(c[0], d[0])
        or max(c[0], d[0]) < min(a[0], b[0])
        or max(a[1], b[1]) < min(c[1], d[1])
        or max(c[1], d[1]) < min(a[1], b[1])
    ):
        return False
    ab_c, ab_d, cd_a, cd_b = (
        _cross(a, b, c),
        _cross(a, b, d),
        _cross(c, d, a),
        _cross(c, d, b),
    )
    return (
        (ab_c * ab_d < 0 and cd_a * cd_b < 0)
        or _on(a, b, c)
        or _on(a, b, d)
        or _on(c, d, a)
        or _on(c, d, b)
    )


def _clip_excludes_region(clip, region):
    """Exact straight-path clip winding proves an entire rectangle is unpainted.

    PDF clipping implicitly closes subpaths. No tolerance, fitting, native
    geometry change or inference from the clip's broad bounding box is used.
    """
    items = clip.get("items", ())
    if (
        not items
        or type(clip.get("even_odd")) is not bool
        or any(i[0] != "l" or len(i) != 3 for i in items)
    ):
        return None
    contours = []
    current = []
    for _, start, end in items:
        a, b = tuple(start), tuple(end)
        if not all(math.isfinite(v) for p in (a, b) for v in p):
            return None
        if current and current[-1] != a:
            if current[-1] != current[0]:
                current.append(current[0])
            contours.append(current)
            current = []
        if not current:
            current = [a]
        current.append(b)
    if current:
        if current[-1] != current[0]:
            current.append(current[0])
        contours.append(current)
    if any(len(row) < 4 for row in contours):
        return None
    x0, y0, x1, y1 = region
    box = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    segments = [
        (a, b) for row in contours for a, b in zip(row, row[1:], strict=False) if a != b
    ]
    if any(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for row in contours for p in row):
        return None
    if any(
        _segments_intersect(a, b, c, d)
        for a, b in segments
        for c, d in zip(box, box[1:], strict=False)
    ):
        return None
    point = ((x0 + x1) / 2, (y0 + y1) / 2)
    x, y = point
    winding = 0
    for a, b in segments:
        if _on(a, b, point):
            return None
        cross = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])
        if a[1] <= y < b[1] and cross > 0:
            winding += 1
        elif b[1] <= y < a[1] and cross < 0:
            winding -= 1
    if (winding % 2 != 0) if clip["even_odd"] else (winding != 0):
        return None
    return dict(
        fill_rule="evenodd" if clip["even_odd"] else "nonzero",
        contours=contours,
        source_level=clip.get("level"),
        region_pdf=list(region),
        winding=winding,
    )


def _text_items(trace, rawdict, page_number):
    """Partition one original text trace across exact rawdict span occurrences."""
    chars = [(int(row[0]), tuple(row[2])) for row in trace.get("chars", ())]
    if not chars or trace.get("type") != 0 or trace.get("opacity") != 1:
        return None
    candidates = []
    for bi, block in enumerate(rawdict.get("blocks", ())):
        if block.get("type") != 0:
            continue
        for li, line in enumerate(block.get("lines", ())):
            for si, span in enumerate(line.get("spans", ())):
                layout = [
                    (ord(c["c"]), tuple(c["origin"])) for c in span.get("chars", ())
                ]
                if not layout:
                    continue
                for start in range(len(chars) - len(layout) + 1):
                    if all(
                        a[0] == b[0] and _close(a[1], b[1])
                        for a, b in zip(
                            layout, chars[start : start + len(layout)], strict=False
                        )
                    ):
                        candidates.append(
                            (
                                start,
                                start + len(layout),
                                dict(
                                    source_item_id=f"p{page_number}:b{bi}:l{li}:s{si}",
                                    text="".join(chr(c[0]) for c in layout),
                                    bbox_pdf=list(span["bbox"]),
                                    chars=layout,
                                ),
                            )
                        )
    cursor, result = 0, []
    while cursor < len(chars):
        matches = [row for row in candidates if row[0] == cursor]
        if len(matches) != 1:
            return None
        start, end, item = matches[0]
        if any(
            a < end and b > start for a, b, _ in candidates if (a, b) != (start, end)
        ):
            return None
        result.append(item)
        cursor = end
    return result


def _source_path_row(raw, order, kind):
    """Join only a path's own paint event, including the stroke half of fs.

    Text/image events do not inherit the preceding path's clip: a Q operator
    can have restored the graphics state between those two source paints.
    """
    row = raw.get(order)
    if row is not None and (
        (kind == "fill-path" and row.get("type") in ("f", "fs"))
        or (kind == "stroke-path" and row.get("type") == "s")
    ):
        return row
    previous = raw.get(order - 1)
    if kind == "stroke-path" and previous is not None and previous.get("type") == "fs":
        return previous
    return None


def _svg_rects(svg):
    # The proof understands one page viewport and unambiguous local clips;
    # external/document CSS must not silently override these paint attributes.
    if re.search(r"<\?xml-stylesheet(?:\s|\?>)", svg):
        return []
    root = ET.fromstring(svg)
    if root.tag.rsplit("}", 1)[-1] != "svg" or not _neutral(
        root, {"version", "width", "height", "viewBox", "opacity"}
    ):
        return []
    ids = {}
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "style" or (tag == "svg" and node is not root):
            return []
        node_id = node.get("id")
        if node_id:
            if node_id in ids:
                return []
            ids[node_id] = node
    result = []
    identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    def visit(node, matrix, clips, safe):
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in ("defs", "clipPath", "mask", "symbol"):
            return
        if tag == "g":
            safe = safe and _neutral(node, {"id", "transform", "clip-path", "opacity"})
            matrix = _multiply(matrix, _matrix(node.get("transform")))
            if node.get("clip-path"):
                match = re.fullmatch(r"url\(#([^()]+)\)", node.get("clip-path"))
                try:
                    clips = clips + [_clip_quad(ids[match[1]], matrix)] if match else []
                    safe = safe and match is not None
                except (ValueError, KeyError):
                    safe = False
        elif tag == "path" and safe:
            allowed = {
                "d",
                "transform",
                "fill",
                "fill-opacity",
                "fill-rule",
                "stroke",
                "stroke-opacity",
                "stroke-width",
                "stroke-linecap",
                "stroke-linejoin",
                "stroke-miterlimit",
            }
            match = _RECT.fullmatch(node.get("d", ""))
            if not match or set(node.attrib) - allowed:
                return
            x0, y0, x1, y1, last_x = map(float, match.groups())
            if x0 == x1 or y0 == y1 or last_x != x0:
                return
            transform = _multiply(matrix, _matrix(node.get("transform")))
            quad = [
                _point(transform, p) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
            ]
            stroke = node.get("fill") == "none" and node.get("stroke") is not None
            width = float(node.get("stroke-width", "0"))
            if stroke:
                if (
                    width <= 0
                    or node.get("stroke-linejoin") != "miter"
                    or float(node.get("stroke-miterlimit", "0")) < math.sqrt(2)
                ):
                    return
                lo_x, hi_x = sorted((x0, x1))
                lo_y, hi_y = sorted((y0, y1))
                half = width / 2
                paint_quad = [
                    _point(transform, p)
                    for p in (
                        (lo_x - half, lo_y - half),
                        (hi_x + half, lo_y - half),
                        (hi_x + half, hi_y + half),
                        (lo_x - half, hi_y + half),
                    )
                ]
            else:
                if node.get("stroke") is not None or node.get("fill") == "none":
                    return
                paint_quad = quad
            rgb = _rgb(node.get("stroke" if stroke else "fill", "#000000"))
            alpha = float(node.get("stroke-opacity" if stroke else "fill-opacity", "1"))
            if rgb is not None and alpha == 1:
                result.append(
                    dict(
                        kind="stroke" if stroke else "fill",
                        bounds=_bounds(quad),
                        quad=quad,
                        paint_bounds=_bounds(paint_quad),
                        rgb=rgb,
                        full_clip=all(_contains(clip, paint_quad) for clip in clips),
                        width=width
                        * math.sqrt(
                            abs(
                                transform[0] * transform[3]
                                - transform[1] * transform[2]
                            )
                        ),
                        signature=(node.get("d"), transform),
                    )
                )
            return
        elif tag != "svg":
            return
        for child in node:
            visit(child, matrix, clips, safe)

    visit(root, identity, [], True)
    return result


def plan_opaque_rectangles(page, source_sha256):
    if re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None:
        raise ValueError("Invalid source rectangle identity")
    if int(page.rotation) or tuple(page.rect)[:2] != (0.0, 0.0):
        return []
    try:
        _source_group_declarations(page)
    except ValueError:
        return []
    svg = page.get_svg_image(text_as_path=False)
    proofs = _svg_rects(svg)
    drawings = page.get_drawings()
    raw = {row["seqno"]: row for row in drawings}
    log = [(kind, _box(box)) for kind, box in page.get_bboxlog()]
    traces = {row["seqno"]: row for row in page.get_texttrace()}
    rawdict = page.get_text("rawdict")
    extended = page.get_drawings(extended=True)
    bad_groups = [
        row
        for row in extended
        if row.get("type") == "group"
        and (
            row.get("blendmode", "Normal") != "Normal"
            or row.get("opacity", 1) != 1
            or row.get("knockout", False)
        )
    ]
    active = []
    clip_map = {}
    for row in extended:
        level = row.get("level", 0)
        active = [previous for previous in active if previous.get("level", 0) < level]
        if row.get("type") in ("clip", "group"):
            active.append(row)
        elif "seqno" in row:
            clip_map[row["seqno"]] = [r for r in active if r.get("type") == "clip"]
    matches = {}
    for row in drawings:
        items = row.get("items", ())
        if len(items) != 1 or items[0][0] != "re":
            continue
        bounds = tuple(items[0][1])
        for kind in ("fill", "stroke"):
            color = row.get("fill" if kind == "fill" else "color")
            if (
                color is None
                or row.get("fill_opacity" if kind == "fill" else "stroke_opacity") != 1
            ):
                continue
            candidates = [
                p
                for p in proofs
                if p["kind"] == kind
                and _close(p["bounds"], bounds)
                and all(
                    abs(a - b) <= 1 / 255 + 1e-6
                    for a, b in zip(p["rgb"], color, strict=False)
                )
                and (kind != "stroke" or _close((p["width"],), (row["width"],)))
            ]
            if len(candidates) == 1:
                matches[(row["seqno"], kind)] = candidates[0]
    # A renderer path cannot certify two coincident source paints.
    counts = {}
    for proof in matches.values():
        counts[id(proof)] = counts.get(id(proof), 0) + 1
    matches = {key: value for key, value in matches.items() if counts[id(value)] == 1}
    results = []
    for row in drawings:
        seq = row["seqno"]
        fill, stroke = matches.get((seq, "fill")), matches.get((seq, "stroke"))
        if (
            row.get("type") not in ("f", "fs")
            or fill is None
            or not fill["full_clip"]
            or (
                row["type"] == "fs"
                and (stroke is None or fill["signature"] != stroke["signature"])
            )
        ):
            continue
        region = stroke["paint_bounds"] if row["type"] == "fs" else fill["paint_bounds"]
        if not _contains(
            [
                (page.rect.x0, page.rect.y0),
                (page.rect.x1, page.rect.y0),
                (page.rect.x1, page.rect.y1),
                (page.rect.x0, page.rect.y1),
            ],
            [
                (region[0], region[1]),
                (region[2], region[1]),
                (region[2], region[3]),
                (region[0], region[3]),
            ],
        ):
            continue
        later = {}
        eligible = True
        while True:
            before = region
            excluded = {}
            for order in range(seq + 1 + (row["type"] == "fs"), len(log)):
                kind, box = log[order]
                if not _intersects(region, box) or kind == "ignore-text":
                    continue
                raw_row = _source_path_row(raw, order, kind)
                clip_proofs = [
                    e
                    for c in (
                        clip_map.get(raw_row["seqno"], ())
                        if raw_row is not None
                        else ()
                    )
                    if (e := _clip_excludes_region(c, region)) is not None
                ]
                if clip_proofs:
                    encoded = json.dumps(clip_proofs, sort_keys=True)
                    excluded[order] = dict(
                        source_paint_order=order,
                        proof_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
                        source_clip_proofs=clip_proofs,
                    )
                    continue
                if kind == "stroke-path":
                    earlier = raw_row
                    refined = (
                        matches.get((earlier["seqno"], "stroke")) if earlier else None
                    )
                    if refined is not None and not _intersects(
                        region, refined["paint_bounds"]
                    ):
                        continue
                if kind != "fill-text":
                    eligible = False
                    break
                items = _text_items(traces.get(order, {}), rawdict, page.number + 1)
                if not items:
                    eligible = False
                    break
                later[order] = dict(source_paint_order=order, items=items)
                # Preserve the entire original trace, and include every later
                # dependency of its actual source paint bounds. Font metric
                # span rectangles do not define painted glyph extents.
                region = (
                    min(region[0], box[0]),
                    min(region[1], box[1]),
                    max(region[2], box[2]),
                    max(region[3], box[3]),
                )
            if not eligible or region == before:
                break
        if (
            not eligible
            or not later
            or any(_intersects(region, tuple(g["rect"])) for g in bad_groups)
        ):
            continue
        results.append(
            dict(
                schema="source-opaque-rectangle-order/1",
                source_sha256=source_sha256,
                page=page.number + 1,
                source_draw_order=seq,
                source_paint_order=seq,
                source_bbox_pdf=list(row["rect"]),
                source_quad_pdf=[
                    (row["rect"].x0, row["rect"].y0),
                    (row["rect"].x1, row["rect"].y0),
                    (row["rect"].x1, row["rect"].y1),
                    (row["rect"].x0, row["rect"].y1),
                ],
                fill_rgb=list(row["fill"]),
                stroke_rgb=list(row["color"]) if row["type"] == "fs" else None,
                stroke_width=float(row["width"]) if row["type"] == "fs" else 0.0,
                paint_bounds_pdf=list(region),
                later_text=[later[key] for key in sorted(later)],
                original_stroke_fully_unclipped=stroke["full_clip"] if stroke else None,
                border_display_contract="retained native editable centerline and viewport lineweight",
                clip_disjoint_later_paints=[excluded[key] for key in sorted(excluded)],
                source_svg_sha256=hashlib.sha256(svg.encode()).hexdigest(),
            )
        )
    return results
