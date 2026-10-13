"""Editable round-cap footprints for isolated, opaque Normal PDF strokes.

The original editable centerline stays present. A filled rational boundary
supplies the round ends missing from Blender's default bevelled open curve.
Unknown clip, blend or overlapping paint is reported, never guessed.
"""
from __future__ import annotations

import math
import json

from .bl_source_capsules import _file_sha, _native_capsule, _require_filled_surface, model_point
from .image_paint_order import _close, _verify_native_stroke
from .capsule_nurbs import cubic_capsule_controls, native_float_close
from .stroke_footprint import bind_similarity_strokes, round_stroke, unclipped_capsules


class _PaintBoundsIndex:
    """Balanced two-dimensional boxes avoid a page scan for every round stroke."""

    def __init__(self, log):
        self.unknown=[]
        known=[]
        for order,(kind,box) in enumerate(log):
            if kind=="ignore-text":
                continue
            if (len(box)!=4 or not all(math.isfinite(v) for v in box)
                    or box[0]>box[2] or box[1]>box[3]):
                self.unknown.append(order)
            else:
                known.append((order,tuple(box)))
        self.tree=self._build(known)

    def _build(self, rows):
        if not rows:
            return None
        bounds=(min(b[0] for _,b in rows),min(b[1] for _,b in rows),
                max(b[2] for _,b in rows),max(b[3] for _,b in rows))
        if len(rows)<=8:
            return bounds,rows,None,None
        spans=[max(b[axis]+b[axis+2] for _,b in rows)-min(b[axis]+b[axis+2] for _,b in rows)
               for axis in (0,1)]
        axis=int(spans[1]>spans[0])
        rows.sort(key=lambda item:item[1][axis]+item[1][axis+2])
        middle=len(rows)//2
        return bounds,None,self._build(rows[:middle]),self._build(rows[middle:])

    @staticmethod
    def _overlaps(a,b):
        return not(a[2]<b[0] or b[2]<a[0] or a[3]<b[1] or b[3]<a[1])

    def candidates(self, bounds):
        result=list(self.unknown)
        pending=[self.tree]
        while pending:
            node=pending.pop()
            if node is None or not self._overlaps(node[0],bounds):
                continue
            if node[1] is not None:
                result.extend(order for order,box in node[1] if self._overlaps(box,bounds))
            else:
                pending.extend(node[2:])
        return result


def prepare_round_strokes(page, page_data, *, source_sha256, page_number,
                          user_scale=1.0, flip_y=True):
    if page.rotation or tuple(page.rect)[:2] != (0., 0.):
        return [], []
    rows = page.get_drawings(extended=True)
    candidates = {row["seqno"] for row in rows if type(row.get("seqno")) is int
                  and round_stroke(row) is not None}
    proofs = unclipped_capsules(rows, tuple(page.rect), allow_long=True)
    multiply = {seq for seq, proof in proofs.items()
                if "Multiply" in proof.get("source_blend_modes", ())}
    candidates -= multiply  # The existing Multiply compositor owns this separate route.
    proofs = {seq: proof for seq, proof in proofs.items()
              if not proof.get("source_knockout") and all(
                  mode == "Normal" for mode in proof.get("source_blend_modes", ()))}
    unresolved = [{"page":page_number,"source_draw_order":seq,"status":"unqualified",
                   "reason":"round_cap_clip_or_blend_not_proven"} for seq in sorted(candidates-set(proofs))]
    if not proofs:
        return [], unresolved
    from .pdf_paint_proof import optional_page_svg

    svg = optional_page_svg(page, text_as_path=True)
    if svg is None:
        return [], unresolved + [{"page":page_number,"source_draw_order":seq,"status":"unqualified",
                                  "reason":"source_svg_round_stroke_binding_not_proven"} for seq in proofs]
    unbound = set(proofs)
    proofs = bind_similarity_strokes(proofs, rows, svg, allow_long=True)
    unresolved.extend({"page":page_number,"source_draw_order":seq,"status":"unqualified",
                       "reason":"source_svg_round_stroke_binding_not_proven"} for seq in sorted(unbound-set(proofs)))
    scale = 25.4 / 72 * float(user_scale)
    if not math.isfinite(scale) or scale <= 0 or page.number + 1 != page_number:
        raise ValueError("Round stroke source page or scale changed")
    log = page.get_bboxlog()
    paint_index=_PaintBoundsIndex(log)
    by_order={}
    for primitive in page_data.primitives:
        by_order.setdefault(primitive.source_draw_order,[]).append(primitive)
    prepared = []
    for seq, proof in proofs.items():
        capsule = proof["capsule"]
        start, end, radius = capsule["start"], capsule["end"], capsule["width"] / 2
        bounds = (min(start[0], end[0])-radius, min(start[1], end[1])-radius,
                  max(start[0], end[0])+radius, max(start[1], end[1])+radius)
        record = {"page":page_number,"source_draw_order":seq,"status":"unqualified"}
        if seq >= len(log) or log[seq][0] != "stroke-path":
            unresolved.append(dict(record, reason="source_stroke_occurrence_not_proven"))
            continue
        if any(order!=seq for order in paint_index.candidates(bounds)):
            unresolved.append(dict(record, reason="round_cap_footprint_overlaps_other_source_paint"))
            continue
        matches = by_order.get(seq,())
        points = [model_point(p, page.rect.height, scale, flip_y) for p in (start, end)]
        if (len(matches) != 1 or matches[0].type not in ("line", "polyline")
                or len(matches[0].points) != 2 or matches[0].fill_color is not None
                or matches[0].stroke_opacity != 1
                or not _close((matches[0].line_width,), (capsule["width"]*scale,))
                or not all(_close(a,b) for a,b in zip(matches[0].points,points,strict=True))):
            raise ValueError("Round stroke has no unique unchanged source centerline")
        prepared.append({"primitive_id":matches[0].id,"points_mm":points,"scale_mm":scale,
                         "flip_y":bool(flip_y),"page_height":page.rect.height,
                         "recipe":{"source_paint_order":seq,"source_sha256":source_sha256,
                                   "page":page_number,"source_capsule_proof":proof}})
    return prepared, unresolved


