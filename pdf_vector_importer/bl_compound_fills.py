"""Keep the contours of one ordinary even-odd PDF fill together in Blender.

The shared extractor retains each contour's source paint occurrence. Blender
must fill that occurrence once: making a separate face for an inner contour
paints over the counter instead of leaving a hole. This host-only adaptation
keeps the extracted vertices and the caller's PageData unchanged.
"""
from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
import math
import re
import xml.etree.ElementTree as ET

from .pdfcadcore.drawing_clips import _contours


class CompoundTopologyBudgetExceeded(ValueError):
    """Source paths are retained, but their filled native topology is unproven."""


class _TopologyBudget:
    def __init__(self, progress_callback=None, *, point_budget=65536, work_budget=131072,
                 candidate_budget=32768, split_budget=1024):
        self.budgets = dict(points=point_budget, work=work_budget,
                            candidates=candidate_budget, splits=split_budget)
        if any(type(v) is not int or v < 1 for v in self.budgets.values()):
            raise ValueError("Compound fill topology budgets must be positive integers")
        self.counts = dict.fromkeys(self.budgets, 0)
        self.progress_callback = progress_callback

    def charge(self, kind, amount=1):
        self.counts[kind] += amount
        if self.counts[kind] > self.budgets[kind]:
            raise CompoundTopologyBudgetExceeded(
                f"Compound fill topology {kind} budget exceeded "
                f"({self.counts[kind]} > {self.budgets[kind]}); original source paths retained")
        if self.progress_callback is not None and (self.counts[kind] % 128 == 1 or amount > 128):
            if self.progress_callback() is False:
                from .import_session import ImportCancelledError

                raise ImportCancelledError("PDF import cancelled during compound fill topology")


def grouped_fill_primitives(page_data, *, progress_callback=None, limits=None, topology_cache=None,
                            nonzero_windings=None):
    primitives = list(page_data.primitives or ())
    by_order = {}
    for primitive in primitives:
        if not primitive.clip_fill_group_id and primitive.fill_color is not None:
            by_order.setdefault(primitive.source_draw_order, []).append(primitive)

    replacements = {}
    seen = set()
    for row in getattr(page_data, "_source_drawings", ()) or ():
        order = row.get("seqno")
        if (type(order) is not int or row.get("type") != "f"
                or type(row.get("even_odd")) is not bool or row.get("fill") is None
                or row.get("bcs_compound_clip_fill")):
            continue
        try:
            contours, _curved = _contours(row.get("items") or ())
        except (ValueError, TypeError, IndexError):
            # Unsupported source commands do not establish compound membership.
            continue
        if not contours:
            continue
        if len(contours) == 1 and not row["even_odd"]:
            continue  # Existing simple nonzero fills keep their owned faces.
        if order in seen:
            raise ValueError("Ordinary compound fill has ambiguous source paint order")
        seen.add(order)
        members = by_order.get(order, ())
        if (len(members) != len(contours) or len({p.id for p in members}) != len(members)
                or any(p.stroke_color is not None or len(p.points) < 3 for p in members)
                or any(p.fill_color != members[0].fill_color
                       or p.source_fill_color != members[0].source_fill_color
                       or p.fill_opacity != members[0].fill_opacity for p in members)):
            raise ValueError("Ordinary compound fill lost or changed a source contour")
        group_id = f"pdf-fill:{order}"
        if not row["even_odd"] and topology_cache is not None:
            topology_cache[group_id+":winding"] = (nonzero_windings or {}).get(order)
        if len(members) == 1:
            # Keep ordinary simple polygons on their existing owned-face route.
            # A lone bow-tie is still an even-odd paint operation: its signed
            # area can be zero although both lobes contain visible source ink.
            source = [tuple(float(v) for v in p) for p in members[0].points]
            normalized = source[:-1] if len(source) > 1 and source[0] == source[-1] else source
            try:
                split = split_self_crossing_contours(
                    [source], progress_callback=progress_callback, **(limits or {}))
            except CompoundTopologyBudgetExceeded:
                pass  # The builder retains this source path and reports the limit.
            else:
                if split == [normalized]:
                    continue
                if topology_cache is not None:
                    topology_cache[group_id] = split
        for primitive in members:
            replacements[primitive.id] = replace(
                primitive, clip_fill_group_id=group_id,
                clip_fill_even_odd=row["even_odd"], closed=True,
            )
    return [replacements.get(p.id, p) for p in primitives]


