"""Native outlines with source controls and independently checked filled boundaries.

This route deliberately carries renderer provenance, never font authenticity.
Bezier controls are retained by CURVE; the finite MESH boundary is checked as
an oriented triangle chain against the same native curve tessellation.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from bisect import bisect_left, bisect_right
from copy import deepcopy
from fractions import Fraction as F
import hashlib
import json
import math
import struct
import sys
from threading import RLock

from .text_delivery import AttemptOutcome
from .visual_style import scene_linear_color

RESOLUTION = 32
MAX_PIECES = 4096
MAX_GEOMETRY_CANDIDATES = 2_000_000
_QUALIFICATION_SCHEMA = "source-outline-exact-qualification/2"
_CACHE_MAX_ENTRIES = 8192
_CACHE_MAX_BYTES = 256 * 1024 * 1024
_CACHE_INPUT_MAX_BYTES = 1024 * 1024
_CACHE_INPUT_MAX_NODES = 65536
_qualification_cache = OrderedDict()
_qualification_cache_bytes = 0
_qualification_cache_lock = RLock()


class _UncacheableInput(Exception):
    pass


def _qualification_snapshot(value, budget, active, depth=0):
    """Detach inputs; key equivalent sequences and mappings without changing values."""
    kind = type(value)
    if kind not in (type(None), bool, int, float, str, list, tuple, dict):
        raise _UncacheableInput
    budget[0] += 1
    budget[1] += sys.getsizeof(value)
    if depth > 32 or budget[0] > _CACHE_INPUT_MAX_NODES or budget[1] > _CACHE_INPUT_MAX_BYTES:
        raise _UncacheableInput
    if value is None:
        return None, ("none",)
    if kind in (bool, int, str):
        return value, (kind.__name__, value)
    if kind is float:
        if not math.isfinite(value):
            raise _UncacheableInput
        return value, ("float64", struct.pack(">d", value))
    if id(value) in active:
        raise _UncacheableInput
    active.add(id(value))
    try:
        if kind is dict:
            if any(type(key) is not str for key in value):
                raise _UncacheableInput
            rows = []
            for key, item in value.items():
                _qualification_snapshot(key, budget, active, depth+1)
                rows.append((key, _qualification_snapshot(item, budget, active, depth+1)))
            # Qualification reads fixed fields, so only the key ignores mapping order.
            # The detached cold operand retains the caller's original insertion order.
            return {key: item[0] for key, item in rows}, ("dict", tuple(sorted((key, item[1]) for key, item in rows)))
        rows = [_qualification_snapshot(item, budget, active, depth+1) for item in value]
        # Geometry consumes sequence order, not list/tuple identity. Retain that
        # original container in the detached cold operand, including nested points.
        return (tuple(item[0] for item in rows) if kind is tuple else [item[0] for item in rows],
                ("sequence", tuple(item[1] for item in rows)))
    finally:
        active.remove(id(value))


def _qualification_retained_bytes(value, seen=None, active=None):
    """Count the complete Python object graph once per entry, never across entries."""
    kind = type(value)
    if kind not in (F, list, tuple, dict, type(None), bool, int, float, str, bytes):
        raise _UncacheableInput
    seen = set() if seen is None else seen
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise _UncacheableInput
    if identity in seen:
        return 0
    seen.add(identity)
    active.add(identity)
    try:
        size = sys.getsizeof(value)
        if kind is F:
            children = (value.numerator, value.denominator)
        elif kind in (list, tuple):
            children = value
        elif kind is dict:
            children = (item for pair in value.items() for item in pair)
        else:
            children = ()
        return size + sum(_qualification_retained_bytes(item, seen, active) for item in children)
    finally:
        active.remove(identity)


def _clear_qualification_cache():
    global _qualification_cache_bytes
    with _qualification_cache_lock:
        _qualification_cache.clear()
        _qualification_cache_bytes = 0


def _cached_qualify_contours(contours, fill_rule, *, native, coordinates_in_metres, segment_limit):
    """Memoize only successful pure math, never native reads or delivery outcomes."""
    global _qualification_cache_bytes
    values = (contours, fill_rule, native, coordinates_in_metres, segment_limit,
              _QUALIFICATION_SCHEMA, RESOLUTION, MAX_PIECES, MAX_GEOMETRY_CANDIDATES)
    try:
        detached, key = _qualification_snapshot(values, [0, 0], set())
    except (_UncacheableInput, RuntimeError):
        return _qualify_contours(contours, fill_rule, native=native,
                                 coordinates_in_metres=coordinates_in_metres, segment_limit=segment_limit)
    with _qualification_cache_lock:
        entry = _qualification_cache.get(key)
        if entry is not None:
            _qualification_cache.move_to_end(key)
            return deepcopy(entry[0])
    result = _qualify_contours(detached[0], detached[1], native=detached[2],
                               coordinates_in_metres=detached[3], segment_limit=detached[4])
    retained = deepcopy(result)
    # Include the key, value tuple and cost integer together. The temporary
    # outer tuple and upper-sized integer deliberately overcount each entry.
    # This bounds accounted retained Python objects, not allocator/RSS usage.
    cost = _qualification_retained_bytes((key, (retained, sys.maxsize)))
    with _qualification_cache_lock:
        if (cost <= sys.maxsize and cost + sys.getsizeof(_qualification_cache) <= _CACHE_MAX_BYTES
                and _CACHE_MAX_ENTRIES > 0):
            previous = _qualification_cache.pop(key, None)
            if previous is not None:
                _qualification_cache_bytes -= previous[1]
            _qualification_cache[key] = (retained, cost)
            _qualification_cache_bytes += cost
            while (_qualification_cache and (len(_qualification_cache) > _CACHE_MAX_ENTRIES
                    or _qualification_cache_bytes + sys.getsizeof(_qualification_cache) > _CACHE_MAX_BYTES)):
                _, (_, removed_cost) = _qualification_cache.popitem(last=False)
                _qualification_cache_bytes -= removed_cost
    return deepcopy(result)


class OutlineVerificationError(ValueError):
    pass


class OutlineTopologyUnavailable(OutlineVerificationError):
    """Positive bounded source qualification stopped before native allocation."""


def _topology(value, reason):
    if not value:
        raise OutlineTopologyUnavailable(reason)


def _require(value, reason):
    if not value:
        raise OutlineVerificationError(reason)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _f32(value):
    value = float(value)
    _require(math.isfinite(value), "nonfinite source/native value")
    result = struct.unpack("<f", struct.pack("<f", value))[0]
    _require(math.isfinite(result), "native float32 overflow")
    return result


def _point(value):
    _require(len(value) == 2, "expected planar source point")
    point = tuple(float(v) / 1000 for v in value)
    _require(all(math.isfinite(v) for v in point), "nonfinite source point")
    return point


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def _area(poly):
    return sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(poly, poly[1:]+poly[:1], strict=False)) / 2


def _hull(points):
    points = sorted(set(points))
    halves = []
    for sequence in (points, list(reversed(points))):
        half = []
        for p in sequence:
            while len(half) > 1 and _cross(half[-2], half[-1], p) <= 0:
                half.pop()
            half.append(p)
        halves.append(half[:-1])
    return halves[0]+halves[1]


def _separate(a, b):
    # Exact separating-axis proof, including degenerate line hulls.
    for poly in (a, b):
        for p, q in zip(poly, poly[1:]+poly[:1], strict=False):
            dx, dy = q[0]-p[0], q[1]-p[1]
            for axis in ((dx, dy), (-dy, dx)):
                if axis == (0, 0):
                    continue
                aa = [v[0]*axis[0]+v[1]*axis[1] for v in a]
                bb = [v[0]*axis[0]+v[1]*axis[1] for v in b]
                if max(aa) < min(bb) or max(bb) < min(aa):
                    return True
    return False


def _adjacent_separate(a, b, joint):
    # A strict plane through the shared endpoint must separate all other poles.
    axes = [(b[-1][i]-a[0][i]) for i in range(2)]
    vectors_a = [tuple(joint[i]-p[i] for i in range(2)) for p in a if p != joint]
    vectors_b = [tuple(p[i]-joint[i] for i in range(2)) for p in b if p != joint]
    if not vectors_a or not vectors_b:
        return False
    vectors = vectors_a+vectors_b

    def separates(axis):
        return all(sum(v[i]*axis[i] for i in range(2)) > 0 for v in vectors)

    if separates(axes):
        return True
    # Unequal adjacent segment lengths can put that first direction outside
    # the feasible cone. Try exact interior directions between constraint rays.
    # Acceptance always requires the same strict dot tests, never an epsilon.
    rays = []
    for x, y in vectors:
        norm = abs(x)+abs(y)
        rays.extend(((-y/norm, x/norm), (y/norm, -x/norm)))
    return any(separates(v) for v in vectors) or any(
        separates((a[0]+b[0], a[1]+b[1]))
        for i, a in enumerate(rays) for b in rays[i+1:])


def _split(points):
    rows = [points]
    while len(rows[-1]) > 1:
        rows.append([tuple((a+b)/2 for a, b in zip(p, q, strict=False))
                     for p, q in zip(rows[-1], rows[-1][1:], strict=False)])
    return [r[0] for r in rows], [r[-1] for r in reversed(rows)]


def _pieces(points):
    if len(points) == 2:
        return [points]
    result = [points]
    for _ in range(5):  # Exactly RESOLUTION=32 dyadic subintervals, no sampling proof.
        result = [part for row in result for part in _split(row)]
    return result


def _injective_piece(points):
    # Nonnegative Bernstein derivative coefficients along the nonzero chord
    # imply a strictly positive projection derivative in the open interval:
    # their sum is the squared chord length. Zero endpoint derivatives are OK.
    chord = tuple(b-a for a, b in zip(points[0], points[-1], strict=True))
    return any(chord) and all(
        sum((b[i]-a[i])*chord[i] for i in range(2)) >= 0
        for a, b in zip(points, points[1:], strict=False))


def _inside(point, polygon):
    inside = False
    for a, b in zip(polygon, polygon[1:]+polygon[:1], strict=False):
        if (a[1] > point[1]) != (b[1] > point[1]):
            x = a[0]+(point[1]-a[1])*(b[0]-a[0])/(b[1]-a[1])
            if point[0] < x:
                inside = not inside
    return inside


def _curve_area(segments):
    """Exact Green integral from the full polynomial controls, in model m²."""
    result = F(0)
    for points in segments:
        if len(points) == 2:
            x = [points[0][0], points[1][0]-points[0][0]]
            y = [points[0][1], points[1][1]-points[0][1]]
        else:
            x, y = [], []
            for axis, out in ((0, x), (1, y)):
                a, b, c, d = [p[axis] for p in points]
                out.extend((a, 3*(b-a), 3*(a-2*b+c), -a+3*b-3*c+d))
        for i in range(len(x)):
            for j in range(1, len(y)):
                result += (x[i]*j*y[j]-y[i]*j*x[j]) / (i+j) / 2
    return result


def qualify_contours(contours, fill_rule, *, native=False, coordinates_in_metres=False):
    """Prove disjoint simple boundaries using exact rational Bezier pole hulls.

    Unknown touching/intersecting hulls fail closed; subdivisions never replace
    the retained source controls. Work is bounded per glyph.
    """
    return _cached_qualify_contours(contours, fill_rule, native=native,
                             coordinates_in_metres=coordinates_in_metres, segment_limit=512)


def _qualify_contours(contours, fill_rule, *, native, coordinates_in_metres, segment_limit):
    _require(fill_rule in {"nonzero", "evenodd"}, "unsupported fill rule")
    _require(0 < len(contours) <= 64, "source contour count outside bound")
    rings, whole, pieces = [], [], []
    for ring_index, contour in enumerate(contours):
        _require(contour.get("closed") is True, "source contour not explicitly closed")
        convert = lambda p: tuple(F(_f32(v) if native else v) for v in
                                  (tuple(p) if coordinates_in_metres else _point(p)))
        start = convert(contour["start"])
        current, segments, parts = start, [], []
        _topology(0 < len(contour["segments"]) <= segment_limit, "source segment count outside bound")
        for segment in contour["segments"]:
            _require(segment[0] in {"L", "C"} and len(segment) == (2 if segment[0] == "L" else 4),
                     "unsupported source curve command")
            controls = [current]+[convert(p) for p in segment[1:]]
            _topology(controls[0] != controls[-1], "collapsed source segment")
            segments.append(controls)
            parts.extend(_pieces(controls))
            _topology(len(parts)+len(pieces) <= MAX_PIECES, "source topology work bound exceeded")
            current = controls[-1]
        _require(current == start, "source closure segment missing")
        _require(len(parts) >= 3, "insufficient closed boundary")
        for i, (a, b) in enumerate(zip(parts, parts[1:]+parts[:1], strict=False)):
            _topology(_injective_piece(a), "unproved cubic-piece injectivity")
            _topology(_adjacent_separate(a, b, a[-1]), "unproved adjacent curve topology")
            hull = _hull(a)
            _require(len(hull) >= 2, "collapsed curve hull")
            pieces.append((ring_index, i, len(parts), hull,
                           min(p[0] for p in hull), max(p[0] for p in hull),
                           min(p[1] for p in hull), max(p[1] for p in hull)))
        rings.append([part[0] for part in parts])
        whole.append(segments)
    _require(len(pieces) <= MAX_PIECES, "source topology work bound exceeded")
    ordered = sorted(pieces, key=lambda p: p[4])
    candidates = 0
    for i, a in enumerate(ordered):
        for j in range(i+1, len(ordered)):
            b = ordered[j]
            if b[4] > a[5]:
                break
            candidates += 1
            _topology(candidates <= MAX_GEOMETRY_CANDIDATES, "source topology candidate work bound exceeded")
            if b[6] > a[7] or a[6] > b[7]:
                continue
            if a[0] == b[0] and (a[1]-b[1]) % a[2] in {1, a[2]-1}:
                continue
            _topology(_separate(a[3], b[3]), "intersecting or touching source boundaries")
    areas = [_curve_area(row) for row in whole]
    _require(all(areas), "zero source contour area")
    depth = [sum(_inside(ring[0], other) for j, other in enumerate(rings) if i != j)
             for i, ring in enumerate(rings)]
    if fill_rule == "nonzero":
        for i, ring in enumerate(rings):
            ancestors = [j for j, other in enumerate(rings) if i != j and _inside(ring[0], other)]
            winding = sum(1 if areas[j] > 0 else -1 for j in ancestors)
            inside_winding = winding+(1 if areas[i] > 0 else -1)
            _topology((winding == 0) != (inside_winding == 0), "redundant nonzero boundary unsupported")
    wanted = [1 if d % 2 == 0 else -1 for d in depth]
    return {"segments": whole, "depth": depth, "reverse": [(a > 0) != (w > 0) for a, w in zip(areas, wanted, strict=False)],
            "area_m2": sum(abs(a)*w for a, w in zip(areas, wanted, strict=False)), "holes": sum(d % 2 for d in depth)}


def _normalized_segments(qualified):
    return [[list(reversed(p)) for p in reversed(row)] if reverse else row
            for row, reverse in zip(qualified["segments"], qualified["reverse"], strict=False)]


def _read_curve(data, expected):
    _require(data.dimensions == "2D" and data.fill_mode == "BOTH" and data.extrude == 0
             and data.bevel_depth == 0 and data.resolution_u == RESOLUTION
             and data.render_resolution_u == RESOLUTION and data.offset == 0
             and data.bevel_object is None and data.taper_object is None,
             "native outline settings changed")
    _require(len(data.splines) == len(expected), "native contour count changed")
    for spline, segments in zip(data.splines, expected, strict=False):
        _require(spline.type == "BEZIER" and spline.use_cyclic_u
                 and spline.resolution_u == RESOLUTION, "native spline settings changed")
        _require(len(spline.bezier_points) == len(segments), "native source control count changed")
        for index, (point, segment) in enumerate(zip(spline.bezier_points, segments, strict=False)):
            previous = segments[index-1]
            _require(tuple(point.co) == tuple(map(float, segment[0]))+(0.,), "native endpoint changed")
            for kind, wanted in ((point.handle_right_type, segment),
                                 (point.handle_left_type, previous)):
                _require(kind == ("FREE" if len(wanted) == 4 else "VECTOR"), "native handle type changed")
            if len(segment) == 4:
                _require(tuple(point.handle_right) == tuple(map(float, segment[1]))+(0.,), "native cubic outgoing control changed")
            if len(previous) == 4:
                _require(tuple(point.handle_left) == tuple(map(float, previous[-2]))+(0.,), "native cubic incoming control changed")
            _require(point.tilt == 0 and point.radius == 1, "native point decoration changed")


def _native_polygons(segments):
    from mathutils import Vector
    from mathutils.geometry import interpolate_bezier
    result = []
    for ring in segments:
        polygon = []
        for points in ring:
            if len(points) == 2:
                polygon.append(tuple(map(float, points[0])))
            else:
                values = interpolate_bezier(*(Vector(tuple(map(float, p))+(0.,)) for p in points), RESOLUTION+1)
                _require(len(values) == RESOLUTION+1, "native interpolation census changed")
                polygon.extend(tuple(float(v) for v in p[:2]) for p in values[:-1])
        result.append(polygon)
    return result


def _polygon_qualification(polygons, expected_depth):
    # Cubics have already been qualified as complete source controls. Their
    # native finite boundary has RESOLUTION samples per cubic, not one command.
    _topology(0 < sum(map(len, polygons)) <= MAX_PIECES, "native polygon work bound exceeded")
    contours = [{"start": ring[0], "closed": True,
                 "segments": [["L", p] for p in ring[1:]+ring[:1]]} for ring in polygons]
    proof = _cached_qualify_contours(contours, "nonzero", native=False,
                              coordinates_in_metres=True, segment_limit=MAX_PIECES)
    _topology(proof["depth"] == expected_depth, "native tessellation changes source counter nesting")
    return proof


def _simplify(polygon):
    polygon = list(polygon)
    changed = True
    while changed and len(polygon) >= 3:
        changed = False
        for i in range(len(polygon)):
            a, b, c = polygon[i-1], polygon[i], polygon[(i+1) % len(polygon)]
            if b == a or (_cross(a, b, c) == 0 and sum((b[k]-a[k])*(b[k]-c[k]) for k in range(2)) <= 0):
                del polygon[i]
                changed = True
                break
    _require(len(polygon) >= 3 and _area(polygon), "collapsed native fill boundary")
    return polygon


def _cycle_key(polygon):
    polygon = _simplify(polygon)
    first = min(range(len(polygon)), key=polygon.__getitem__)
    return tuple(polygon[first:]+polygon[:first])


def _segment_triangle_interval(a, b, triangle):
    """Exact closed parameter interval lying in a positive oriented triangle."""
    low, high = F(0), F(1)
    for p, q in zip(triangle, triangle[1:]+triangle[:1], strict=True):
        start, end = _cross(p, q, a), _cross(p, q, b)
        delta = end-start
        if delta == 0:
            if start < 0:
                return None
        elif delta > 0:
            low = max(low, -start/delta)
        else:
            high = min(high, -start/delta)
        if low > high:
            return None
    return low, high


def _prove_degenerate_support(zero_triangles, positive_triangles, budget):
    # An exact zero integral alone does not certify a harmless native edge:
    # its entire segment must lie in the positively certified filled support.
    for triangle in zero_triangles:
        for a, b in zip(triangle, triangle[1:]+triangle[:1], strict=True):
            intervals = []
            for positive in positive_triangles:
                budget[0] += 1
                _require(budget[0] <= MAX_GEOMETRY_CANDIDATES, "native fill candidate work bound")
                if any(max(a[k], b[k]) < min(p[k] for p in positive)
                       or min(a[k], b[k]) > max(p[k] for p in positive) for k in range(2)):
                    continue
                interval = _segment_triangle_interval(a, b, positive)
                if interval is not None:
                    intervals.append(interval)
            covered = F(0)
            for low, high in sorted(intervals):
                _require(low <= covered, "zero-area native edge crosses unfilled source region")
                covered = max(covered, high)
                if covered == 1:
                    break
            _require(covered == 1, "zero-area native edge outside certified fill")


def _atomic_triangle_edges(triangles, points, budget):
    # Split collinear edges at actual native vertices so a long edge and its
    # oppositely oriented short neighbors cancel as the same geometric chain.
    # No coordinate or area is changed; broad-phase indexes only limit work.
    unique = sorted({p[:2] for p in points})
    axes = [sorted(unique, key=lambda p: p[k]) for k in range(2)]
    values = [[p[k] for p in axes[k]] for k in range(2)]
    result = Counter()
    for triangle in triangles:
        for a, b in zip(triangle, triangle[1:]+triangle[:1], strict=True):
            a, b = a[:2], b[:2]
            spans = [(bisect_left(values[k], min(a[k], b[k])),
                      bisect_right(values[k], max(a[k], b[k]))) for k in range(2)]
            axis = min(range(2), key=lambda k: spans[k][1]-spans[k][0])
            low, high = spans[axis]
            parameter_axis = 0 if a[0] != b[0] else 1
            split = []
            for i in range(low, high):
                budget[0] += 1
                _require(budget[0] <= MAX_GEOMETRY_CANDIDATES, "native fill candidate work bound")
                p = axes[axis][i]
                if (min(a[1-axis], b[1-axis]) <= p[1-axis] <= max(a[1-axis], b[1-axis])
                        and _cross(a, b, p) == 0):
                    split.append(((p[parameter_axis]-a[parameter_axis]) /
                                  (b[parameter_axis]-a[parameter_axis]), p))
            split.sort()
            _require(split[0] == (0, a) and split[-1] == (1, b), "native edge endpoints missing")
            for (_, p), (_, q) in zip(split, split[1:], strict=False):
                result[(p, q)] += 1
    return result


def verify_mesh_ink(vertices, triangles, expected_polygons):
    """Exact oriented triangle-chain boundary, not counts or bounding boxes."""
    points = [tuple(F(float(x)) for x in p) for p in vertices]
    _require(points and len(points) <= 200000 and 0 < len(triangles) <= 400000, "native fill work bound")
    _require(all(len(p) == 3 and p[2] == 0 for p in points), "native ink has nonzero depth")
    area, used = F(0), set()
    positive_triangles, zero_triangles = [], []
    signs = set()
    for triangle in triangles:
        _require(len(triangle) == 3 and len(set(triangle)) == 3, "invalid native fill triangle")
        _require(all(type(i) is int and 0 <= i < len(points) for i in triangle), "invalid native vertex index")
        a, b, c = (points[i] for i in triangle)
        signed = _cross(a, b, c)
        used.update(triangle)
        if signed == 0:
            zero_triangles.append((a, b, c))
            continue
        signs.add(signed > 0)
        area += abs(signed)/2
        oriented = triangle if signed > 0 else tuple(reversed(triangle))
        positive_triangles.append(tuple(points[i] for i in oriented))
    _require(len(signs) == 1 and len(used) == len(points), "mixed winding or orphaned native vertices")
    budget = [0]
    if zero_triangles:
        edges = _atomic_triangle_edges(positive_triangles, points, budget)
    else:
        # Ordinary triangulations retain the original exact chain proof. The
        # additional subdivision is needed only for collapsed native faces.
        edges = Counter((a[:2], b[:2]) for triangle in positive_triangles
                        for a, b in zip(triangle, triangle[1:]+triangle[:1], strict=True))
    boundary = {}
    for (a, b), count in edges.items():
        _require(count == 1, "duplicate overlapping triangle edge")
        if (b, a) not in edges:
            _require(a not in boundary, "branching native fill boundary")
            boundary[a] = b
    cycles = []
    while boundary:
        start = next(iter(boundary)); ring = [start]; current = boundary.pop(start)
        while current != start:
            _require(current in boundary and len(ring) <= len(points), "open native fill boundary")
            ring.append(current); current = boundary.pop(current)
        cycles.append(_cycle_key(ring))
    expected = [_cycle_key([tuple(F(x) for x in p) for p in ring]) for ring in expected_polygons]
    _require(Counter(cycles) == Counter(expected), "native filled boundary/counters differ from source tessellation")
    expected_area = sum(_area(list(ring)) for ring in expected)
    _require(area == expected_area > 0, "native filled area differs from complete source boundary")
    _prove_degenerate_support(zero_triangles, positive_triangles, budget)
    return {"vertices": len(points), "triangles": len(triangles), "boundary_cycles": len(cycles),
            "area_m2": float(area), "exact_oriented_boundary_chain": True,
            "positive_triangles": len(positive_triangles), "exact_zero_area_triangles": len(zero_triangles),
            "degenerate_segments_in_positive_fill": True, "candidate_checks": budget[0],
            "mesh_sha256": _digest({"vertices": vertices, "triangles": triangles})}


def _mesh_readback(mesh, polygons):
    mesh.calc_loop_triangles()
    vertices = [list(map(float, v.co)) for v in mesh.vertices]
    triangles = [tuple(int(v) for v in t.vertices) for t in mesh.loop_triangles]
    polygon_edges = {tuple(sorted((int(a), int(c)))) for p in mesh.polygons
                     for a, c in zip(tuple(p.vertices), tuple(p.vertices)[1:]+tuple(p.vertices)[:1], strict=True)}
    _require({tuple(sorted(map(int, e.vertices))) for e in mesh.edges} == polygon_edges,
             "native mesh contains loose or missing edges")
    _require(all(p.material_index == 0 for p in mesh.polygons), "native polygon source material changed")
    return verify_mesh_ink(vertices, triangles, polygons)


def _matrix(value):
    return [[float(v) for v in row] for row in value]


def _creation_matrix(z_offset_m):
    return [[1., 0., 0., 0.], [0., 1., 0., 0.],
            [0., 0., 1., _f32(z_offset_m)], [0., 0., 0., 1.]]


def _storage_bound(contours):
    # The exact source-to-float32 coordinate conversion is the only control
    # displacement. Outward-round a rational L1 bound for clear evidence.
    bound = F(0)
    for contour in contours:
        points = [contour["start"]]+[p for s in contour["segments"] for p in s[1:]]
        for point in points:
            converted = _point(point)
            bound = max(bound, sum(abs(F(_f32(v))-F(v)) for v in converted))
    value = math.nextafter(float(bound), math.inf) if bound else 0.
    return {"method": "exact_per_coordinate_binary32_rounding",
            "euclidean_upper_bound_m": value, "exact_l1_upper_bound": str(bound)}


def _verify_material(obj, color):
    expected = tuple(_f32(v) for v in color)+(1.,)
    _require(len(obj.data.materials) == 1, "source material slot changed")
    mat = obj.data.materials[0]
    _require(mat.name == obj["pdf_text_material"] and mat.use_nodes,
             "source material replaced or shader disabled")
    _require(tuple(mat.diffuse_color) == expected and tuple(obj.color) == expected,
             "native opaque source color changed")
    tree = mat.node_tree
    _require(tree is not None and len(tree.nodes) == 2 and len(tree.links) == 1,
             "source emission graph changed")
    emitters = [node for node in tree.nodes if node.bl_idname == "ShaderNodeEmission"]
    outputs = [node for node in tree.nodes if node.bl_idname == "ShaderNodeOutputMaterial"]
    _require(len(emitters) == len(outputs) == 1, "source constant emission nodes changed")
    emission, output = emitters[0], outputs[0]
    _require(not emission.mute and not output.mute and output.is_active_output and output.target == "ALL",
             "source emission output inactive")
    _require(tuple(emission.inputs["Color"].default_value) == expected
             and emission.inputs["Strength"].default_value == 1
             and all(not socket.is_linked for socket in emission.inputs),
             "source constant emission color or strength changed")
    link = next(iter(tree.links))
    _require(link.from_node == emission and link.to_node == output
             and link.from_socket == emission.outputs["Emission"]
             and link.to_socket == output.inputs["Surface"]
             and link.is_valid and not getattr(link, "is_muted", False), "source emission link changed")
    _require(all(socket.is_linked == (socket == output.inputs["Surface"]) for socket in output.inputs),
             "source output has extra shader inputs")
    for name, value in (("Displacement", (0., 0., 0.)), ("Thickness", 0.)):
        socket = output.inputs.get(name)
        if socket is not None:
            actual = socket.default_value
            _require((tuple(actual) if isinstance(value, tuple) else actual) == value,
                     "source output decoration changed")
    _require(getattr(mat, "animation_data", None) is None and getattr(tree, "animation_data", None) is None,
             "animated source material unsupported")
    return {"material": mat.name, "rgba": list(expected), "opaque": True,
            "shader": "constant_emission", "strength": 1., "exact_node_graph": True}


def verify_source_outline_entity(obj, *, expected_world_matrix=None, page_clip_verified=False):
    """Reread underlying controls and actual filled ink after stacking/reopen."""
    _require(obj.get("pdf_source_outline") is True, "source outline route missing")
    saved = json.loads(obj["pdf_source_outline_record"])
    import bpy
    owner = bpy.data.objects.get(saved["source_record_owner"])
    _require(owner is not None, "source outline record owner missing")
    record = json.loads(owner["pdf_source_outline_source_record"])
    placement = saved["placement"]
    raw = {k: v for k, v in record.items() if k != "source_outline_sha256"}
    _require(_digest(raw) == record["source_outline_sha256"] == obj["pdf_source_outline_sha256"],
             "source outline record changed")
    _require(placement in record["placements"], "source placement unbound")
    _require(obj["pdf_source_item_id"] == obj["pdf_text_item_id"] == record["item_id"]
             and obj["pdf_text_source"] == record["source_text"]
             and obj["pdf_page"] == record["page_number"]
             and obj["pdf_source_placement_index"] == placement["index"], "source semantic identity changed")
    _require(obj["pdf_text_mode"] in {"glyphs", "geometry"}, "invalid native outline mode")
    _require(obj.type == ("CURVE" if obj["pdf_text_mode"] == "glyphs" else "MESH"), "native representation changed")
    _require(not obj.hide_render and not obj.hide_viewport, "native source ink hidden")
    expected_matrix = saved["creation_matrix"] if expected_world_matrix is None else _matrix(expected_world_matrix)
    _require(_matrix(obj.matrix_world) == [[_f32(v) for v in row] for row in expected_matrix], "source placement transform changed")
    modifiers = list(obj.modifiers)
    _require(not modifiers or (page_clip_verified and len(modifiers) == 1 and modifiers[0].type == "NODES"),
             "unverified native modifier")
    qualified = qualify_contours(placement["contours"], placement["fill_rule"], native=True)
    expected = _normalized_segments(qualified)
    polygons = _native_polygons(expected)
    _polygon_qualification(polygons, qualified["depth"])
    material = _verify_material(obj, placement["color"])
    if obj.type == "CURVE":
        _read_curve(obj.data, expected)
        mesh = obj.to_mesh()
        try:
            ink = _mesh_readback(mesh, polygons)
        finally:
            obj.to_mesh_clear()
    else:
        ink = _mesh_readback(obj.data, polygons)
    _require(ink["mesh_sha256"] == saved["initial_mesh_sha256"], "native underlying fill geometry changed")
    result = {"verified": True, "actual_object_type": obj.type, "outline_source": "source_renderer_svg",
            "font_program_authenticity": "absent", "source_outline_sha256": record["source_outline_sha256"],
            "source_placement_index": placement["index"], "material": material, "native_ink": ink,
            "entity_id": obj.name, "creation_world_matrix": saved["creation_matrix"],
            "actual_location_m": [float(v) for v in obj.location],
            "native_control_storage": _storage_bound(placement["contours"]),
            "source_cubic_area_m2": float(qualified["area_m2"]), "hole_count": qualified["holes"],
            "native_tessellation_area_difference_m2": ink["area_m2"]-float(qualified["area_m2"]),
            "finite_native_tessellation": True, "source_controls_float32_exact": obj.type == "CURVE"}
    if "source_page_ledger" in record:
        result["source_page_ledger"] = record["source_page_ledger"]
    return result


def build_source_outlines(record, collection, *, representation, requested, z_offset_m=0.0):
    """Create one source-owned object per visible glyph; return all owned resources."""
    objects, blocks = [], []
    try:
        import bpy
        _require(representation in {"glyphs", "geometry"}, "invalid source outline representation")
        _require(_digest({k: v for k, v in record.items() if k != "source_outline_sha256"})
                 == record["source_outline_sha256"], "source record hash mismatch")
        _require(record["source_font_absence"] and record["placements"], "no positively qualified visible source outline")
        absence = record["source_font_absence"]
        empty_font = (absence.get("reason") == "embedded_font_asset_build_failed"
                      and absence.get("proof_category") == "source_specific_impossibility"
                      and absence.get("detail") == "embedded font stream is empty"
                      and type(absence.get("source_xref")) is int and absence["source_xref"] > 0)
        missing_font = (absence.get("reason") == "no_exact_embedded_font_match"
                        and absence.get("proof_category") == "source_font_absent_for_item")
        _require((empty_font or missing_font) and absence.get("source_page") == record["page_number"],
                 "source font absence unproved for this page")
        _require(len(record["placements"]) <= 4096, "item placement work bound")
        _require(len({p["index"] for p in record["placements"]}) == len(record["placements"]), "duplicate source placement")
        prepared = []
        for placement in record["placements"]:
            try:
                source = qualify_contours(placement["contours"], placement["fill_rule"])
                native = qualify_contours(placement["contours"], placement["fill_rule"], native=True)
                _topology(source["depth"] == native["depth"] and source["reverse"] == native["reverse"],
                          "float32 storage changes source topology")
                expected = _normalized_segments(native)
                polygons = _native_polygons(expected)
                _polygon_qualification(polygons, native["depth"])
            except OutlineTopologyUnavailable as error:
                return AttemptOutcome.impossible("source_outline_topology_unavailable_for_item", evidence={
                    "importer_id": "bc_pdf_vector_importer.blender", "item_id": record["item_id"],
                    "page_number": record["page_number"], "source_span_id": int(record["item_id"].rsplit(":", 1)[1]),
                    "proof_category": "source_outline_topology_unsupported", "native_entities_created": 0,
                    "pdf_sha256": record["pdf_sha256"], "svg_sha256": record["svg_sha256"],
                    "source_outline_sha256": record["source_outline_sha256"],
                    "source_font_absence": record["source_font_absence"], "detail": str(error),
                    "source_placement_index": placement["index"], "requested_representation": representation})
            prepared.append((placement, native, polygons))
        evidence = []
        pending = []
        for placement, native, polygons in prepared:
            expected = _normalized_segments(native)
            name = "PDF_Outline_%s_%d" % (record["item_id"], placement["index"])
            data = bpy.data.curves.new(name, "CURVE"); blocks.append(data)
            data.dimensions = "2D"; data.fill_mode = "BOTH"
            data.extrude = 0; data.bevel_depth = 0; data.resolution_u = RESOLUTION
            data.render_resolution_u = RESOLUTION
            data.offset = 0; data.bevel_object = None; data.taper_object = None
            for segments in expected:
                spline = data.splines.new("BEZIER")
                spline.bezier_points.add(len(segments)-1)
                spline.use_cyclic_u = True; spline.resolution_u = RESOLUTION
                for point, segment in zip(spline.bezier_points, segments, strict=False):
                    point.co = tuple(map(float, segment[0]))+(0.,)
                for i, (point, segment) in enumerate(zip(spline.bezier_points, segments, strict=False)):
                    previous = segments[i-1]
                    point.handle_left_type = "FREE" if len(previous) == 4 else "VECTOR"
                    point.handle_right_type = "FREE" if len(segment) == 4 else "VECTOR"
                    if len(previous) == 4:
                        point.handle_left = tuple(map(float, previous[-2]))+(0.,)
                    if len(segment) == 4:
                        point.handle_right = tuple(map(float, segment[1]))+(0.,)
                    point.tilt = 0; point.radius = 1
            _read_curve(data, expected)
            obj = bpy.data.objects.new(name, data); objects.append(obj); collection.objects.link(obj)
            obj.location = (0., 0., _f32(z_offset_m))
            color = placement["color"]
            _require(len(color) == 3 and all(math.isfinite(v) and 0 <= v <= 1 for v in color), "invalid source color")
            rgba = tuple(_f32(v) for v in scene_linear_color(tuple(color)))+(1.,)
            material = bpy.data.materials.new(name+"_ink"); blocks.append(material)
            material.use_nodes = True; material.diffuse_color = rgba
            nodes, links = material.node_tree.nodes, material.node_tree.links
            nodes.clear()
            emission = nodes.new(type="ShaderNodeEmission")
            output = nodes.new(type="ShaderNodeOutputMaterial")
            output.is_active_output = True; output.target = "ALL"
            emission.inputs["Color"].default_value = rgba
            emission.inputs["Strength"].default_value = 1.
            links.new(emission.outputs["Emission"], output.inputs["Surface"])
            obj.data.materials.append(material); obj.color = rgba
            pending.append((name, placement, native, polygons, obj, data, material, rgba))
        bpy.context.view_layer.update()
        creation_matrix = _creation_matrix(z_offset_m)
        proved = []
        # Complete every original Curve proof before any conversion invalidates
        # the dependency graph. Retain each copied mesh before clearing its temporary.
        for name, placement, _native, polygons, obj, data, material, rgba in pending:
            _require(_matrix(obj.matrix_world) == creation_matrix, "native creation transform differs from requested frame")
            mesh = obj.to_mesh()
            retained = None
            try:
                ink = _mesh_readback(mesh, polygons)
                if representation == "geometry":
                    retained = mesh.copy(); blocks.append(retained)
            finally:
                obj.to_mesh_clear()
            proved.append((name, placement, obj, data, material, rgba, ink, retained))
        final_objects = []
        for name, _placement, obj, data, _material, rgba, _ink, retained in proved:
            if representation == "geometry":
                converted = bpy.data.objects.new(name+"_mesh", retained)
                objects.append(converted); collection.objects.link(converted)
                converted.location = tuple(obj.location); converted.color = rgba
                bpy.data.objects.remove(obj, do_unlink=True)
                objects = [owned for owned in objects if owned is not obj]
                obj = converted
                bpy.data.curves.remove(data); blocks.remove(data)
            final_objects.append(obj)
        if representation == "geometry":
            bpy.context.view_layer.update()
            for obj in final_objects:
                _require(_matrix(obj.matrix_world) == creation_matrix, "native mesh transform differs from requested frame")
        # The final source owner is explicit: creation is no longer serial with verification.
        final_objects[0]["pdf_source_outline_source_record"] = _json(record)
        for row, obj in zip(proved, final_objects, strict=True):
            _name, placement, _curve, _data, material, rgba, ink, _retained = row
            obj["pdf_source_outline"] = True
            obj["pdf_text_mode"] = representation; obj["pdf_text_requested_mode"] = requested
            obj["pdf_source_item_id"] = record["item_id"]; obj["pdf_text_item_id"] = record["item_id"]
            obj["pdf_text_source"] = record["source_text"]; obj["pdf_page"] = record["page_number"]
            obj["pdf_source_placement_index"] = placement["index"]
            obj["pdf_source_outline_sha256"] = record["source_outline_sha256"]
            obj["pdf_text_material"] = material.name; obj["pdf_text_material_owned"] = True
            obj["pdf_text_expected_rgba"] = rgba
            obj["pdf_source_outline_record"] = _json({"source_record_owner": final_objects[0].name, "placement": placement,
                "creation_matrix": creation_matrix, "initial_mesh_sha256": ink["mesh_sha256"]})
        for obj in final_objects:
            evidence.append(verify_source_outline_entity(obj))
        return AttemptOutcome.delivered(final_objects[0], entity_ids=[o.name for o in final_objects],
            owned_objects=objects, owned_datablocks=blocks, evidence={"item_id": record["item_id"],
            "outline_source": "source_renderer_svg", "font_program_authenticity": "absent",
            "actual_object_type": "CURVE" if representation == "glyphs" else "MESH",
            "source_outline_sha256": record["source_outline_sha256"], "placements": evidence})
    except Exception as error:
        return AttemptOutcome.failed("source_outline_native_verification_failed",
            evidence={"exception_type": type(error).__name__, "detail": str(error)},
            owned_objects=objects, owned_datablocks=blocks)
