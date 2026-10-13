"""Source-bound round ends retain width/placement, not guessed cap defaults."""
import hashlib
import json
import math
import sys

import pymupdf
import pytest

from pdf_vector_importer import bl_round_strokes as module
from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page
from pdf_vector_importer.stroke_footprint import round_stroke


def fixture(tmp_path, *, start=(10,20), end=(85,20), cap=1, extra=b"", before=b"", width=6):
    path = tmp_path / "round.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=100,height=100)
        xref = doc.get_new_xref()
        doc.update_object(xref,"<<>>")
        stream = (before + f".85 .12 .08 RG {cap} J {width} w {start[0]} {start[1]} m {end[0]} {end[1]} l S\n".encode() + extra)
        doc.update_stream(xref,stream)
        page.set_contents(xref)
        doc.save(path)
    return path


def prepare(path, **kwargs):
    with pymupdf.open(path) as doc:
        data = extract_page(doc[0],1,detect_arcs=False,scale=kwargs.get("user_scale",1),flip_y=kwargs.get("flip_y",True))
        return module.prepare_round_strokes(doc[0],data,source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),page_number=1,**kwargs)


@pytest.mark.parametrize("end", [(85,20),(10,85),(75,80),(10,20),(10.01,20)])
@pytest.mark.parametrize("flip_y", [False,True])
def test_horizontal_vertical_slanted_dot_and_short_caps_keep_source_endpoints(tmp_path,end,flip_y):
    path = fixture(tmp_path,end=end)
    specs, unresolved = prepare(path,user_scale=2.5,flip_y=flip_y)
    assert len(specs)==1 and not unresolved
    spec=specs[0]
    factor=25.4/72*2.5
    # Fixture content uses PDF bottom-up coordinates; MuPDF reports top-down.
    wanted = [(10*factor,(20 if flip_y else 80)*factor),
              (end[0]*factor,(end[1] if flip_y else 100-end[1])*factor)]
    for actual, expected in zip(spec["points_mm"], wanted, strict=True):
        assert actual == pytest.approx(expected, rel=0, abs=2.5e-6)  # MuPDF float32 input only.
    capsule=spec["recipe"]["source_capsule_proof"]["capsule"]
    assert capsule["width"]==6
    assert capsule["area"]==pytest.approx(6*math.hypot(end[0]-10,end[1]-20)+9*math.pi)


@pytest.mark.parametrize("cap", [0,2])
def test_butt_and_projecting_square_are_not_claimed_as_round(tmp_path,cap):
    assert prepare(fixture(tmp_path,cap=cap)) == ([],[])


@pytest.mark.parametrize("extra", [b"0 g 50 18 5 5 re f",b"0 G 1 w 50 10 m 50 30 l S"])
def test_overlapping_source_paint_preserves_centerline_and_reports_unqualified(tmp_path,extra):
    specs, unresolved = prepare(fixture(tmp_path,extra=extra))
    assert not specs and unresolved[0]["reason"]=="round_cap_footprint_overlaps_other_source_paint"


def test_partially_clipped_round_ends_are_not_extended_past_the_mask(tmp_path):
    specs, unresolved=prepare(fixture(tmp_path,before=b"q 12 10 70 20 re W n\n",extra=b"Q"))
    assert not specs and unresolved[0]["reason"]=="round_cap_clip_or_blend_not_proven"


def test_knockout_group_is_not_plain_normal_paint(monkeypatch,tmp_path):
    path=fixture(tmp_path)
    monkeypatch.setattr(module,"unclipped_capsules",lambda *a,**k:{0:{"source_knockout":True,"source_blend_modes":["Normal"]}})
    specs, unresolved=prepare(path)
    assert not specs and unresolved[0]["reason"]=="round_cap_clip_or_blend_not_proven"


