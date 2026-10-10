"""One failed sheet-edge text check never costs the sheet or the sheets after it.

A letter whose trim to the sheet edge cannot be proven stays as delivered
(untrimmed) and is listed; the sheet's lines and other text stay, and later
sheets are still imported. Fictional sheet D042 only.
"""
import json
import sys
import types

import pytest

if "bpy" not in sys.modules:
    sys.modules["bpy"] = types.SimpleNamespace()
if not hasattr(sys.modules["bpy"], "app"):
    sys.modules["bpy"].app = types.SimpleNamespace(version=(4, 1, 0))
if not hasattr(sys.modules["bpy"], "types"):
    sys.modules["bpy"].types = types.SimpleNamespace(Collection=object, Object=object)
if "bmesh" not in sys.modules:
    sys.modules["bmesh"] = types.SimpleNamespace()

from pdf_vector_importer import bl_import_engine as engine  # noqa: E402
from pdf_vector_importer import text_page_clip  # noqa: E402


def _records():
    return [{"page": number, "item_id": f"page:{number}:text:1", "source_span_id": 1,
             "status": "delivered", "requested_representation": "text",
             "final_representation": "text", "entity_ids": [f"page{number}_text"]}
            for number in (1, 2)]


def setup_page(monkeypatch, clip):
    owned = types.SimpleNamespace(name="page2_text")
    page = types.SimpleNamespace(name="page2", all_objects=[owned])
    objects = {owned.name: owned, "peer": object()}
    collections = {page.name: page, "page1": object()}
    monkeypatch.setattr(engine, "_discard_page_collection",
                        lambda _collection: pytest.fail("the sheet must never be discarded"))
    monkeypatch.setattr(engine.bpy, "data", types.SimpleNamespace(
        objects=objects, collections=collections), raising=False)
    records = _records()
    provenance = types.SimpleNamespace(_text_delivery_records=records)
    monkeypatch.setattr(text_page_clip, "clip_delivered_page_text",
                        lambda *args, **kwargs: clip(records, *args, **kwargs))
    stats = {"text_source_spans": 2, "text_items": 2, "pages_imported": 2,
             "pages_requested": 3}
    assert engine._clip_text_page_guarded(
        page, provenance, stats, page_number=2, width_mm=100, height_mm=80) is True
    return stats, provenance, objects, collections


def _clip_raises(records, *_args, **_kwargs):
    # One letter was already left untrimmed before the sheet-level error.
    records[1].setdefault("page_viewport_clip_skipped", []).append(
        {"entity_id": "page2_text", "page": 2, "reason": "ValueError: edge letter"})
    raise ValueError("Native visible ink lost")


def test_clip_exception_keeps_the_sheet_and_names_it(monkeypatch, tmp_path):
    stats, provenance, objects, collections = setup_page(monkeypatch, _clip_raises)
    assert "page2_text" in objects and "page2" in collections
    assert [record["status"] for record in provenance._text_delivery_records] == ["delivered"] * 2
    assert provenance._text_delivery_records[1]["entity_ids"] == ["page2_text"]
    warning = stats["text_page_edge_warnings"][0]
    assert warning["page"] == 2 and "Native visible ink lost" in warning["reason"]
    assert warning["skipped_items"] == [
        {"entity_id": "page2_text", "page": 2, "reason": "ValueError: edge letter"}]
    assert "text_page_viewport_failures" not in stats
    config = {"import_text": True, "text_mode": "text"}
    assert engine._terminal_import_failures(config, stats, provenance) == []

    report = tmp_path / "report.json"
    engine.write_import_report("D042.pdf", config, stats, import_mode="vector",
                               output_path=str(report), provenance_opts=provenance)
    data = json.loads(report.read_text())
    extra = data["extra"]
    assert extra["result_status"] == "success"
    assert extra["text_page_edge_warnings"][0]["page"] == 2
    assert extra["pages_requested"] == 3
    assert extra["text_delivery"]["summary"]["failed_items"] == 0
    assert data["fallback"]["used"] is True
    assert "1 letter on sheet 2 was left untrimmed at the sheet edge." in extra["human_summary"]


def test_letters_left_untrimmed_by_a_finished_clip_are_listed(monkeypatch):
    skipped = [{"entity_id": "page2_text", "page": 2, "reason": "ValueError: area"}]

    def clip(_records, *_args, **_kwargs):
        return text_page_clip.PageClipResult([], skipped)

    stats, _provenance, objects, _collections = setup_page(monkeypatch, clip)
    assert "page2_text" in objects
    assert stats["text_page_edge_warnings"] == [{
        "page": 2, "reason": "letters left untrimmed at the sheet edge",
        "stage": "page_viewport", "skipped_items": skipped}]


