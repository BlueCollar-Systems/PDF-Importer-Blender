"""Exact retained/cut LINE fills must remain filled paint, never open strokes."""
from copy import deepcopy
from fractions import Fraction
import math

import pytest

from pdf_vector_importer.pdfcadcore import drawing_clips as clips


def path(points, closed=False):
    result = [("l", a, b) for a, b in zip(points, points[1:], strict=False)]
    if closed:
        result.append(("l", points[-1], points[0]))
    return result


def source_rows(points=((1, 1), (3, 1), (1, 3)), *, close=None, even_odd=False):
    outer = {"type": "clip", "level": 0, "scissor": (0, 0, 20, 20),
             "items": [("re", (0, 0, 20, 20), 1)], "even_odd": False}
    inner = {"type": "clip", "level": 1, "scissor": (0, 0, 10, 10),
             "items": path([(0, 0), (10, 0), (10, 10), (0, 10)], True),
             "even_odd": True, "closePath": False}
    xs, ys = zip(*points, strict=True)
    fill = {"type": "f", "level": 2, "seqno": 19,
            "rect": (min(xs), min(ys), max(xs), max(ys)), "items": path(points),
            "fill": (.2, .4, .6), "fill_opacity": .75,
            "even_odd": even_odd, "closePath": close, "color": None}
    return [outer, inner, fill]


@pytest.mark.parametrize("close", [None, False, True])
@pytest.mark.parametrize("even_odd", [False, True])
def test_contained_original_commands_closure_rule_and_style_are_not_changed(close, even_odd):
    rows = source_rows(close=close, even_odd=even_odd)
    original = deepcopy(rows)
    result = clips.resolve_covered_clip_fills(rows)
    assert len(result) == 1
    row = result[0]
    for key, value in original[-1].items():
        assert row[key] == value
    assert row["items"] is rows[-1]["items"]
    assert row["bcs_compound_clip_fill"] is True
    assert row["bcs_clip_fill_group_id"] == "clip-fill:19"
    proof = row["bcs_clip_fill_source_proof"]
    assert proof["source_items"] == tuple(rows[-1]["items"])
    assert proof["source_closePath"] is close and proof["implicit_fill_closure"]
    assert proof["source_even_odd"] is even_odd and proof["source_draw_order"] == 19
    assert proof["source_area_pt2_exact"] == proof["normalized_area_pt2_exact"] == "2"
    assert proof["point_serialization_error_pt_exact"] == "0"
    assert rows == original
    issue, = clips.clip_fill_issues(result)
    assert (issue["action"], issue["severity"], issue["exact"], issue["dropped"]) == (
        "path-convex-clip-contained", "info", True, False)
    second = clips.resolve_covered_clip_fills(result)
    assert second == result and clips.clip_fill_issues(second) == clips.clip_fill_issues(result)


def test_partial_convex_intersection_has_exact_area_and_bounded_serialization():
    rows = source_rows(((1, 1), (11, 1), (1, 8)))
    original = deepcopy(rows)
    result = clips.resolve_covered_clip_fills(rows)
    row, = result
    assert row["closePath"] is None and row["even_odd"] is False
    for key in ("fill", "fill_opacity", "seqno", "type", "color"):
        assert row[key] == rows[-1][key]
    proof = row["bcs_clip_fill_source_proof"]
    assert proof["source_items"] == tuple(original[-1]["items"])
    assert proof["source_area_pt2_exact"] == "35"
    assert proof["normalized_area_pt2_exact"] == "693/20"
    vertices = [(Fraction(x), Fraction(y)) for x, y in proof["normalized_vertices_exact"]]
    assert len(vertices) == 4 and (Fraction(10), Fraction(17, 10)) in vertices
    for _kind, a, b in row["items"]:
        for x, y in (a, b):
            assert 0 <= x <= 10 and 0 <= y <= 10
    assert Fraction(proof["point_serialization_error_pt_exact"]) <= Fraction(math.ulp(10.))
    assert rows == original
    assert clips.clip_fill_issues(result)[0]["action"] == "path-convex-clip-intersection"


@pytest.mark.parametrize("points", [((1, 1), (2, 2)), ((1, 1), (2, 2), (3, 3)),
                                     ((1, 1), (1, 1)), ((1, 1), (2, 2), (1, 1))])
def test_only_exact_collinear_connected_source_fill_is_nonpainting(points):
    rows = source_rows(points)
    original = deepcopy(rows)
    result = clips.resolve_covered_clip_fills(rows)
    assert result == [] and rows == original
    issue, = clips.clip_fill_issues(result)
    assert (issue["action"], issue["exact"], issue["severity"]) == ("dropped-invisible", True, "info")


@pytest.mark.parametrize("change", ["hole", "concave_clip", "star", "multiple_subjects", "bowtie",
                                    "curved_subject", "curved_clip", "nonfinite", "unknown_rule"])