@pytest.mark.parametrize("change", ["missing","duplicate","moved","width"])
def test_changed_source_centerline_cannot_claim_rounded_footprint(tmp_path,change):
    path=fixture(tmp_path)
    with pymupdf.open(path) as doc:
        data=extract_page(doc[0],1,detect_arcs=False)
        if change=="missing":data.primitives.clear()
        elif change=="duplicate":data.primitives*=2
        elif change=="moved":data.primitives[0].points[0]=(0,0)
        else:data.primitives[0].line_width=1
        with pytest.raises(ValueError,match="unique unchanged"):
            module.prepare_round_strokes(doc[0],data,source_sha256="a"*64,page_number=1)


def test_long_capsule_has_no_short_stroke_threshold():
    row={"type":"s","items":[("l",(0,0),(10000,0))],"width":.1,"lineCap":(1,1,1),
         "color":(1,0,0),"fill":None,"closePath":False,"stroke_opacity":1,"dashes":"[] 0"}
    assert round_stroke(row)["length"]==10000
    row["lineCap"]=(1,0,1)
    assert round_stroke(row) is None


def test_dense_page_paint_index_keeps_complete_overlap_and_tangency_semantics():
    log=[("stroke-path",(0,float(i*10),100,float(i*10+1))) for i in range(2500)]
    index=module._PaintBoundsIndex(log)
    for y in (0,11,120,9990,24991):
        bounds=(20,y,80,y+2)
        expected={i for i,(_kind,box) in enumerate(log) if index._overlaps(box,bounds)}
        assert set(index.candidates(bounds))==expected
    assert index.candidates((100,1,110,1))==[0]  # Touching paint stays a blocker.


def test_unknown_paint_bounds_never_prove_disjoint_source():
    index=module._PaintBoundsIndex([("image",(float('nan'),0,1,1)),("ignore-text",(0,0,1,1))])
    assert index.candidates((20,20,30,30))==[0]


