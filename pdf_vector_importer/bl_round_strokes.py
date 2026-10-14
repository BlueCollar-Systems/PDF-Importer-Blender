"""Editable round-cap footprints for isolated, opaque Normal PDF strokes.

The original editable centerline stays present. A filled rational boundary
supplies the round ends missing from Blender's default bevelled open curve.
Unknown clip, blend or overlapping paint is reported, never guessed.
"""
from __future__ import annotations

import math
import json

from .bl_source_capsules import (
    _file_sha, _construct_native_capsule, _verify_native_capsule_surface, model_point,
)
from .image_paint_order import _close, _verify_native_stroke
from .capsule_nurbs import capsule_controls, cubic_capsule_controls, native_float_close
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


class RoundStrokeRollbackFailure(RuntimeError):
    """Owned footprints or original centerline state could not be restored."""


def _normal_round_marker_retained(footprint):
    # Older Blender ID properties store True as the builtin integer 1. That
    # representation can also survive when a saved model opens in a newer host.
    value = footprint.get("pdf_normal_round_stroke")
    return value is True or (type(value) is int and value == 1)


def apply_round_strokes(prepared, collection, config, *, source_path, progress_callback=None):
    if not prepared:
        return []
    import bpy
    from .import_session import ImportCancelledError, cancel_heartbeat_interval
    from .bl_geometry_builder import _line_bevel_depth
    from .fill_paint_order import same_native_object

    digest = _file_sha(source_path)
    owned, centers, entries = [], [], []
    footprint_ownership = {}
    records = []
    total = len(prepared)
    heartbeat = cancel_heartbeat_interval(total)
    seen_primitive_ids = set()

    def checkpoint(index, phase, base=0., span=.4, count=total):
        if progress_callback and (index % heartbeat == 0 or index == count):
            if progress_callback(base + span * index / max(1, count), phase) is False:
                raise ImportCancelledError("Round stroke construction cancelled")

    try:
        checkpoint(0, "Constructing round strokes")
        for index, spec in enumerate(prepared):
            checkpoint(index, "Constructing round strokes")
            if digest != spec["recipe"]["source_sha256"]:
                raise ValueError("Round stroke source PDF changed before native construction")
            matches = config.get("_image_order_stroke_objects", {}).get(spec["primitive_id"], ())
            if len(matches) != 1 or spec["primitive_id"] in seen_primitive_ids:
                raise ValueError("Round stroke has no unique native centerline")
            seen_primitive_ids.add(spec["primitive_id"])
            center = matches[0]
            _verify_native_stroke(center, dict(points_mm=spec["points_mm"], closed=False))
            if center.data.bevel_depth == 0 and center.data.fill_mode == "NONE":
                continue  # Explicit wire-only output remains the user's requested form.
            if len(center.data.materials) != 1:
                raise ValueError("Round stroke native material is absent")
            material = center.data.materials[0]
            source_width = spec["recipe"]["source_capsule_proof"]["capsule"]["width"] * spec["scale_mm"]
            if not native_float_close(center.data.bevel_depth, _line_bevel_depth(source_width)):
                raise ValueError("Round stroke native centerline width changed")
            previous = center.get("pdf_source_round_footprint")
            centers.append((center, center.data.bevel_depth, previous, "pdf_source_round_footprint" in center))
            center.data.bevel_depth = source_width / 2000
            if not native_float_close(center.data.bevel_depth, source_width / 2000):
                raise ValueError("Round stroke physical width could not be stored")
            footprint = _construct_native_capsule(spec, collection, material, owned)
            footprint_ownership[id(footprint)] = owned[-1]
            entries.append([spec, center, material, footprint, ""])
        checkpoint(total, "Evaluating round strokes")
        if entries:
            bpy.context.view_layer.update()
            graph = bpy.context.evaluated_depsgraph_get()
            fallback = []
            for index, entry in enumerate(entries):
                checkpoint(index, "Verifying round strokes", .4, .2, len(entries))
                spec, center, material, footprint, _ = entry
                _verify_round_footprint(footprint, spec, material, collection)
                _verify_native_capsule_surface(footprint, graph)
                capsule = spec["recipe"]["source_capsule_proof"]["capsule"]
                if not _footprint_area_matches(footprint, capsule, spec["scale_mm"], graph=graph):
                    # Preserve the pre-3.6 bounded cubic fallback, evaluating all
                    # replacements after one update rather than once per object.
                    fallback.append(entry)
            if fallback:
                for index, entry in enumerate(fallback):
                    checkpoint(index, "Constructing curved round strokes", .6, .1, len(fallback))
                    spec, _, material, footprint, _ = entry
                    _discard_owned_footprint(footprint_ownership.pop(id(footprint)))
                    entry[3] = _cubic_footprint(spec, collection, material, owned=owned)
                    entry[4] = "native_rational_fill_differs; circular quarters use bounded cubic curves"
                checkpoint(0, "Evaluating curved round strokes", .7, 0.)
                bpy.context.view_layer.update()
                graph = bpy.context.evaluated_depsgraph_get()
                # This update can reevaluate the surviving rational footprints
                # too. Their filled-surface and area proofs must remain current.
                for index, entry in enumerate(entries):
                    checkpoint(index, "Verifying curved round strokes", .7, .1, len(entries))
                    spec, _, material, footprint, approximation = entry
                    _verify_round_footprint(footprint, spec, material, collection, cubic=bool(approximation))
                    _verify_native_capsule_surface(footprint, graph)
                    if not _footprint_area_matches(footprint, spec["recipe"]["source_capsule_proof"]["capsule"], spec["scale_mm"], graph=graph):
                        raise ValueError("Round stroke native filled footprint could not be verified")
        for index, (spec, center, material, footprint, approximation) in enumerate(entries):
            checkpoint(index, "Checking original round strokes", .8, .1, len(entries))
            matches = config.get("_image_order_stroke_objects", {}).get(spec["primitive_id"], ())
            _verify_native_stroke(center, dict(points_mm=spec["points_mm"], closed=False))
            width = spec["recipe"]["source_capsule_proof"]["capsule"]["width"] * spec["scale_mm"]
            if (len(matches) != 1 or not same_native_object(matches[0], center)
                    or not native_float_close(center.data.bevel_depth, width / 2000)
                    or len(center.data.materials) != 1
                    or not same_native_object(center.data.materials[0], material)):
                raise ValueError("Round stroke original centerline changed during evaluation")
            _verify_round_footprint(footprint, spec, material, collection, cubic=bool(approximation))
        for index, (spec, center, _material, footprint, approximation) in enumerate(entries):
            checkpoint(index, "Linking round stroke footprints", .9, .09, len(entries))
            capsule = spec["recipe"]["source_capsule_proof"]["capsule"]
            footprint["pdf_normal_round_stroke"] = True
            world = footprint.matrix_world.copy()
            footprint.parent = center
            footprint.matrix_world = world
            center["pdf_source_round_footprint"] = footprint.name
            if (not _normal_round_marker_retained(footprint)
                    or not same_native_object(footprint.parent, center)
                    or center.get("pdf_source_round_footprint") != footprint.name
                    or any(not native_float_close(a,b) for row,wanted in zip(footprint.matrix_world,world,strict=True)
                           for a,b in zip(row,wanted,strict=True))):
                raise ValueError("Round footprint source placement or reference was not retained")
            records.append({"page":spec["recipe"]["page"],
                        "source_draw_order":spec["recipe"]["source_paint_order"],
                        "primitive_id":spec["primitive_id"],"status":"approximated" if approximation else "verified",
                        "representation":"editable_cubic_capsule" if approximation else "editable_rational_quadratic_capsule",
                        "approximation":approximation,
                        "max_radial_error_mm":0.000273*capsule["width"]/2*spec["scale_mm"] if approximation else 0,
                        "centerline_object":center.name,"footprint_object":footprint.name,
                        "source_width_pdf":spec["recipe"]["source_capsule_proof"]["capsule"]["width"],
                            "source_area_pdf2":capsule["area"]})
        checkpoint(total, "Checking completed round strokes", 1., 0.)
        # No mutable progress callback follows this seal. Stored definitions,
        # actual placed ownership and source bytes must remain the same after
        # the last callback and all parent/reference writes.
        for spec, center, material, footprint, approximation in entries:
            matches = config.get("_image_order_stroke_objects", {}).get(spec["primitive_id"], ())
            _verify_native_stroke(center, dict(points_mm=spec["points_mm"], closed=False))
            width = spec["recipe"]["source_capsule_proof"]["capsule"]["width"] * spec["scale_mm"]
            if (len(matches) != 1 or not same_native_object(matches[0], center)
                    or not native_float_close(center.data.bevel_depth, width / 2000)
                    or len(center.data.materials) != 1
                    or not same_native_object(center.data.materials[0], material)
                    or center.get("pdf_source_round_footprint") != footprint.name
                    or not _normal_round_marker_retained(footprint)):
                raise ValueError("Round stroke original ownership or reference changed during publication")
            _verify_round_footprint(footprint, spec, material, collection,
                                    cubic=bool(approximation), expected_parent=center)
        if _file_sha(source_path) != digest:
            raise ValueError("Round stroke source PDF changed during native construction")
    except BaseException as error:
        failures = []
        for row in reversed(owned):
            try:
                _discard_owned_footprint(row)
            except Exception as cleanup:
                failures.append(str(cleanup))
        for center, width, reference, present in reversed(centers):
            try:
                center.data.bevel_depth = width
                if not native_float_close(center.data.bevel_depth, width):
                    raise ValueError("Original stroke width was not restored")
                if present:
                    center["pdf_source_round_footprint"] = reference
                elif "pdf_source_round_footprint" in center:
                    del center["pdf_source_round_footprint"]
                if ("pdf_source_round_footprint" in center) != present or (present and center.get("pdf_source_round_footprint") != reference):
                    raise ValueError("Original stroke footprint reference was not restored")
            except Exception as cleanup:
                failures.append(str(cleanup))
        if failures:
            raise RoundStrokeRollbackFailure("Round stroke rollback could not be verified: " + "; ".join(failures)) from error
        raise
    return records


