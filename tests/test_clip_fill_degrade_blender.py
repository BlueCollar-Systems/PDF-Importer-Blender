"""Clip-fill degrade on the Blender host: one clipped fill never costs the document.

Synthetic rows only (fictional sheet D042, job 1000-01); no private drawing required.
"""
from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest


class _Links:
    def __init__(self):
        self.items = []

    def link(self, value):
        self.items.append(value)


class _Collection:
    def __init__(self, name):
        self.name = name
        self.children = _Links()
        self.objects = _Links()

    @property
    def all_objects(self):
        return list(self.objects.items)


class _Collections:
    def __init__(self):
        self.items = []

    def new(self, name):
        value = _Collection(name)
        self.items.append(value)
        return value

    def remove(self, value, **_kwargs):
        self.items.remove(value)


class _Object(dict):
    def __init__(self, name, data):
        super().__init__()
        self.name = name
        self.data = data


class _Datablocks:
    """bpy.data.curves / bpy.data.objects: records what was made and removed."""

    def __init__(self, factory):
        self.factory = factory
        self.made = []
        self.removed = []

    def new(self, *args, **kwargs):
        value = self.factory(*args, **kwargs)
        self.made.append(value)
        return value

    def remove(self, value, **_kwargs):
        self.removed.append(value)


def _new_curve(name, type):  # noqa: A002 - Blender's keyword
    splines = []

    def spline_new(_kind):
        spline = types.SimpleNamespace()
        splines.append(spline)
        return spline

    return types.SimpleNamespace(
        name=name, splines=types.SimpleNamespace(new=spline_new, made=splines), materials=[]
    )