def test_unqualified_path_geometry_never_becomes_a_claimed_convex_fill(change):
    rows = source_rows()
    if change == "hole":
        rows[1]["items"].append(("re", (1, 1, 4, 4), 1))
    elif change == "concave_clip":
        rows[1]["items"] = path([(0, 0), (10, 0), (10, 10), (5, 3), (0, 10)], True)
    elif change == "star":
        points = [(5+4*math.cos(i*2*math.pi/5), 5+4*math.sin(i*2*math.pi/5))
                  for i in (0, 2, 4, 1, 3)]
        rows[1]["items"] = path(points, True)
    elif change == "multiple_subjects":
        rows[-1]["items"] = path([(1, 1), (3, 1), (1, 3)], True) * 2
    elif change == "bowtie":
        rows[-1]["items"] = path([(1, 1), (9, 9), (1, 9), (9, 1)])
    elif change == "curved_subject":
        rows[-1]["items"] = [("c", (1, 1), (1, 2), (2, 1), (3, 1)), ("l", (3, 1), (1, 1))]
    elif change == "curved_clip":
        rows[1]["items"][0] = ("c", (0, 0), (3, -1), (7, -1), (10, 0))
    elif change == "nonfinite":
        rows[-1]["items"][0] = ("l", (math.inf, 1), (3, 1))
    else:
        rows[1]["even_odd"] = None
    result = clips.resolve_covered_clip_fills(rows)
    assert result == []
    issue, = clips.clip_fill_issues(result)
    assert issue["action"] == "dropped-unsupported" and not issue["exact"]


def test_nested_scissor_is_an_actual_constraint_even_when_clip_polygon_is_larger():
    rows = source_rows()
    rows[1]["scissor"] = (0, 0, 2, 2)
    row, = clips.resolve_covered_clip_fills(rows)
    proof = row["bcs_clip_fill_source_proof"]
    assert Fraction(proof["normalized_area_pt2_exact"]) < Fraction(proof["source_area_pt2_exact"])
    assert all(0 <= v <= 2 for item in row["items"] for point in item[1:] for v in point)


def test_proof_work_budget_is_checked_before_any_supporting_edge_loop(monkeypatch):
    monkeypatch.setattr(clips, "_MAX_CONVEX_CLIP_WORK", 1, raising=False)
    calls = []
    monkeypatch.setattr(clips, "_exact_clip_cross", lambda *_a: calls.append(True), raising=False)
    result = clips.resolve_covered_clip_fills(source_rows())
    assert result == [] and calls == []
    assert clips.clip_fill_issues(result)[0]["action"] == "dropped-unsupported"


def test_coordinate_bit_budget_is_checked_before_geometric_proof(monkeypatch):
    monkeypatch.setattr(clips, "_MAX_CONVEX_CLIP_BITS", 1, raising=False)
    calls = []
    monkeypatch.setattr(clips, "_exact_clip_cross", lambda *_a: calls.append(True), raising=False)
    result = clips.resolve_covered_clip_fills(source_rows())
    assert result == [] and calls == []


def test_cut_point_adapter_readback_cannot_silently_change_source_geometry(monkeypatch):
    rows = source_rows(((1, 1), (11, 1), (1, 8)))
    original = deepcopy(rows)
    monkeypatch.setattr(clips, "_like_point", lambda _sample, x, y: (x+.001, y))
    result = clips.resolve_covered_clip_fills(rows)
    assert result == [] and rows == original
    issue, = clips.clip_fill_issues(result)
    assert issue["action"] == "dropped-unsupported" and not issue["exact"]


def test_fill_stroke_original_close_cap_dash_and_geometry_are_not_normalized():
    rows = source_rows()
    rows[-1].update(type="fs", color=(1, 0, 0), width=2., lineCap=(1, 1, 1),
                    lineJoin=1, dashes="[2 3] 1", closePath=False)
    original = deepcopy(rows)
    row, = clips.resolve_covered_clip_fills(rows)
    assert row == original[-1] and rows == original
    assert "bcs_compound_clip_fill" not in row and clips.clip_fill_issues([row]) == []


@pytest.mark.parametrize("kind", ["s", "fs"])
def test_canonical_rectangle_containment_keeps_open_stroke_and_fill_stroke_semantics(kind):
    rows = source_rows()
    rows[1]["items"] = [("re", (0, 0, 10, 10), 1)]
    rows[-1].update(type=kind, color=(1, 0, 0), width=2., lineCap=(1, 1, 1),
                    lineJoin=1, dashes="[2 3] 1", closePath=False)
    if kind == "s":
        rows[-1]["fill"] = None
    original = deepcopy(rows)
    row, = clips.resolve_covered_clip_fills(rows)
    assert row == original[-1] and rows == original
    assert "bcs_compound_clip_fill" not in row and clips.clip_fill_issues([row]) == []


@pytest.mark.parametrize("kind", ["curved", "nonconvex"])
def test_other_canonical_rectangle_contained_path_policies_are_unchanged(kind):
    rows = source_rows()
    rows[1]["items"] = [("re", (0, 0, 10, 10), 1)]
    if kind == "curved":
        rows[-1]["items"] = [("c", (1, 1), (1, 2), (2, 1), (3, 1)), ("l", (3, 1), (1, 1))]
    else:
        rows[-1]["items"] = path([(1, 1), (4, 1), (3, 2), (4, 4), (1, 4)])
    original = deepcopy(rows)
    row, = clips.resolve_covered_clip_fills(rows)
    assert row == original[-1] and rows == original
    assert "bcs_compound_clip_fill" not in row