def split_self_crossing_contours(contours, *, progress_callback=None,
                                 point_budget=65536, work_budget=131072,
                                 candidate_budget=32768, split_budget=1024, _budget=None):
    """Decompose proper polygon crossings without changing even-odd edge parity.

    Blender fills separate intersecting loops correctly, but a crossing inside
    one spline can become its entire bounding face. Cutting a cycle at its
    exact intersection replaces it with two cycles having the same edges.
    Original PageData is retained separately; no original vertex is moved.
    """
    from .bl_round_strokes import _PaintBoundsIndex

    budget = _budget or _TopologyBudget(
        progress_callback, point_budget=point_budget, work_budget=work_budget,
        candidate_budget=candidate_budget, split_budget=split_budget)
    charge = budget.charge

    pending=[]
    for contour in contours:
        ring=[]
        for point in contour:
            charge("points")
            ring.append(tuple(float(v) for v in point))
        if any(len(p)!=2 or not all(math.isfinite(v) for v in p) for p in ring):
            raise ValueError("Ordinary compound fill contains non-finite source coordinates")
        if len(ring)>1 and ring[0]==ring[-1]:
            ring.pop()
        pending.append(ring)
    pending.reverse()  # Native fill keeps the producer's original contour order.
    result=[]
    while pending:
        ring=pending.pop()
        charge("work", len(ring))
        repeated={};split=False
        for index,point in enumerate(ring):
            previous=repeated.get(point)
            if previous is not None and index-previous>1:
                charge("splits")
                pending.extend((ring[index:]+ring[:previous],ring[previous:index]))
                split=True
                break
            repeated[point]=index
        if split:
            continue
        if len(ring)<3:
            continue  # A retraced, zero-area edge cycle contains no fill ink.
        edges=list(zip(ring,ring[1:]+ring[:1],strict=True))
        index=_PaintBoundsIndex([("edge",(min(a[0],b[0]),min(a[1],b[1]),
                                         max(a[0],b[0]),max(a[1],b[1]))) for a,b in edges])
        for first,(a,b) in enumerate(edges):
            box=(min(a[0],b[0]),min(a[1],b[1]),max(a[0],b[0]),max(a[1],b[1]))
            for second in index.candidates(box):
                charge("candidates")
                if second<=first or (second-first)%len(ring) in (1,len(ring)-1):
                    continue
                c,d=edges[second]
                aa,bb,cc,dd=[tuple(Fraction(v) for v in p) for p in (a,b,c,d)]
                r=tuple(v-u for u,v in zip(aa,bb,strict=True))
                s=tuple(v-u for u,v in zip(cc,dd,strict=True))
                delta=tuple(v-u for u,v in zip(aa,cc,strict=True))
                cross=lambda u,v:u[0]*v[1]-u[1]*v[0]
                denominator=cross(r,s)
                if not denominator:
                    continue
                t,u=cross(delta,s)/denominator,cross(delta,r)/denominator
                if not(0<t<1 and 0<u<1):
                    continue
                point=tuple(float(v+t*step) for v,step in zip(aa,r,strict=True))
                charge("splits")
                pending.extend(([point]+ring[second+1:]+ring[:first+1],
                                [point]+ring[first+1:second+1]))
                split=True
                break
            if split:
                break
        if not split:
            result.append(ring)
    # Coincident touching edges in Blender's curve filler can depend on spline
    # insertion order. Spatial ordering is neutral within this one even-odd
    # paint operation, and keeps both adjacent regions on legacy/current hosts.
    result.sort(key=lambda ring:(min(p[0] for p in ring),min(p[1] for p in ring),
                                 -abs(sum(a[0]*b[1]-b[0]*a[1]
                                          for a,b in zip(ring,ring[1:]+ring[:1],strict=True)))))
    return result