def _install_blender_stubs(monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch-guarded; the host version is the one the report tests expect."""
    fake_bpy = types.ModuleType("bpy")
    fake_bpy.app = types.SimpleNamespace(version=(4, 1, 0))
    fake_bpy.ops = types.SimpleNamespace(
        wm=types.SimpleNamespace(redraw_timer=lambda **_kwargs: None)
    )
    fake_bpy.types = types.SimpleNamespace(
        Collection=object, Material=object, Object=object, VectorFont=object,
        Operator=type("Operator", (), {}),
    )
    fake_bpy.data = types.SimpleNamespace(
        collections=_Collections(),
        curves=_Datablocks(_new_curve),
        objects=_Datablocks(_Object),
    )
    fake_bpy.context = types.SimpleNamespace(
        scene=types.SimpleNamespace(collection=types.SimpleNamespace(children=_Links())),
        view_layer=types.SimpleNamespace(update=lambda: None),
        evaluated_depsgraph_get=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    monkeypatch.setitem(sys.modules, "bmesh", types.SimpleNamespace())
    return fake_bpy


def _sheet_rows(fitz, unresolvable=True, unbuildable=True):
    """Sheet D042: a stroke and clipped fills that meet different fates."""
    point, rect = fitz.Point, fitz.Rect

    def ring(points):
        return [("l", point(*a), point(*b)) for a, b in zip(points, points[1:] + points[:1], strict=True)]

    def flood(level, seqno, box, fill):
        return {"type": "f", "level": level, "seqno": seqno, "rect": rect(*box),
                "items": [("re", rect(*box), 1)], "fill": fill, "fill_opacity": 1.0}

    rows = [
        {"type": "s", "level": 0, "seqno": 0, "rect": rect(5, 95, 95, 95), "color": (0, 0, 0),
         "width": 1.0, "closePath": False, "items": [("l", point(5, 95), point(95, 95))]},
        # seqno 7: an ordinary masked shape with a hole; always buildable.
        {"type": "clip", "level": 0, "scissor": rect(0, 50, 20, 70), "even_odd": True,
         "items": ring([(0, 50), (20, 50), (20, 70), (0, 70)]) + [("re", rect(5, 55, 15, 65), 1)]},
        flood(1, 7, (-1, 49, 21, 71), (0, 1, 0)),
        # seqno 9: a rectangle through a rectangular clip resolves exactly (info only).
        {"type": "clip", "level": 0, "scissor": rect(30, 60, 40, 70), "even_odd": False,
         "items": [("re", rect(30, 60, 40, 70), 1)]},
        flood(1, 9, (35, 65, 50, 80), (0, 0, 0)),
    ]
    if unresolvable:
        # seqno 3: two different triangles clip at once; their intersection is
        # not computed, so the resolver leaves this one fill out.
        rows += [
            {"type": "clip", "level": 0, "scissor": rect(10, 10, 50, 45), "even_odd": False,
             "items": ring([(10, 10), (50, 10), (30, 45)])},
            {"type": "clip", "level": 1, "scissor": rect(20, 12, 50, 45), "even_odd": False,
             "items": ring([(20, 12), (60, 12), (40, 50)])},
            flood(2, 3, (0, 0, 100, 100), (1, 0, 0)),
        ]
    if unbuildable:
        # seqno 5: resolves, but its clip path carries a two-point sliver contour
        # that Blender's builder refuses ("degenerate or non-finite contour").
        rows += [
            {"type": "clip", "level": 0, "scissor": rect(60, 60, 90, 90), "even_odd": True,
             "items": ring([(60, 60), (90, 60), (90, 90), (60, 90)])
             + [("l", point(70, 70), point(71, 71)), ("l", point(71, 71), point(70, 70))]},
            flood(1, 5, (55, 55, 95, 95), (0, 0, 1)),
        ]
    return rows


def _rect_only_clip_rows(fitz, adapter_shaped=False):
    """Sheet D042: a clip path made only of 're' items, partly covered by its fill.

    No 'l'/'c' item leaves the resolver no line point to copy the point type from.
    With PyMuPDF rectangles it borrows a rectangle corner instead. Rows from a simple
    page adapter carry plain tuples, so nothing has a point type: the cut path comes
    out as bare (x, y) tuples, which extract_page cannot read as line ends.
    """
    point = fitz.Point
    rect = (lambda *box: tuple(float(v) for v in box)) if adapter_shaped else fitz.Rect
    return [
        {"type": "s", "level": 0, "seqno": 0, "rect": rect(5, 95, 95, 95), "color": (0, 0, 0),
         "width": 1.0, "closePath": False, "items": [("l", point(5, 95), point(95, 95))]},
        {"type": "clip", "level": 0, "scissor": rect(10, 10, 60, 40), "even_odd": True,
         "items": [("re", rect(10, 10, 30, 40), 1), ("re", rect(40, 10, 60, 40), 1)]},
        {"type": "f", "level": 1, "seqno": 11, "rect": rect(0, 0, 50, 50),
         "items": [("re", rect(0, 0, 50, 50), 1)], "fill": (1, 0, 0), "fill_opacity": 1.0},
    ]


def _rect_only_clip_pdf(fitz) -> bytes:
    """A real one-page PDF of the same shape: `re re W n`, then a partly covering flood."""
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.draw_line((10, 190), (190, 190), color=(0, 0, 0), width=1.0)
    xref = page.get_contents()[0]
    doc.update_stream(
        xref, doc.xref_stream(xref) + b"\nq 20 20 40 60 re 80 20 40 60 re W n 1 0 0 rg 0 0 100 50 re f Q\n"
    )
    data = doc.tobytes()
    doc.close()
    return data


class _FakeHost:
    """import_pdf on a fake document (or real PDF bytes), real extractor, real build_page."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sheets=None, pdf=None, **sheet):
        self.bpy = _install_blender_stubs(monkeypatch)
        self.engine = importlib.import_module("pdf_vector_importer.bl_import_engine")
        self.session = importlib.import_module("pdf_vector_importer.import_session")
        fitz_loader = importlib.import_module("pdf_vector_importer.pdfcadcore.fitz_loader")
        fitz = fitz_loader.import_fitz()
        pages = sheets(fitz) if sheets else [_sheet_rows(fitz, **sheet)]

        class Page:
            rect = fitz.Rect(0, 0, 100, 100)
            mediabox = fitz.Rect(0, 0, 100, 100)
            rotation = 0

            def __init__(self, rows):
                self.rows = rows

            def get_drawings(self, extended=False):
                assert extended
                return self.rows

            def get_text(self, _kind):
                return []

        class Document:
            page_count = len(pages)
            is_closed = False

            def load_page(self, index):
                return Page(pages[index])

            def close(self):
                self.is_closed = True

        engine = self.engine
        monkeypatch.setattr(engine, "bpy", self.bpy)
        monkeypatch.setattr(engine, "check_pymupdf", lambda: True)
        monkeypatch.setattr(engine, "ensure_lib_path", lambda: None)
        monkeypatch.setattr(engine, "recognition", types.SimpleNamespace(run=lambda *_a, **_k: None))
        monkeypatch.setattr(engine, "cleanup_primitives", lambda *_a, **_k: {})
        if pdf is None:
            # Synthetic drawing rows exercise the real resolver/extractor and
            # builder, but do not carry PDF dictionary/SVG paint-order evidence.
            # Keep those independently tested planners out of this fixture.
            for module_name, function_name, result in (
                ("opaque_rectangle_proof", "plan_opaque_rectangles", []),
                ("triangle_paint_order", "plan_terminal_triangles", ([], [])),
                ("compound_paint_order", "plan_compound_fills", ([], [])),
            ):
                module = importlib.import_module(f"pdf_vector_importer.{module_name}")
                monkeypatch.setattr(module, function_name, lambda *_a, _result=result, **_k: _result)
        if pdf is None:
            monkeypatch.setattr(fitz_loader, "import_fitz", lambda **_kwargs: object())
            monkeypatch.setattr(fitz_loader, "safe_open", lambda _path: Document())
        monkeypatch.setitem(engine.extract_page.__globals__, "_extract_text", lambda *_a, **_k: [])
        # The builder's own globals, so a test that re-imported the module
        # earlier cannot leave this one patching a stale copy.
        builder = engine.build_page.__globals__
        self.batched_strokes = []
        self.faces = []
        self.build_configs = []
        compound_order = importlib.import_module("pdf_vector_importer.compound_paint_order")
        real_apply_compounds = compound_order.apply_compound_fills

        def record_compound_ownership(plans, collection, config):
            self.build_configs.append(config)
            return real_apply_compounds(plans, collection, config)

        monkeypatch.setattr(compound_order, "apply_compound_fills", record_compound_ownership)
        # Face construction below intentionally returns an identity-only fake;
        # evaluated native mesh/depth checks have their own host doubles.
        fill_order = importlib.import_module("pdf_vector_importer.fill_paint_order")
        monkeypatch.setattr(fill_order, "apply_fill_depths", lambda *_a, **_k: [])

        def face_mesh(name, *_args, **_kwargs):
            # The exactly resolved rectangle takes the ordinary fill path.
            self.faces.append(name)
            return _Object(name, None)

        def multi_poly_curve(name, runs, collection, *_args, **_kwargs):
            self.batched_strokes.append(runs)
            obj = _Object(name, None)
            collection.objects.link(obj)
            return obj

        monkeypatch.setitem(builder, "bpy", self.bpy)
        monkeypatch.setitem(builder, "_resolve_collection", lambda collection, *_a, **_k: collection)
        monkeypatch.setitem(builder, "_get_or_create_material", lambda *_a, **_k: "ink")
        monkeypatch.setitem(builder, "_write_spline_points", lambda spline, pts, **_k: setattr(spline, "coords", pts))
        monkeypatch.setitem(builder, "_create_multi_poly_curve", multi_poly_curve)
        monkeypatch.setitem(builder, "_create_face_mesh", face_mesh)
        self.pdf = tmp_path / "D042.pdf"
        self.pdf.write_bytes(pdf(fitz) if pdf else b"%PDF-1.7\n")
        self.report_path = tmp_path / "D042_import_report.json"
        self.checkpoint = tmp_path / "resume.json"

    def run(self, **overrides):
        config = {
            "mode": "vector",
            "pages": "1",
            "import_text": False,
            "ignore_images": True,
            "auto_focus_view": False,
            "auto_hide_default_cube": False,
            # This fixture verifies source-fill construction/reporting, not the
            # separately tested optional native display-aid plane.
            "white_page_background": False,
            "import_report_path": str(self.report_path),
            "resume_checkpoint_path": str(self.checkpoint),
        }
        callbacks = {key: overrides.pop(key, None) for key in ("cancel_callback", "progress_callback")}
        config.update(overrides)
        return self.engine.import_pdf(str(self.pdf), config=config, **callbacks)

    def clip_fill_objects(self):
        return [obj for obj in self.bpy.data.objects.made if obj.get("bcs_compound_clip_fill")]


def test_unresolvable_and_unbuildable_fills_are_left_out_and_the_page_still_imports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Contract (a)+(b)+(c): drops are per fill, reported, and never an abort."""
    host = _FakeHost(monkeypatch, tmp_path)

    stats = host.run()

    # Everything else on the sheet was built: the stroke, the rectangle, the mask.
    assert stats["cancelled"] is False and stats["pages_imported"] == 1
    assert len(host.batched_strokes) == 1
    assert len(host.faces) == 1
    built = host.clip_fill_objects()
    assert [obj["bcs_clip_fill_group_id"] for obj in built] == ["clip-fill:7"]
    assert built[0]["bcs_clip_fill_contour_count"] == 2
    owned = host.build_configs[0]["_source_compound_fill_objects"]
    assert len(owned) == 1 and owned[0]["object"] is built[0]
    assert owned[0]["source_draw_order"] == 7
    assert owned[0]["fill_opacity"] == 1.0 and owned[0]["fill_rgb"] == (0.0, 1.0, 0.0)
    assert owned[0]["even_odd"] is True
    assert len(owned[0]["primitive_ids"]) == len(set(owned[0]["primitive_ids"])) == 2
    assert len(owned[0]["contours_mm"]) == 2
    assert [row["source_draw_order"] for row in owned] == [7]  # Dropped seqnos 3/5 are never owned.
    # Nothing half-built is left behind for the fill the builder refused.
    assert host.bpy.data.curves.removed == [] and host.bpy.data.objects.removed == []
    assert len(host.bpy.data.curves.made) == 1

    tally = stats["clip_fill_delivery"]
    assert {key: tally[key] for key in ("resolved_exactly", "dropped_invisible", "approximated", "dropped")} == {
        "resolved_exactly": 1, "dropped_invisible": 0, "approximated": 0, "dropped": 2,
    }
    assert tally["by_action"] == {"dropped-unsupported": 2, "rect-intersection": 1}
    core_drop, host_drop = tally["issues"]
    assert (core_drop["page"], core_drop["seqno"], core_drop["reason"]) == (1, 3, "nested")
    assert "stage" not in core_drop
    assert host_drop == {
        "seqno": 5, "reason": "host-build-error", "action": "dropped-unsupported", "exact": False,
        "severity": "warning", "dropped": True, "stage": "host-build", "page": 1,
        "detail": "ValueError: Clip fill contains a degenerate or non-finite contour",
        "paint_rect": [60.0, 60.0, 90.0, 90.0], "fill": [0.0, 0.0, 1.0], "fill_opacity": 1.0,
    }
    # Dropped fills are warnings, not geometry delivery failures (those are terminal).
    assert stats["geometry_delivery_issues"] == []
    assert stats["clip_fill_warning"] == (
        "2 clipped fill(s) could not be resolved and were left out (drawing order 3, 5) on page 1"
    )

    report = json.loads(host.report_path.read_text(encoding="utf-8"))
    block = report["extra"]["clip_fill_delivery"]
    assert block["dropped"] == 2 and block["resolved_exactly"] == 1
    assert [issue["seqno"] for issue in block["issues"]] == [3, 5]
    assert block["issues_truncated"] is False and block["warning_pages"] == [1]
    assert block["summary"] == stats["clip_fill_warning"]
    assert report["result"]["warnings"] == 2
    assert report["extra"]["result_status"] == "success"
    assert "terminal_failure" not in report["extra"]


def test_page_with_only_exactly_resolved_fills_reports_counts_and_no_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host = _FakeHost(monkeypatch, tmp_path, unresolvable=False, unbuildable=False)

    stats = host.run()

    assert len(host.clip_fill_objects()) == 1 and len(host.faces) == 1
    assert stats["clip_fill_delivery"]["resolved_exactly"] == 1
    assert stats["clip_fill_delivery"]["issues"] == []
    assert stats["clip_fill_warning"] == ""
    report = json.loads(host.report_path.read_text(encoding="utf-8"))
    assert report["extra"]["clip_fill_delivery"]["by_action"] == {"rect-intersection": 1}
    assert report["extra"]["clip_fill_delivery"]["summary"] == ""
    assert report["result"]["warnings"] == 0


@pytest.mark.parametrize("source", ["rows", "pdf"])
def test_clip_path_made_only_of_rectangles_is_built_instead_of_aborting_the_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str
) -> None:
    """extract_page refuses the resolver's tuple-point rows; the engine re-extracts on readable ones."""
    sheet = {"pdf": _rect_only_clip_pdf} if source == "pdf" else {"sheets": lambda fitz: [_rect_only_clip_rows(fitz)]}
    host = _FakeHost(monkeypatch, tmp_path, **sheet)

    stats = host.run()

    assert stats["cancelled"] is False and stats["pages_imported"] == 1
    assert len(host.batched_strokes) == 1
    (built,) = host.clip_fill_objects()
    # Both rectangles of the clip path, each cut to the fill: nothing flooded, nothing lost.
    assert built["bcs_clip_fill_contour_count"] == 2 and built["bcs_clip_fill_even_odd"] is True
    tally = stats["clip_fill_delivery"]
    assert (tally["resolved_exactly"], tally["dropped"], tally["by_action"]) == (1, 0, {"polygon-rect": 1})
    assert stats["clip_fill_warning"] == ""
    report = json.loads(host.report_path.read_text(encoding="utf-8"))
    assert report["extra"]["clip_fill_delivery"]["resolved_exactly"] == 1
    assert report["result"]["warnings"] == 0


def test_streamed_import_reopens_after_a_page_the_extractor_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """iter_pages dies with the page it cannot extract; the pages after it must still arrive."""
    host = _FakeHost(monkeypatch, tmp_path, sheets=lambda fitz: [
        _sheet_rows(fitz, unresolvable=False, unbuildable=False),
        _rect_only_clip_rows(fitz, adapter_shaped=True),
        _sheet_rows(fitz, unresolvable=False, unbuildable=False),
    ])
    iter_pages = host.engine.iter_pages
    streamed = []
    monkeypatch.setattr(
        host.engine, "iter_pages",
        lambda doc, pages, **kwargs: streamed.append(list(pages)) or iter_pages(doc, pages=pages, **kwargs),
    )
    extracted = []

    stats = host.run(pages="1-3", progress_callback=lambda pct, message: (
        extracted.append((message.partition(":")[0], pct)) if message.startswith("Extracted page") else None
    ))

    assert stats["cancelled"] is False and stats["pages_imported"] == 3
    assert streamed == [[1, 2, 3], [3]]
    assert len(host.batched_strokes) == 3
    assert [obj["bcs_clip_fill_group_id"] for obj in host.clip_fill_objects()] == [
        "clip-fill:7", "clip-fill:11", "clip-fill:7",
    ]
    assert stats["clip_fill_delivery"]["resolved_exactly"] == 3 and stats["clip_fill_warning"] == ""
    # The reopened stream keeps the import's page count and progress position.
    assert [name for name, _pct in extracted] == ["Extracted page 1/3", "Extracted page 3/3"]
    assert [pct for _name, pct in extracted] == pytest.approx([0.1875, 0.6875])


def test_second_extraction_attempt_reraises_what_clip_fills_did_not_cause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host = _FakeHost(monkeypatch, tmp_path, unresolvable=False, unbuildable=False)
    fitz_loader = importlib.import_module("pdf_vector_importer.pdfcadcore.fitz_loader")
    document = fitz_loader.safe_open(str(host.pdf))

    for error in (RuntimeError("SAMPLE text layer is damaged"), host.session.ImportCancelledError("cancel requested")):
        with pytest.raises(type(error)) as raised:
            host.engine._extract_page_with_readable_clip_fills(document, 1, {}, error)
        assert raised.value is error


def test_raster_delivered_page_does_not_warn_about_vector_fills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host = _FakeHost(monkeypatch, tmp_path)

    stats = host.run(mode="raster")

    assert stats["pages_imported"] == 1 and host.bpy.data.objects.made == []
    tally = stats["clip_fill_delivery"]
    assert tally["dropped"] == 0 and tally["by_action"] == {} and tally["issues"] == []
    assert stats["clip_fill_warning"] == ""


def test_make_faces_off_skips_clip_fills_without_reporting_a_build_drop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host = _FakeHost(monkeypatch, tmp_path)

    stats = host.run(make_faces=False)

    assert host.clip_fill_objects() == []
    assert [issue["seqno"] for issue in stats["clip_fill_delivery"]["issues"]] == [3]
    assert all(issue.get("stage") != "host-build" for issue in stats["clip_fill_delivery"]["issues"])


@pytest.mark.parametrize("failure", ["ImportCancelledError", "KeyboardInterrupt"])
def test_cancellation_inside_a_clip_fill_build_still_propagates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    """Contract (d): the per-fill backstop must not swallow a cancel."""
    host = _FakeHost(monkeypatch, tmp_path)
    error = host.session.ImportCancelledError if failure == "ImportCancelledError" else KeyboardInterrupt

    def cancelled(*_args, **_kwargs):
        raise error("cancel requested")

    monkeypatch.setitem(host.engine.build_page.__globals__, "_create_compound_clip_fill", cancelled)

    if error is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            host.run()
        return
    stats = host.run()
    assert stats["cancelled"] is True and stats["pages_imported"] == 0
    assert stats["resume"]["remaining_pages"] == [1]
    # The discarded page is rebuilt on resume; counting it now would count it twice.
    assert stats["clip_fill_delivery"]["dropped"] == 0
    assert stats["clip_fill_delivery"]["issues"] == []
    # The tally rides in the resume checkpoint, so completed pages keep theirs.
    assert stats["resume"]["aggregate_stats"]["clip_fill_delivery"] == stats["clip_fill_delivery"]


def test_half_built_clip_fill_is_removed_before_the_drop_is_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An outer ring left in the scene without its hole would flood the shape."""
    host = _FakeHost(monkeypatch, tmp_path)
    builder = host.engine.build_page.__globals__
    written = []

    def failing_hole(spline, pts, **_kwargs):
        written.append(pts)
        if len(written) == 2:
            raise RuntimeError("spline allocation failed")
        spline.coords = pts

    monkeypatch.setitem(builder, "_write_spline_points", failing_hole)
    # Only the buildable mask (seqno 7) reaches the curve writer first.
    monkeypatch.setitem(
        builder, "_clip_fill_build_drop",
        lambda page_data, group_id, prim, error: {"seqno": prim.source_draw_order, "severity": "warning",
                                                   "dropped": True, "action": "dropped-unsupported",
                                                   "detail": f"{type(error).__name__}: {error}"},
    )

    stats = host.run()

    details = {issue["seqno"]: issue["detail"] for issue in stats["clip_fill_delivery"]["issues"]}
    assert details[7] == "RuntimeError: spline allocation failed"
    assert host.clip_fill_objects() == []
    assert len(host.bpy.data.curves.removed) == 1
    assert stats["pages_imported"] == 1 and len(host.batched_strokes) == 1


def test_reporting_a_build_drop_cannot_itself_abort_the_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The record is written inside the except handler: nothing in it may raise."""
    host = _FakeHost(monkeypatch, tmp_path, unresolvable=False, unbuildable=False)
    builder = host.engine.build_page.__globals__

    class Unprintable(Exception):
        def __str__(self):
            raise RuntimeError("no message")

    def refuse(*_args, **_kwargs):
        raise Unprintable()

    monkeypatch.setitem(builder, "_create_compound_clip_fill", refuse)

    stats = host.run()

    assert stats["pages_imported"] == 1 and len(host.batched_strokes) == 1
    (drop,) = stats["clip_fill_delivery"]["issues"]
    assert (drop["seqno"], drop["detail"], drop["stage"]) == (7, "Unprintable", "host-build")
    # With nothing to read the fill from, a shorter record still reports the drop.
    bare = builder["_clip_fill_build_drop"](None, "clip-fill:x", object(), ValueError("SAMPLE"))
    assert (bare["seqno"], bare["detail"], bare["dropped"]) == (None, "ValueError: SAMPLE", True)
    assert json.loads(json.dumps(bare, allow_nan=False))["severity"] == "warning"


def test_clip_fill_tally_survives_cancel_and_resume_and_caps_listed_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_blender_stubs(monkeypatch)
    engine = importlib.import_module("pdf_vector_importer.bl_import_engine")
    stats = {"clip_fill_delivery": engine._new_clip_fill_delivery()}
    info = {"seqno": 1, "action": "clip-path", "severity": "info", "dropped": False, "exact": True}
    hidden = {"seqno": 2, "action": "dropped-invisible", "severity": "info", "dropped": True, "exact": True}
    flattened = {"seqno": 4, "action": "polygon-rect-flattened", "severity": "warning", "dropped": False}
    engine._record_clip_fill_issues(stats, 1, [info] * 3000 + [hidden] * 40 + [flattened], None)
    # A checkpoint is JSON; the tally must come back from it unchanged.
    stats = json.loads(json.dumps(stats, allow_nan=False))
    drops = [{"seqno": 100 + n, "action": "dropped-unsupported", "severity": "warning", "dropped": True}
             for n in range(250)]
    # The resolver resolved seqno 100, then the builder dropped it: one fill, one record.
    resolved_then_dropped = dict(info, seqno=100)
    engine._record_clip_fill_issues(stats, 12, [resolved_then_dropped], drops)

    tally = stats["clip_fill_delivery"]
    assert (tally["resolved_exactly"], tally["dropped_invisible"]) == (3000, 40)
    assert (tally["approximated"], tally["dropped"]) == (1, 250)
    assert len(tally["issues"]) == 200 and tally["issues_truncated"] is True
    assert tally["warning_pages"] == [1, 12]
    line = engine._clip_fill_warning_line(tally)
    assert line.startswith("199 clipped fill(s) could not be resolved and were left out; 1 clipped fill(s) "
                           "are approximate (flattened curves or crossing contours) (drawing order ")
    assert line.endswith("; 251 fills were affected in all, the report lists the first 200 on pages 1, 12")
    # A checkpoint written before this tally existed, or a damaged one, must not abort a resume.
    damaged = {"clip_fill_delivery": {"dropped": 2, "by_action": None, "issues": "lost"}}
    engine._record_clip_fill_issues(damaged, 3, [flattened], None)
    assert (damaged["clip_fill_delivery"]["dropped"], damaged["clip_fill_delivery"]["approximated"]) == (2, 1)
    assert [issue["page"] for issue in damaged["clip_fill_delivery"]["issues"]] == [3]
    # When every page after the resume is raster nothing repairs the tally first:
    # the end-of-import helpers meet it as restored, after the objects were built.
    listed = [dict(flattened, page=2)]
    for lost in ({"dropped": "lost", "approximated": None, "issues": listed},
                 {"approximated": 1, "by_action": {"polygon-rect-flattened": "many"}, "issues": listed},
                 {"approximated": float("inf"), "by_action": "lost", "issues": listed}):
        assert engine._clip_fill_warning_line(lost).endswith("(drawing order 4) on page 2")
        block = engine._clip_fill_delivery_block({"clip_fill_delivery": lost})
        assert json.loads(json.dumps(block, allow_nan=False))["warning_pages"] == [2]


def test_report_block_and_warning_count_carry_the_clip_fill_tally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Contract (c), report writer alone: counts come from the tally, not the capped list."""
    _install_blender_stubs(monkeypatch)
    engine = importlib.import_module("pdf_vector_importer.bl_import_engine")
    monkeypatch.setattr(engine, "bpy", sys.modules["bpy"])
    monkeypatch.setattr(engine, "_pymupdf_version", lambda: "")
    stats = {"pages_imported": 2, "primitives": 60, "clip_fill_delivery": engine._new_clip_fill_delivery()}
    drops = [{"seqno": n, "reason": "nested", "action": "dropped-unsupported", "exact": False,
              "severity": "warning", "dropped": True, "detail": "SAMPLE", "paint_rect": [0, 0, 1, 1],
              "fill": [0, 0, 0], "fill_opacity": 1.0} for n in range(205)]
    engine._record_clip_fill_issues(stats, 2, drops, None)
    report_path = tmp_path / "MXT-100_import_report.json"

    engine.write_import_report(
        str(tmp_path / "MXT-100.pdf"), {"import_text": False}, stats,
        import_mode="vector", output_path=str(report_path),
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    block = report["extra"]["clip_fill_delivery"]
    assert block["dropped"] == 205 and block["by_action"] == {"dropped-unsupported": 205}
    assert len(block["issues"]) == 200 and block["issues_truncated"] is True
    assert all(issue["page"] == 2 for issue in block["issues"])
    assert report["result"]["warnings"] == 205
    assert "warnings_present" in report["extra"]["diagnostics"]["signals"]
    # A dropped clip fill is a warning; it must never become a terminal failure.
    assert report["extra"]["result_status"] == "success"
    assert engine._terminal_import_failures({"import_text": False}, stats, None) == []
    # A run that kept no tally (older callers) writes no block and no warning.
    engine.write_import_report(
        str(tmp_path / "MXT-100.pdf"), {"import_text": False}, {"primitives": 60},
        import_mode="vector", output_path=str(report_path),
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "clip_fill_delivery" not in report["extra"] and report["result"]["warnings"] == 0


@pytest.fixture
def operators_module(monkeypatch: pytest.MonkeyPatch):
    fake_bpy = _install_blender_stubs(monkeypatch)
    props = types.ModuleType("bpy.props")
    for name in ("BoolProperty", "EnumProperty", "FloatProperty", "StringProperty"):
        setattr(props, name, lambda **_kwargs: None)
    io_utils = types.ModuleType("bpy_extras.io_utils")
    io_utils.ImportHelper = type("ImportHelper", (), {})
    bpy_extras = types.ModuleType("bpy_extras")
    bpy_extras.io_utils = io_utils
    monkeypatch.setitem(sys.modules, "bpy.props", props)
    monkeypatch.setitem(sys.modules, "bpy_extras", bpy_extras)
    monkeypatch.setitem(sys.modules, "bpy_extras.io_utils", io_utils)
    engine = importlib.import_module("pdf_vector_importer.bl_import_engine")
    previous = sys.modules.pop("pdf_vector_importer.operators", None)
    try:
        yield importlib.import_module("pdf_vector_importer.operators"), engine, fake_bpy
    finally:
        sys.modules.pop("pdf_vector_importer.operators", None)
        if previous is not None:
            sys.modules["pdf_vector_importer.operators"] = previous


@pytest.mark.parametrize("warning,expected", [
    ("2 clipped fill(s) could not be resolved and were left out (drawing order 3, 5) on page 1", 1),
    ("", 0),
])
def test_operator_emits_one_clip_fill_warning_line_per_import(
    monkeypatch: pytest.MonkeyPatch, operators_module, warning: str, expected: int
) -> None:
    operators, engine, _fake_bpy = operators_module
    monkeypatch.setattr(engine, "import_pdf", lambda *_a, **_k: {
        "primitives": 12, "pages_imported": 1, "clip_fill_warning": warning,
        "import_report_path": "D042_import_report.json",
    })
    operator = operators.IMPORT_OT_pdf_vector()
    for name in ("mode", "pages", "text_mode", "visual_style", "page_arrangement", "model3d_mode"):
        setattr(operator, name, "")
    for name in ("show_advanced", "resume_interrupted", "import_text", "group_by_color", "auto_focus_view",
                 "keep_selection_after_focus", "auto_hide_default_cube", "white_page_background"):
        setattr(operator, name, False)
    for name in ("line_z_offset_mm", "text_z_offset_mm", "image_z_offset_mm", "page_gap_ratio", "model3d_depth_mm"):
        setattr(operator, name, 0.0)
    operator.filepath = "D042.pdf"
    reports = []
    operator.report = lambda level, message: reports.append((level, message))
    context = types.SimpleNamespace(
        preferences=types.SimpleNamespace(addons={}), workspace=None,
    )

    assert operator.execute(context) == {"FINISHED"}

    warnings = [message for level, message in reports if level == {"WARNING"}]
    assert len(warnings) == expected
    if expected:
        assert warnings[0].startswith(warning + ". Everything else was imported; review ")
        assert "extra.clip_fill_delivery in D042_import_report.json" in warnings[0]
    assert not [message for level, message in reports if level == {"ERROR"}]