def sheet(tmp_path, count, cols=8):
    """Separate q/cm-wrapped round-cap segments, each with its own paint state."""
    path=tmp_path/"sheet.pdf"
    with pymupdf.open() as doc:
        page=doc.new_page(width=40+cols*45,height=40+(count//cols+1)*45)
        xref=doc.get_new_xref()
        doc.update_object(xref,"<<>>")
        doc.update_stream(xref,"\n".join(
            f"q 1 0 0 1 {40+i%cols*45} {40+i//cols*45} cm {i%3*.3:.1f} .2 .5 RG {1+i%3} w 1 J 1 j "
            f"0 0 m {12+i%5} {i%4*3} l S Q" for i in range(count)).encode())
        page.set_contents(xref)
        doc.save(path)
    return path


class _Native(dict):
    """Blender ID stand-in: custom properties plus plain attributes."""

    def __init__(self, **attrs):
        super().__init__()
        self.__dict__.update(attrs)


class _Points(list):
    def add(self, count):
        self.extend(_Native(co=None) for _ in range(count))


class _Scene:
    """Counts whole-graph evaluations; an unevaluated link cannot be measured."""

    def __init__(self, native_area_factor):
        self.updates=self.rebuilds=0
        self.dirty=False
        self.linked,self.evaluated,self.removed_curves=[],set(),[]
        self.native_area_factor=native_area_factor
        self.bpy=_Native(
            data=_Native(curves=_Native(new=self.curve,remove=self.removed_curves.append),
                         objects=_Native(new=self.object,remove=self.remove)),
            context=_Native(view_layer=_Native(update=self.update),evaluated_depsgraph_get=self.graph))
        self.collection=_Native(objects=_Native(link=self.link))

    def curve(self, name, kind):
        new_spline=lambda kind:_Native(points=_Points([_Native(co=None)]),bezier_points=_Points([_Native(co=None)]))
        return _Native(name=name,users=0,materials=[],splines=_Native(new=new_spline))

    def object(self, name, curve):
        curve.users+=1
        obj=_Native(name=name,data=curve,parent=None,matrix_world=_Native(copy=lambda:"world"))
        obj.evaluated_get=lambda graph:self.evaluate(obj,graph)
        return obj

    def link(self, obj):
        self.linked.append(obj)
        self.dirty=True

    def remove(self, obj, do_unlink):
        assert do_unlink
        self.linked.remove(obj)
        obj.data.users-=1
        self.dirty=True

    def update(self):
        self.updates+=1
        self.graph()

    def graph(self):
        if self.dirty:
            self.rebuilds+=1
            self.dirty=False
            self.evaluated=set(map(id,self.linked))
        return self

    def evaluate(self, obj, graph):
        assert graph is self and not self.dirty and id(obj) in self.evaluated
        spec=json.loads(obj["pdf_source_capsule"])
        area=spec["recipe"]["source_capsule_proof"]["capsule"]["area"]*spec["scale_mm"]**2/1e6
        if obj.name.startswith("PDF_Source_Capsule_"):
            area*=self.native_area_factor
        mesh=_Native(polygons=[_Native(area=area)],loop_triangles=[_Native(area=area)],calc_loop_triangles=lambda:None)
        return _Native(to_mesh=lambda:mesh,to_mesh_clear=lambda:None)


def dense_sheet_run(monkeypatch,tmp_path,native_area_factor,material):
    path=sheet(tmp_path,40)
    specs,unresolved=prepare(path)
    assert len(specs)==40 and not unresolved
    scene=_Scene(native_area_factor)
    monkeypatch.setitem(sys.modules,"bpy",scene.bpy)
    monkeypatch.setitem(sys.modules,"pdf_vector_importer.bl_geometry_builder",
                        _Native(_line_bevel_depth=lambda width:width/2000))
    monkeypatch.setattr(module,"_verify_native_stroke",lambda *a:None)
    centers={spec["primitive_id"]:_Native(name=f"line-{spec['primitive_id']}",data=_Native(
        bevel_depth=spec["recipe"]["source_capsule_proof"]["capsule"]["width"]*spec["scale_mm"]/2000,
        fill_mode="FULL",materials=[material])) for spec in specs}
    config={"_image_order_stroke_objects":{key:[center] for key,center in centers.items()}}
    return scene,specs,centers,lambda:module.apply_round_strokes(specs,scene.collection,config,source_path=path)


@pytest.mark.parametrize("native_area_factor,updates",[(1,1),(.9,2)])
def test_dense_sheet_footprints_share_one_graph_update(monkeypatch,tmp_path,native_area_factor,updates):
    # v1.0.109 updated the whole scene graph twice per round end; a sheet
    # with thousands of them did not finish importing.
    material=object()
    scene,specs,centers,run=dense_sheet_run(monkeypatch,tmp_path,native_area_factor,material)
    records=run()
    assert scene.updates==scene.rebuilds==updates
    prefix="PDF_Source_Capsule_" if native_area_factor==1 else "PDF_Source_Cubic_Capsule_"
    assert [r["footprint_object"] for r in records]==[prefix+str(s["recipe"]["source_paint_order"]) for s in specs]
    assert [o.name for o in scene.linked]==[r["footprint_object"] for r in records]
    assert {r["status"] for r in records}=={"verified" if native_area_factor==1 else "approximated"}
    for spec,record,footprint in zip(specs,records,scene.linked,strict=True):
        center=centers[spec["primitive_id"]]
        assert footprint.parent is center and footprint.matrix_world=="world"
        assert record["centerline_object"]==center.name and center["pdf_source_round_footprint"]==footprint.name
        assert footprint.data.materials==[material] and footprint["pdf_normal_round_stroke"]
    assert len(scene.removed_curves)==(0 if native_area_factor==1 else 40)


def test_dense_sheet_unfilled_footprint_aborts_before_any_is_attached(monkeypatch,tmp_path):
    scene,specs,centers,run=dense_sheet_run(monkeypatch,tmp_path,0,object())
    with pytest.raises(ValueError,match="no filled surface"):
        run()
    first="PDF_Source_Capsule_"+str(specs[0]["recipe"]["source_paint_order"])
    assert scene.updates==1 and len(scene.removed_curves)==1 and first not in {o.name for o in scene.linked}
    assert not any(o.parent for o in scene.linked)
    assert not any("pdf_source_round_footprint" in center for center in centers.values())