def bind_nonzero_windings(page_data, svg, *, progress_callback=None, **limits):
    """Bind original directed SVG paint to unique ordinary nonzero source fills.

    MuPDF can mislabel the orientation of detected four-line rectangles. Its
    SVG retains the directed source path. Reusing that path requires a unique
    complete geometry/style match, with no clipping, mask or unknown ancestry.
    A missing proof is an explicit per-fill refusal, never guessed winding.
    """
    from .bl_round_strokes import _PaintBoundsIndex
    from .image_paint_order import _matrix, _neutral, _ulp32
    from .pdf_paint_proof import _rgb
    from .triangle_paint_order import _straight_path, _transformed_point_proof

    if not isinstance(svg, str) or len(svg) > 8*1024*1024 or "<!" in svg or "<?xml-stylesheet" in svg:
        return {}
    parse_budget = _TopologyBudget(progress_callback, **limits)
    parse_budget.charge("work")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return {}
    nodes = list(root.iter())
    if (len(nodes) > 65536 or root.tag.rsplit("}", 1)[-1] != "svg"
            or any(n.tag.rsplit("}", 1)[-1] == "style" or
                   (n is not root and n.tag.rsplit("}", 1)[-1] == "svg") for n in nodes)):
        return {}
    try:
        if not _neutral(root, {"version", "width", "height", "viewBox", "opacity"}):
            return {}
    except ValueError:
        return {}
    rows = []

    def visit(node, matrices, safe):
        parse_budget.charge("work")
        if len(matrices) > 64:
            return  # Unknown/deep transform ancestry cannot prove this paint.
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in {"defs", "clipPath", "mask", "symbol"}:
            return
        if tag == "g":
            try:
                safe = safe and _neutral(node, {"id", "transform", "opacity"})
                matrices += (_matrix(node.get("transform")),)
            except ValueError:
                safe = False
        elif tag == "path" and safe:
            try:
                if (not _neutral(node, {"d", "transform", "fill", "fill-opacity", "fill-rule"})
                        or node.get("fill-rule", "nonzero") != "nonzero"
                        or float(node.get("fill-opacity", "1")) != 1):
                    return
                rgb = _rgb(node.get("fill", "#000000"))
                path = node.get("d", "")
                if rgb is None or len(path) > 1024*1024:
                    return
                parts = [part for part in re.split(r"(?=M)", path.strip()) if part]
                contours = []
                total = 0
                for part in parts:
                    parse_budget.charge("work", max(1, len(part)//8))
                    points = _straight_path(part)
                    if points is None or len(points) < 3:
                        return
                    total += len(points)
                    if total > 65536:
                        return
                    parse_budget.charge("points", len(points))
                    transformed = []
                    for point in points:
                        parse_budget.charge("work")
                        transformed.append(_transformed_point_proof(point, matrices+(_matrix(node.get("transform")),)))
                    contours.append(dict(points=[p for p, _ in transformed],
                                         point_errors=[e for _, e in transformed]))
                if len(contours) < 2:
                    return
                points = [p for contour in contours for p in contour["points"]]
                rows.append(dict(contours=contours, rgb=rgb,
                                 bounds=(min(p[0] for p in points), min(p[1] for p in points),
                                         max(p[0] for p in points), max(p[1] for p in points))))
            except CompoundTopologyBudgetExceeded:
                raise
            except (ValueError, OverflowError):
                return
        elif tag != "svg":
            return
        for child in node:
            visit(child, matrices, safe)

    try:
        visit(root, (), True)
    except CompoundTopologyBudgetExceeded:
        return {}
    index = _PaintBoundsIndex([("fill", row["bounds"]) for row in rows])
    assignments = []
    for source in getattr(page_data, "_source_drawings", ()) or ():
        if (source.get("type") != "f" or source.get("even_odd") is not False
                or source.get("fill") is None or source.get("fill_opacity") != 1
                or source.get("bcs_compound_clip_fill")):
            continue
        budget = _TopologyBudget(progress_callback, **limits)
        try:
            contours, curved = _contours(source.get("items") or ())
            if curved or len(contours) < 2:
                continue
            rgb = tuple(round(float(v)*255)/255 for v in source["fill"])

            def matches(candidate, expected, budget=budget):
                if len(candidate["points"]) != len(expected):
                    return False
                for direction in (1, -1):
                    for start in range(len(expected)):
                        budget.charge("work")
                        first, error = candidate["points"][0], candidate["point_errors"][0]
                        if any(abs(a-b) > e+4*_ulp32(b) for a, b, e in zip(first, expected[start], error, strict=True)):
                            continue
                        matched = True
                        for offset, (actual, errors) in enumerate(zip(candidate["points"], candidate["point_errors"], strict=True)):
                            budget.charge("work")
                            wanted = expected[(start+direction*offset) % len(expected)]
                            if any(abs(a-b) > e+4*_ulp32(b) for a, b, e in zip(actual, wanted, errors, strict=True)):
                                matched = False
                                break
                        if matched:
                            return True
                return False

            qualified = []
            for candidate_id in index.candidates(tuple(source["rect"])):
                budget.charge("candidates")
                candidate = rows[candidate_id]
                if candidate["rgb"] != rgb or len(candidate["contours"]) != len(contours):
                    continue
                selected = []
                for contour in contours:
                    matching = [i for i, c in enumerate(candidate["contours"]) if matches(c, contour)]
                    if len(matching) != 1:
                        break
                    selected.append(matching[0])
                if len(selected) != len(contours) or len(set(selected)) != len(selected):
                    continue
                directions = []
                for selected_id in selected:
                    points = candidate["contours"][selected_id]["points"]
                    area = sum(Fraction(a[0])*Fraction(b[1])-Fraction(b[0])*Fraction(a[1])
                               for a, b in zip(points, points[1:]+points[:1], strict=True))
                    if not area:
                        break
                    directions.append(1 if area > 0 else -1)
                if len(directions) == len(contours):
                    qualified.append((candidate_id, directions))
            if len(qualified) == 1:
                assignments.append((source["seqno"], *qualified[0]))
        except (ValueError, TypeError, KeyError, OverflowError):
            continue  # This one source fill has no bounded original direction proof.
    # A safe path must never certify a second equal-shaped source occurrence
    # whose original SVG ancestry was masked, clipped, blended or ambiguous.
    candidate_owners = {}
    source_owners = {}
    for seq, candidate, _directions in assignments:
        candidate_owners[candidate] = candidate_owners.get(candidate, 0)+1
        source_owners[seq] = source_owners.get(seq, 0)+1
    return {seq: directions for seq, candidate, directions in assignments
            if candidate_owners[candidate] == source_owners[seq] == 1}


def nonzero_fill_contours(contours, *, progress_callback=None, source_windings=None, **limits):
    """Keep exactly the zero/nonzero winding boundaries of simple nested loops.

    A same-winding nested contour changes winding from one to two and paints no
    boundary. An opposing contour can change it to zero, leaving a real hole.
    Intersecting/touching source contours require a more general arrangement;
    those are refused and retained as editable paths instead of guessed parity.
    """
    from .bl_round_strokes import _PaintBoundsIndex

    budget = _TopologyBudget(progress_callback, **limits)
    simple = split_self_crossing_contours(contours, _budget=budget)
    source = [[tuple(float(v) for v in p) for p in ring] for ring in contours]
    source = [ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring for ring in source]
    if sorted(map(tuple, simple)) != sorted(map(tuple, source)):
        raise ValueError("Nonzero self-crossing source fill requires a winding-aware arrangement")
    rings = [list(ring) for ring in simple]
    areas = [sum(Fraction(a[0])*Fraction(b[1])-Fraction(b[0])*Fraction(a[1])
                 for a, b in zip(ring, ring[1:]+ring[:1], strict=True)) for ring in rings]
    if any(not area for area in areas):
        raise ValueError("Nonzero source fill contains an unproven zero-area contour")
    if source_windings is not None:
        if len(source_windings) != len(source) or any(v not in (-1, 1) for v in source_windings):
            raise ValueError("Nonzero fill has unproven source contour winding")
        directions = dict(zip(map(tuple, source), source_windings, strict=True))
        orientation = [directions[tuple(ring)] for ring in rings]
    else:
        orientation = [1 if area > 0 else -1 for area in areas]
    bounds = [(min(p[0] for p in ring), min(p[1] for p in ring),
               max(p[0] for p in ring), max(p[1] for p in ring)) for ring in rings]
    edges = [(i, a, b) for i, ring in enumerate(rings)
             for a, b in zip(ring, ring[1:]+ring[:1], strict=True)]
    edge_bounds = [("edge", (min(a[0], b[0]), min(a[1], b[1]),
                              max(a[0], b[0]), max(a[1], b[1]))) for _, a, b in edges]
    index = _PaintBoundsIndex(edge_bounds)

    def cross(a, b, c):
        return ((Fraction(b[0])-Fraction(a[0]))*(Fraction(c[1])-Fraction(a[1]))
                - (Fraction(b[1])-Fraction(a[1]))*(Fraction(c[0])-Fraction(a[0])))

    for first, (ring_id, a, b) in enumerate(edges):
        budget.charge("work")
        for second in index.candidates(edge_bounds[first][1]):
            budget.charge("candidates")
            other_id, c, d = edges[second]
            if second <= first:
                continue
            if ring_id == other_id and (second-first) % len(rings[ring_id]) in (1, len(rings[ring_id])-1):
                continue
            signs = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
            if signs[0]*signs[1] <= 0 and signs[2]*signs[3] <= 0:
                raise ValueError("Nonzero crossing or touching source contours require a winding-aware arrangement")

    def contains(ring, point):
        winding = 0
        for a, b in zip(ring, ring[1:]+ring[:1], strict=True):
            budget.charge("work")
            if a[1] <= point[1] < b[1] and cross(a, b, point) > 0:
                winding += 1
            elif b[1] <= point[1] < a[1] and cross(a, b, point) < 0:
                winding -= 1
        return winding != 0

    loop_index = _PaintBoundsIndex([("loop", box) for box in bounds])
    parents = []
    for current, ring in enumerate(rings):
        possible = []
        for candidate in loop_index.candidates(bounds[current]):
            budget.charge("candidates")
            if candidate == current or abs(areas[candidate]) <= abs(areas[current]):
                continue
            inner, outer = bounds[current], bounds[candidate]
            if (outer[0] <= inner[0] and outer[1] <= inner[1]
                    and outer[2] >= inner[2] and outer[3] >= inner[3]
                    and contains(rings[candidate], ring[0])):
                possible.append(candidate)
        parents.append(min(possible, key=lambda i: abs(areas[i])) if possible else None)
    selected = []
    for current, ring in enumerate(rings):
        outside = 0
        parent = parents[current]
        while parent is not None:
            budget.charge("work")
            outside += orientation[parent]
            parent = parents[parent]
        if (outside == 0) != (outside+orientation[current] == 0):
            selected.append(ring)
    return selected