def test_a_clean_clip_adds_no_warning(monkeypatch):
    stats, *_rest = setup_page(monkeypatch, lambda *_a, **_k: text_page_clip.PageClipResult())
    assert "text_page_edge_warnings" not in stats


class _Matrix:
    def __matmul__(self, vector):
        return types.SimpleNamespace(x=vector[0], y=vector[1], z=vector[2])


class _Clipped(dict):
    def __init__(self, name):
        super().__init__(pdf_page_clip_helper_id="Guide", pdf_page_clip_expected_area_m2=1e-6,
                         pdf_page_clip_source_z_m=[0.0, 0.001])
        self.name = name
        self.type = "FONT"
        self.location = [0.01, -0.5, 0.0]


def _reverify(monkeypatch, record, *, verify_error="Native text viewport changed the visible source ink area"):
    guide = {"pdf_page_clip_helper": True}
    guide = types.SimpleNamespace(get=guide.get, matrix_world=_Matrix(),
                                  bound_box=[(0, 0, 0), (0.1, 0.08, 0)])
    registry = {"Guide": guide}
    registry.update({name: _Clipped(name) for name in record["entity_ids"] if "missing" not in name})
    monkeypatch.setattr(engine, "bpy", types.SimpleNamespace(
        data=types.SimpleNamespace(objects=registry)))
    monkeypatch.setitem(sys.modules, "mathutils", types.SimpleNamespace(Vector=tuple))

    def verify(obj, *_args):
        if verify_error and obj.name == record["entity_ids"][0]:
            raise ValueError(verify_error)
        return {"inside_page_verified": True}

    monkeypatch.setattr(text_page_clip, "verify_clipped_ink", verify)
    warnings = []
    failures = engine._reverify_text_delivery_after_stack(
        [record], page_number=2, stack_offset_m=-0.5, warnings=warnings)
    return failures, warnings


def _clipped_record(*names):
    return {"page": 2, "item_id": "page:2:text:3", "status": "delivered",
            "final_representation": "3d_text", "entity_ids": list(names),
            "attempts": [{"status": "delivered", "evidence": {}}],
            "page_viewport_clips": [{"entity_id": name, "guide_entity_id": "Guide"}
                                    for name in names]}


def test_after_move_trim_recheck_keeps_the_item_as_a_warning(monkeypatch):
    record = _clipped_record("P2_c0", "P2_c1")
    failures, warnings = _reverify(monkeypatch, record)
    assert failures == []
    assert record["status"] == "delivered" and record["entity_ids"] == ["P2_c0", "P2_c1"]
    assert record["post_stack_recheck"]["status"] == "warning"
    assert record["final_state_verification"]["status"] == "warning"
    assert warnings == [{"item_id": "page:2:text:3", "page": 2,
                         "failures": record["post_stack_recheck"]["failures"]}]
    assert warnings[0]["failures"][0].startswith("final_entity_page_clip_unverified:P2_c0:")


def test_any_other_after_move_failure_still_removes_only_that_item(monkeypatch):
    record = _clipped_record("P2_c0")
    record["entity_ids"].append("P2_missing")
    failures, warnings = _reverify(monkeypatch, record)
    assert warnings == []
    assert [failure["item_id"] for failure in failures] == ["page:2:text:3"]
    assert record["status"] == "failed"


@pytest.mark.parametrize("failures,expected", [
    (["final_entity_page_clip_unverified:A:area"], True),
    (["final_entity_page_clip_unverified:PDF_Outline_page:2:text:3_0:area",
      "final_source_outline_unverified:PDF_Outline_page:2:text:3_0:unverified native modifier"], True),
    (["final_source_outline_unverified:A:unverified native modifier"], False),
    (["final_entity_page_clip_unverified:A:area",
      "final_source_outline_unverified:B:unverified native modifier"], False),
    (["final_entity_page_clip_unverified:A:area", "final_entity_location_mismatch:A"], False),
    (["missing_final_entity:A"], False),
])
def test_only_the_trim_recheck_is_warning_only(failures, expected):
    entity_ids = ["A", "B", "PDF_Outline_page:2:text:3_0"]
    assert engine._post_stack_failures_are_warning_only(failures, entity_ids) is expected
