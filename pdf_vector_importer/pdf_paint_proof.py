"""Bounded renderer evidence for final unclipped rectangular PDF paints."""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_RECT = re.compile(r"\s*M\s*(" + _NUMBER + r")[ ,]+(" + _NUMBER + r")\s*H\s*(" + _NUMBER
                   + r")\s*V\s*(" + _NUMBER + r")\s*H\s*(" + _NUMBER + r")\s*Z\s*")


def _rgb(value):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return None
    return tuple(int(value[i:i+2], 16) / 255 for i in (1, 3, 5))


def _close(a, b, tolerance):
    return len(a) == len(b) and all(math.isfinite(x) and math.isfinite(y)
                                  and abs(x-y) <= tolerance for x, y in zip(a, b, strict=True))


def rect_pair_matches(fill, stroke, raw):
    """Verify a renderer pair against raw paint without assuming PDF defaults."""
    try:
        if any(key in node.attrib for node in (fill, stroke) for key in ("clip-path", "mask", "filter", "opacity")):
            return False
        if fill.tag.rsplit("}", 1)[-1] != "path" or stroke.tag.rsplit("}", 1)[-1] != "path":
            return False
        if fill.get("d") != stroke.get("d") or fill.get("transform") != stroke.get("transform"):
            return False
        match = _RECT.fullmatch(fill.get("d", ""))
        if match is None:
            return False
        x0, y0, x1, y1, final_x = map(float, match.groups())
        if x0 != final_x or x0 == x1 or y0 == y1:
            return False
        transform = fill.get("transform", "")
        if not transform.startswith("matrix(") or not transform.endswith(")"):
            return False
        a, b, c, d, e, f = [float(v) for v in re.split(r"[ ,]+", transform[7:-1].strip())]
        if not all(math.isfinite(v) for v in (a, b, c, d, e, f)):
            return False
        scale2, second = a*a+b*b, c*c+d*d
        if scale2 <= 0 or abs(scale2-second) > scale2*1e-8 or abs(a*c+b*d) > scale2*1e-8:
            return False
        corners = [(a*x+c*y+e, b*x+d*y+f) for x, y in ((x0,y0),(x1,y0),(x1,y1),(x0,y1))]
        xs, ys = zip(*corners, strict=True)
        bounds = (min(xs), min(ys), max(xs), max(ys))
        if not _close(bounds, tuple(raw["rect"]), .001):
            return False
        miter = float(stroke.get("stroke-miterlimit", "nan"))
        if stroke.get("stroke-linejoin") != "miter" or not math.isfinite(miter) or miter < math.sqrt(2):
            return False
        if stroke.get("stroke-dasharray") or stroke.get("fill") != "none":
            return False
        if not _close((float(stroke.get("stroke-width", "nan"))*math.sqrt(scale2),), (float(raw["width"]),), .0001):
            return False
        if not _close((float(fill.get("fill-opacity", "1")),), (float(raw["fill_opacity"]),), 1e-5):
            return False
        if float(stroke.get("stroke-opacity", "1")) != 1:
            return False
        fill_rgb, stroke_rgb = _rgb(fill.get("fill")), _rgb(stroke.get("stroke"))
        return (fill_rgb is not None and stroke_rgb is not None
                and _close(fill_rgb, tuple(raw["fill"]), 1/255 + 1e-6)
                and _close(stroke_rgb, tuple(raw["color"]), 1/255 + 1e-6))
    except (TypeError, ValueError, KeyError, OverflowError):
        return False


def final_svg_rectangles(svg, rows):
    """Require direct-root final path pairs: no inherited clipping or groups."""
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return False
    if any(key in root.attrib for key in ("clip-path", "mask", "filter", "opacity", "transform")):
        return False
    count = len(rows)
    children = list(root)
    if not count or len(children) < 2*count:
        return False
    tail = children[-2*count:]
    return all(rect_pair_matches(tail[2*i], tail[2*i+1], row) for i, row in enumerate(rows))
