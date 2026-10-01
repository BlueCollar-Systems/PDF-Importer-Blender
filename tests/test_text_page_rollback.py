"""A failed native page proof cannot become a completed or resumable page."""
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


def setup_page(monkeypatch, *, retained=False, unavailable=False):
    owned = types.SimpleNamespace(name="page2_text")
    page = types.SimpleNamespace(name="page2", all_objects=[owned])
    objects = {owned.name: owned, "peer": object()}
    collections = {page.name: page, "page1": object()}

    def discard(collection):
        assert collection is page
        if not retained:
            objects.pop(owned.name)
            collections.pop(page.name)

    monkeypatch.setattr(engine, "_discard_page_collection", discard)
    registry = types.SimpleNamespace(get=None) if unavailable else objects
    monkeypatch.setattr(engine.bpy, "data", types.SimpleNamespace(
        objects=registry, collections=collections), raising=False)
    records = [{"page": number, "item_id": f"p{number}:s0", "status": "delivered",
                "requested_representation": "text", "final_representation": "text",
                "entity_ids": [f"page{number}_text"]} for number in (1, 2)]
    provenance = types.SimpleNamespace(_text_delivery_records=records)

    def fail(*_args, **_kwargs):
        raise ValueError("Native visible ink lost")

    monkeypatch.setattr(text_page_clip, "clip_delivered_page_text", fail)
    stats = {"text_source_spans": 2, "text_items": 1, "pages_imported": 1}
    assert engine._clip_text_page_guarded(
        page, provenance, stats, page_number=2, width_mm=100, height_mm=80) is False
    return stats, provenance, objects, collections


def test_native_clip_failure_rolls_back_owned_page_and_persists_failed_request(monkeypatch, tmp_path):
    stats, provenance, objects, collections = setup_page(monkeypatch)
    failure = stats["text_page_viewport_failures"][0]
    assert failure["rollback"] == "verified"
    assert "page2_text" not in objects and "page2" not in collections
    assert "peer" in objects and "page1" in collections
    assert provenance._text_delivery_records[0]["status"] == "delivered"
    assert provenance._text_delivery_records[1]["status"] == "failed"
    assert provenance._text_delivery_records[1]["entity_ids"] == []
    config = {"import_text": True, "text_mode": "text"}
    assert any("source page text" in reason for reason in
               engine._terminal_import_failures(config, stats, provenance))
    report = tmp_path / "report.json"
    engine.write_import_report("source.pdf", config, stats, import_mode="vector",
                               output_path=str(report), provenance_opts=provenance)
    extra = json.loads(report.read_text())["extra"]
    assert extra["result_status"] == "incomplete"
    assert extra["import_contract_ready"]["ready"] is False
    assert extra["terminal_failure"]["text_page_viewport"][0]["page"] == 2
    assert extra["text_delivery"]["summary"]["failed_items"] == 1


@pytest.mark.parametrize("retained,unavailable,rollback", [
    (True, False, "failed"), (False, True, "unverified")])
def test_failed_or_unverifiable_rollback_blocks_resume(monkeypatch, retained, unavailable, rollback):
    stats, _provenance, _objects, _collections = setup_page(
        monkeypatch, retained=retained, unavailable=unavailable)
    assert stats["text_page_viewport_failures"][0]["rollback"] == rollback
    root = {}
    monkeypatch.setattr(engine, "_write_resume_checkpoint_guarded",
                        lambda *_args: pytest.fail("Unsafe checkpoint must not be written"))
    engine._checkpoint_failed_text_page(stats, root, "checkpoint", {"remaining_pages": [2, 3]})
    assert root["pdf_import_resume_blocked"]
    assert stats["text_page_viewport_failures"][0]["remaining_requested_pages"] == [2, 3]


def test_verified_rollback_retries_only_previous_completed_state(monkeypatch):
    stats, _provenance, _objects, _collections = setup_page(monkeypatch)
    safe_state = {"completed_pages": [1], "remaining_pages": [2, 3],
                  "aggregate_stats": {"pages_imported": 1, "text_source_spans": 1},
                  "next_stack_offset_m": -.1}
    writes = []
    monkeypatch.setattr(engine, "_write_resume_checkpoint_guarded",
                        lambda *args: writes.append(args))
    engine._checkpoint_failed_text_page(stats, {}, "checkpoint", safe_state)
    assert writes[0][1] == safe_state
    assert stats["resume"] is safe_state
    assert "text_page_viewport_failures" not in safe_state["aggregate_stats"]
    assert safe_state["aggregate_stats"]["text_source_spans"] == 1
