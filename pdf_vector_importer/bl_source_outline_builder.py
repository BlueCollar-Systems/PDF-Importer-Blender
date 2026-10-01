"""Native outlines with source controls and independently checked filled boundaries.

This route deliberately carries renderer provenance, never font authenticity.
Bezier controls are retained by CURVE; the finite MESH boundary is checked as
an oriented triangle chain against the same native curve tessellation.
"""
from __future__ import annotations

from collections import Counter
from fractions import Fraction as F
import hashlib
import json
import math
import struct

from .text_delivery import AttemptOutcome

RESOLUTION = 32
MAX_PIECES = 4096


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
    _require(fill_rule in {"nonzero", "evenodd"}, "unsupported fill rule")
    _require(0 < len(contours) <= 64, "source contour count outside bound")
    rings, whole, pieces = [], [], []
    for ring_index, contour in enumerate(contours):
        _require(contour.get("closed") is True, "source contour not explicitly closed")
        convert = lambda p: tuple(F(_f32(v) if native else v) for v in
                                  (tuple(p) if coordinates_in_metres else _point(p)))
        start = convert(contour["start"])
        current, segments, parts = start, [], []
        _require(0 < len(contour["segments"]) <= 512, "source segment count outside bound")
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
    for i, a in enumerate(ordered):
        for b in ordered[i+1:]:
            if b[4] > a[5]:
                break
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
    contours = [{"start": ring[0], "closed": True,
                 "segments": [["L", p] for p in ring[1:]+ring[:1]]} for ring in polygons]
    proof = qualify_contours(contours, "nonzero", coordinates_in_metres=True)
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


def verify_mesh_ink(vertices, triangles, expected_polygons):
    """Exact oriented triangle-chain boundary, not counts or bounding boxes."""
    points = [tuple(F(float(x)) for x in p) for p in vertices]
    _require(points and len(points) <= 200000 and 0 < len(triangles) <= 400000, "native fill work bound")
    _require(all(len(p) == 3 and p[2] == 0 for p in points), "native ink has nonzero depth")
    edges, area, used = Counter(), F(0), set()
    signs = set()
    for triangle in triangles:
        _require(len(triangle) == 3 and len(set(triangle)) == 3, "invalid native fill triangle")
        _require(all(type(i) is int and 0 <= i < len(points) for i in triangle), "invalid native vertex index")
        a, b, c = (points[i] for i in triangle)
        signed = _cross(a, b, c)
        _require(signed != 0, "zero-area native triangle")
        signs.add(signed > 0)
        area += abs(signed)/2
        used.update(triangle)
        oriented = triangle if signed > 0 else tuple(reversed(triangle))
        for i, j in zip(oriented, oriented[1:]+oriented[:1], strict=False):
            edges[(points[i][:2], points[j][:2])] += 1
    _require(len(signs) == 1 and len(used) == len(points), "mixed winding or orphaned native vertices")
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
    return {"vertices": len(points), "triangles": len(triangles), "boundary_cycles": len(cycles),
            "area_m2": float(area), "exact_oriented_boundary_chain": True,
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
    _require(mat.name == obj["pdf_text_material"] and not mat.use_nodes,
             "source material replaced or textured")
    _require(tuple(mat.diffuse_color) == expected and tuple(obj.color) == expected,
             "native opaque source color changed")
    return {"material": mat.name, "rgba": list(expected), "opaque": True}


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
    return {"verified": True, "actual_object_type": obj.type, "outline_source": "source_renderer_svg",
            "font_program_authenticity": "absent", "source_outline_sha256": record["source_outline_sha256"],
            "source_placement_index": placement["index"], "material": material, "native_ink": ink,
            "entity_id": obj.name, "creation_world_matrix": saved["creation_matrix"],
            "actual_location_m": [float(v) for v in obj.location],
            "native_control_storage": _storage_bound(placement["contours"]),
            "source_cubic_area_m2": float(qualified["area_m2"]), "hole_count": qualified["holes"],
            "native_tessellation_area_difference_m2": ink["area_m2"]-float(qualified["area_m2"]),
            "finite_native_tessellation": True, "source_controls_float32_exact": obj.type == "CURVE"}


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
            rgba = tuple(_f32(v) for v in color)+(1.,)
            material = bpy.data.materials.new(name+"_ink"); blocks.append(material)
            material.use_nodes = False; material.diffuse_color = rgba
            obj.data.materials.append(material); obj.color = rgba
            bpy.context.view_layer.update()
            creation_matrix = _creation_matrix(z_offset_m)
            _require(_matrix(obj.matrix_world) == creation_matrix, "native creation transform differs from requested frame")
            mesh = obj.to_mesh()
            try:
                ink = _mesh_readback(mesh, polygons)
                if representation == "geometry":
                    retained = mesh.copy(); blocks.append(retained)
            finally:
                obj.to_mesh_clear()
            if representation == "geometry":
                converted = bpy.data.objects.new(name+"_mesh", retained)
                objects.append(converted); collection.objects.link(converted)
                converted.location = tuple(obj.location); converted.color = rgba
                bpy.data.objects.remove(obj, do_unlink=True)
                objects = [owned for owned in objects if owned is not obj]
                obj = converted
                # The exact-source curve is no longer required for scene delivery.
                bpy.data.curves.remove(data); blocks.remove(data)
                bpy.context.view_layer.update()
                _require(_matrix(obj.matrix_world) == creation_matrix, "native mesh transform differs from requested frame")
            obj["pdf_source_outline"] = True
            obj["pdf_text_mode"] = representation; obj["pdf_text_requested_mode"] = requested
            obj["pdf_source_item_id"] = record["item_id"]; obj["pdf_text_item_id"] = record["item_id"]
            obj["pdf_text_source"] = record["source_text"]; obj["pdf_page"] = record["page_number"]
            obj["pdf_source_placement_index"] = placement["index"]
            obj["pdf_source_outline_sha256"] = record["source_outline_sha256"]
            obj["pdf_text_material"] = material.name; obj["pdf_text_material_owned"] = True
            obj["pdf_text_expected_rgba"] = rgba
            if len(objects) == 1:
                obj["pdf_source_outline_source_record"] = _json(record)
            obj["pdf_source_outline_record"] = _json({"source_record_owner": objects[0].name, "placement": placement,
                "creation_matrix": creation_matrix, "initial_mesh_sha256": ink["mesh_sha256"]})
            evidence.append(verify_source_outline_entity(obj))
        return AttemptOutcome.delivered(objects[0], entity_ids=[o.name for o in objects],
            owned_objects=objects, owned_datablocks=blocks, evidence={"item_id": record["item_id"],
            "outline_source": "source_renderer_svg", "font_program_authenticity": "absent",
            "actual_object_type": "CURVE" if representation == "glyphs" else "MESH",
            "source_outline_sha256": record["source_outline_sha256"], "placements": evidence})
    except Exception as error:
        return AttemptOutcome.failed("source_outline_native_verification_failed",
            evidence={"exception_type": type(error).__name__, "detail": str(error)},
            owned_objects=objects, owned_datablocks=blocks)