def test_implicit_pdf_fill_closure_reaches_cyclic_filled_mock_host_curve(monkeypatch, tmp_path):
    from test_clip_fill_degrade_blender import _FakeHost

    def sheets(fitz):
        rows = source_rows(((1, 1), (11, 1), (1, 8)))
        rows[-1]["fill_opacity"] = 1.
        for row in rows:
            if "rect" in row:
                row["rect"] = fitz.Rect(row["rect"])
            if "scissor" in row:
                row["scissor"] = fitz.Rect(row["scissor"])
            row["items"] = [("l", fitz.Point(item[1]), fitz.Point(item[2])) if item[0] == "l"
                            else ("re", fitz.Rect(item[1]), item[2]) for item in row["items"]]
        return [rows]

    host = _FakeHost(monkeypatch, tmp_path, sheets=sheets)
    stats = host.run()
    assert stats["cancelled"] is False and stats["pages_imported"] == 1
    obj, = host.clip_fill_objects()
    curve = obj.data
    assert curve.dimensions == "2D" and curve.fill_mode == "BOTH"
    assert curve.bevel_depth == curve.extrude == 0
    spline, = curve.splines.made
    assert spline.use_cyclic_u and len(spline.coords) == 4
    points = spline.coords
    area = abs(sum(a[0]*b[1]-b[0]*a[1]
                   for a, b in zip(points, points[1:]+points[:1], strict=True))) / 2
    assert area == pytest.approx(float(Fraction(693, 20))*(25.4/72*.001)**2)
    assert host.batched_strokes == [] and host.faces == []
    owner, = host.build_configs[0]["_source_compound_fill_objects"]
    assert owner["object"] is obj and owner["source_draw_order"] == 19
    assert owner["fill_rgb"] == (.2, .4, .6) and owner["fill_opacity"] == 1.
    tally = stats["clip_fill_delivery"]
    assert tally["resolved_exactly"] == 1 and tally["dropped"] == tally["approximated"] == 0
    assert not tally["issues_truncated"]


@pytest.mark.parametrize("canonical_rectangle", [False, True])
@pytest.mark.parametrize("cut", [False, True])
def test_original_pdf_reference_and_real_extractor_preserve_positive_filled_area(
        monkeypatch, tmp_path, cut, canonical_rectangle):
    import pymupdf
    from test_clip_fill_degrade_blender import _FakeHost

    command = b"1 1 m 11 1 l 1 8 l f" if cut else b"1 1 m 3 1 l 1 3 l f"
    with pymupdf.open() as doc:
        page = doc.new_page(width=100, height=100)
        xref = doc.get_new_xref()
        doc.update_object(xref, "<<>>")
        right = b"10" if canonical_rectangle else b"9"
        doc.update_stream(xref, b"q 0 0 20 20 re W n "
                          b"0 0 m 10 0 l " + right + b" 10 l 0 10 l h W* n .2 .4 .6 rg " + command + b" Q")
        page.set_contents(xref)
        source = doc.tobytes()
        original = page.get_drawings(extended=True)
        fill, = [r for r in original if r["type"] == "f"]
        if canonical_rectangle:
            inner = [r for r in original if r["type"] == "clip"][1]
            assert len(inner["items"]) == 1 and inner["items"][0][0] == "re"
        assert not fill.get("closePath") and len(fill["items"]) == 2
        assert any(v != 255 for v in page.get_pixmap().samples)
        if not cut:
            # Independently render the unclipped original commands: the source
            # clip genuinely makes no visible difference for this contained fill.
            reference = page.get_pixmap().samples
            doc.update_stream(xref, b".2 .4 .6 rg " + command)
            assert page.get_pixmap().samples == reference

    host = _FakeHost(monkeypatch, tmp_path, pdf=lambda _fitz: source)
    stats = host.run()
    obj, = host.clip_fill_objects()
    spline, = obj.data.splines.made
    assert obj.data.fill_mode == "BOTH" and spline.use_cyclic_u
    assert len(spline.coords) == (4 if cut else 3)
    points = spline.coords
    area = abs(sum(a[0]*b[1]-b[0]*a[1]
                   for a, b in zip(points, points[1:]+points[:1], strict=True))) / 2
    expected = (Fraction(693, 20) if canonical_rectangle else Fraction(64253, 1860)) if cut else Fraction(2)
    assert area == pytest.approx(float(expected)*(25.4/72*.001)**2)
    assert host.batched_strokes == [] and stats["clip_fill_delivery"]["dropped"] == 0
    if canonical_rectangle and cut:
        # The existing rectangular partial-cut action is deliberately retained.
        assert stats["clip_fill_delivery"]["by_action"] == {"path-rect-clip": 1}