def _discard_owned_footprint(row):
    import bpy

    obj, data = row
    if obj is not None:
        name = obj.name
        bpy.data.objects.remove(obj, do_unlink=True)
        if bpy.data.objects.get(name) is not None:
            raise ValueError("Round footprint object removal was not retained")
        try:
            reader = getattr(obj, "as_pointer", None)
            alive = callable(reader) and reader() != 0
        except ReferenceError:
            alive = False
        if alive:
            raise ValueError("Round footprint native object still exists")
        row[0] = None
    if data is not None:
        name = data.name
        if data.users != 0:
            raise ValueError("Round footprint data remains in use")
        bpy.data.curves.remove(data)
        if bpy.data.curves.get(name) is not None:
            raise ValueError("Round footprint curve removal was not retained")
        try:
            reader = getattr(data, "as_pointer", None)
            alive = callable(reader) and reader() != 0
        except ReferenceError:
            alive = False
        if alive:
            raise ValueError("Round footprint native curve still exists")
        row[1] = None


def _verify_round_footprint(obj, spec, material, collection, *, cubic=False, expected_parent=None):
    from .fill_paint_order import same_native_object

    curve = obj.data
    if (not same_native_object(collection.objects.get(obj.name), obj)
            or not same_native_object(obj.parent, expected_parent) or obj.modifiers or obj.constraints
            or curve.dimensions != "2D" or curve.fill_mode != "BOTH"
            or curve.bevel_depth != 0 or curve.extrude != 0
            or len(curve.materials) != 1 or not same_native_object(curve.materials[0], material)
            or len(curve.splines) != 1):
        raise ValueError("Round footprint native ownership or material changed")
    spline = curve.splines[0]
    capsule = spec["recipe"]["source_capsule_proof"]["capsule"]
    scale, sign = spec["scale_mm"] / 1000, -1 if spec["flip_y"] else 1
    if cubic:
        origin, nodes = cubic_capsule_controls(capsule)
        expected = [[tuple((x*scale, y*scale*sign, 0.)) for x, y in node] for node in nodes]
        valid = (spline.type == "BEZIER" and spline.use_cyclic_u
                 and len(spline.bezier_points) == len(expected)
                 and all(point.handle_left_type == point.handle_right_type == "FREE"
                         and all(native_float_close(a,b) for actual,wanted in
                                 ((point.co,co),(point.handle_left,left),(point.handle_right,right))
                                 for a,b in zip(actual,wanted,strict=True))
                         for point,(co,left,right) in zip(spline.bezier_points,expected,strict=True)))
    else:
        origin, controls = capsule_controls(capsule)
        expected = [(x*scale, y*scale*sign, 0., weight) for x,y,weight in controls]
        valid = (spline.type == "NURBS" and spline.order_u == 3 and not spline.use_endpoint_u
                 and spline.use_bezier_u and spline.use_cyclic_u and len(spline.points) == len(expected)
                 and all(native_float_close(a,b) for point,co in zip(spline.points,expected,strict=True)
                         for a,b in zip(point.co,co,strict=True)))
    x, y = model_point(origin, spec["page_height"], spec["scale_mm"], spec["flip_y"])
    matrix = ((1.,0.,0.,x/1000),(0.,1.,0.,y/1000),(0.,0.,1.,0.),(0.,0.,0.,1.))
    local = (x/1000, y/1000, 0.)
    if expected_parent is not None:
        from mathutils import Vector

        # These privately constructed objects start with identity parent
        # inverse. Their location is now parent-local, while source controls
        # and the full actual world matrix must remain exactly source-bound.
        identity = ((1.,0.,0.,0.),(0.,1.,0.,0.),(0.,0.,1.,0.),(0.,0.,0.,1.))
        if any(not native_float_close(a,b) for row,wanted in zip(obj.matrix_parent_inverse,identity,strict=True)
               for a,b in zip(row,wanted,strict=True)):
            raise ValueError("Round footprint native parent inverse changed")
        inverse = expected_parent.matrix_world.inverted()
        local_vector = inverse @ Vector(local)
        local = (local_vector.x, local_vector.y, local_vector.z)
        # World matrices may still be cached after the last mutable callback.
        # The actual local basis must also retain the complete source pose,
        # including rotations that leave the source axis and origin unchanged.
        basis = tuple(tuple(sum(inverse[i][k]*matrix[k][j] for k in range(4))
                            for j in range(4)) for i in range(4))
        if any(not native_float_close(a,b) for row,wanted in zip(obj.matrix_basis,basis,strict=True)
               for a,b in zip(row,wanted,strict=True)):
            raise ValueError("Round footprint native local transform changed")
    if (not valid or not all(native_float_close(a,b) for a,b in zip(obj.location,local,strict=True))
            or any(not native_float_close(a,b) for row,wanted in zip(obj.matrix_world,matrix,strict=True)
                   for a,b in zip(row,wanted,strict=True))
            or json.loads(obj["pdf_source_capsule"]) != json.loads(json.dumps(spec))):
        raise ValueError("Round footprint original source controls or origin changed")


def _footprint_area_matches(obj, capsule, scale, *, graph=None):
    import bpy

    if graph is None:
        bpy.context.view_layer.update()
        graph = bpy.context.evaluated_depsgraph_get()
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


def _cubic_footprint(spec, collection, material, *, owned=None):
    import bpy

    capsule=spec["recipe"]["source_capsule_proof"]["capsule"]
    origin,nodes=cubic_capsule_controls(capsule)
    scale=spec["scale_mm"]/1000
    sign=-1 if spec["flip_y"] else 1
    expected=[[tuple((x*scale,y*scale*sign,0.)) for x,y in node] for node in nodes]
    curve=bpy.data.curves.new("PDF_Source_Cubic_Capsule","CURVE")
    ownership = [None, curve]
    if owned is not None:
        owned.append(ownership)
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
        ownership[0] = obj
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
        if owned is not None:
            raise  # The stage verifies cleanup of every created data/object pair.
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