def apply_round_strokes(prepared, collection, config, *, source_path):
    if not prepared:
        return []
    import bpy

    digest = _file_sha(source_path)
    built = []
    for spec in prepared:
        if digest != spec["recipe"]["source_sha256"]:
            raise ValueError("Round stroke source PDF changed before native construction")
        owned = config.get("_image_order_stroke_objects", {}).get(spec["primitive_id"], ())
        if len(owned) != 1:
            raise ValueError("Round stroke has no unique native centerline")
        center = owned[0]
        _verify_native_stroke(center, dict(points_mm=spec["points_mm"], closed=False))
        if center.data.bevel_depth == 0 and center.data.fill_mode == "NONE":
            continue  # Explicit wire-only output remains the user's requested form.
        if len(center.data.materials) != 1:
            raise ValueError("Round stroke native material is absent")
        from .bl_geometry_builder import _line_bevel_depth

        source_width=spec["recipe"]["source_capsule_proof"]["capsule"]["width"]*spec["scale_mm"]
        if not native_float_close(center.data.bevel_depth,_line_bevel_depth(source_width)):
            raise ValueError("Round stroke native centerline width changed")
        # A display hairline floor must not widen this positively proven
        # physical stroke beyond its exact filled boundary.
        center.data.bevel_depth=source_width/2000
        if not native_float_close(center.data.bevel_depth,source_width/2000):
            raise ValueError("Round stroke physical width could not be stored")
        footprint = _native_capsule(spec, collection, center.data.materials[0], evaluate=False)
        built.append((spec, center, footprint))
    if not built:
        return []
    # Verify every footprint after one graph update. Each update after a new
    # link rebuilds the whole scene graph, so updating per footprint made
    # sheets with thousands of round ends take hours.
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    approximated = []
    for index, (spec, _center, footprint) in enumerate(built):
        try:
            _require_filled_surface(footprint, graph)
        except Exception:
            _discard_footprint(footprint)
            raise
        capsule = spec["recipe"]["source_capsule_proof"]["capsule"]
        if not _footprint_area_matches(footprint, capsule, spec["scale_mm"], graph):
            approximated.append(index)
    if approximated:
        # The pre-3.6 cyclic knot implementation can retain all controls and
        # flags yet evaluate a different filled shape. Never call that exact.
        for index in approximated:
            spec, center, footprint = built[index]
            _discard_footprint(footprint)
            built[index] = (spec, center, _cubic_footprint(spec, collection, center.data.materials[0]))
        bpy.context.view_layer.update()
        graph = bpy.context.evaluated_depsgraph_get()
        for index in approximated:
            spec, _center, footprint = built[index]
            capsule = spec["recipe"]["source_capsule_proof"]["capsule"]
            if not _footprint_area_matches(footprint, capsule, spec["scale_mm"], graph):
                _discard_footprint(footprint)
                raise ValueError("Round stroke native filled footprint could not be verified")
    approximated = set(approximated)
    records = []
    for index, (spec, center, footprint) in enumerate(built):
        capsule = spec["recipe"]["source_capsule_proof"]["capsule"]
        approximation = ("native_rational_fill_differs; circular quarters use bounded cubic curves"
                         if index in approximated else "")
        footprint["pdf_normal_round_stroke"] = True
        world=footprint.matrix_world.copy()
        footprint.parent=center
        footprint.matrix_world=world
        center["pdf_source_round_footprint"]=footprint.name
        records.append({"page":spec["recipe"]["page"],
                        "source_draw_order":spec["recipe"]["source_paint_order"],
                        "primitive_id":spec["primitive_id"],"status":"approximated" if approximation else "verified",
                        "representation":"editable_cubic_capsule" if approximation else "editable_rational_quadratic_capsule",
                        "approximation":approximation,
                        "max_radial_error_mm":0.000273*capsule["width"]/2*spec["scale_mm"] if approximation else 0,
                        "centerline_object":center.name,"footprint_object":footprint.name,
                        "source_width_pdf":spec["recipe"]["source_capsule_proof"]["capsule"]["width"],
                        "source_area_pdf2":spec["recipe"]["source_capsule_proof"]["capsule"]["area"]})
    return records


