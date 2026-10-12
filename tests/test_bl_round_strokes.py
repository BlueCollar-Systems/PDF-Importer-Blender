"""Source-bound round ends retain width/placement, not guessed cap defaults."""
import hashlib
import math

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


def _round_spec(index):
    return {
        "primitive_id": index,
        "recipe": {"page": 1, "source_sha256": "a" * 64, "source_paint_order": index},
    }


def test_round_footprint_budget_keeps_a_small_page_and_defers_a_dense_sheet():
    small = [_round_spec(i) for i in range(module.MAX_NATIVE_ROUND_FOOTPRINTS)]
    assert module.defer_native_round_footprints(small) is None
    # A one-page foundation hatch qualified thousands of isolated round lines.
    dense = [_round_spec(i) for i in range(4500)]
    record = module.defer_native_round_footprints(dense)
    assert record == {
        "page": 1,
        "status": "deferred",
        "reason": "round_stroke_native_footprint_budget",
        "prepared_count": 4500,
        "budget": module.MAX_NATIVE_ROUND_FOOTPRINTS,
    }


def test_dense_round_lines_do_not_build_native_footprints(monkeypatch):
    built = []
    monkeypatch.setattr(module, "_native_capsule", lambda *args, **kwargs: built.append(args))
    seen = []
    records = module.apply_round_strokes(
        [_round_spec(i) for i in range(4500)],
        None,
        {},
        source_path="unused",
        progress_callback=seen.append,
    )
    assert built == []
    assert seen == [1.0]
    assert records[0]["status"] == "deferred"
    assert "4500 round-ended lines kept their editable centerlines" in module.round_stroke_warning(records)


def test_one_round_line_stays_on_the_native_path_and_can_be_cancelled(monkeypatch, tmp_path):
    from pdf_vector_importer.import_session import ImportCancelledError

    monkeypatch.setattr(module, "_file_sha", lambda _path: "a" * 64)
    monkeypatch.setattr(
        module, "_native_capsule",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("native build after cancel")),
    )
    with pytest.raises(ImportCancelledError, match="round line ends"):
        module.apply_round_strokes(
            [_round_spec(0)],
            None,
            {},
            source_path=str(tmp_path / "unused.pdf"),
            progress_callback=lambda _frac: False,
        )