def _discard_footprint(obj):
    import bpy

    data=obj.data
    bpy.data.objects.remove(obj,do_unlink=True)
    if data.users==0:
        bpy.data.curves.remove(data)


def _footprint_area_matches(obj, capsule, scale, graph):
    evaluated=obj.evaluated_get(graph)
    mesh=evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        measured=sum(triangle.area for triangle in mesh.loop_triangles)*1e6
        expected=capsule["area"]*scale**2
        circle_area=math.pi*(capsule["width"]/2*scale)**2
        # Finite host tessellation plus bounded native float32 position storage.
        coordinate=max(abs(v) for point in (capsule["start"],capsule["end"]) for v in point)*scale
        storage_budget=max(coordinate,1)*2**-21*(2*capsule["length"]+math.pi*capsule["width"])*scale
        return math.isfinite(measured) and measured>0 and abs(measured-expected)<=circle_area*.002+storage_budget
    finally:
        evaluated.to_mesh_clear()


def _cubic_footprint(spec, collection, material):
    import bpy

    capsule=spec["recipe"]["source_capsule_proof"]["capsule"]
    origin,nodes=cubic_capsule_controls(capsule)
    scale=spec["scale_mm"]/1000
    sign=-1 if spec["flip_y"] else 1
    expected=[[tuple((x*scale,y*scale*sign,0.)) for x,y in node] for node in nodes]
    curve=bpy.data.curves.new("PDF_Source_Cubic_Capsule","CURVE")
    obj=None
    try:
        curve.dimensions="2D";curve.fill_mode="BOTH";curve.bevel_depth=0;curve.extrude=0
        curve.resolution_u=32;curve.render_resolution_u=32
        spline=curve.splines.new("BEZIER");spline.bezier_points.add(len(expected)-1)
        spline.use_cyclic_u=True
        for point,(co,left,right) in zip(spline.bezier_points,expected,strict=True):
            point.handle_left_type=point.handle_right_type="FREE"
            point.co=co;point.handle_left=left;point.handle_right=right
            if any(not native_float_close(a,b) for actual,wanted in
                   ((point.co,co),(point.handle_left,left),(point.handle_right,right))
                   for a,b in zip(actual,wanted,strict=True)):
                raise ValueError("Cubic capsule native source controls changed")
        curve.materials.append(material)
        obj=bpy.data.objects.new(f"PDF_Source_Cubic_Capsule_{spec['recipe']['source_paint_order']}",curve)
        x,y=model_point(origin,spec["page_height"],spec["scale_mm"],spec["flip_y"])
        position=(x/1000,y/1000,0)
        obj.location=position
        if not all(native_float_close(a,b) for a,b in zip(obj.location,position,strict=True)):
            raise ValueError("Cubic capsule native source origin changed")
        collection.objects.link(obj)
        obj["pdf_geometry_representation"]="bounded_cubic_circular_quarters"
        obj["pdf_source_capsule"]=json.dumps(spec,sort_keys=True)
        return obj
    except Exception:
        if obj is not None:
            bpy.data.objects.remove(obj,do_unlink=True)
        if curve.users==0:
            bpy.data.curves.remove(curve)
        raise


def round_stroke_warning(records):
    unqualified = sum(record.get("status") == "unqualified" for record in records)
    approximated = sum(record.get("status") == "approximated" for record in records)
    messages=[]
    if unqualified:
        messages.append(f"{unqualified} round-ended line(s) need review; their original editable lines are retained.")
    if approximated:
        messages.append(f"{approximated} round-ended line(s) use editable curved approximations on this Blender version.")
    return " ".join(messages+["See source_round_stroke_footprints in the import report."]) if messages else ""
