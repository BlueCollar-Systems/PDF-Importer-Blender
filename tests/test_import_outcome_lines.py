"""End-of-import lines are plain English, per sheet, with no report jargon."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "import_outcome_under_test", Path(__file__).parents[1] / "pdf_vector_importer/import_outcome.py")
outcome = importlib.util.module_from_spec(spec)
spec.loader.exec_module(outcome)

_JARGON = ("required=", "zero_ink", "extra.", ".json")


def _assert_plain(lines):
    for line in lines:
        for word in _JARGON:
            assert word not in line, (word, line)


def test_twelve_sheet_packet_with_one_problem_on_sheet_11():
    report = str(Path("C:/Temp/bcs-blender-import-d042/D042_import_report.json"))
    lines = outcome.plain_outcome_lines({
        "pages_requested": 12, "pages_imported": 12,
        "text_delivery_failed_item_ids": ["page:11:text:1"],
        "text_page_edge_warnings": [{"page": 11}],
        "import_report_path": report,
    })
    text = " ".join(lines)
    assert "12 of 12" in text and "sheet 11" in text
    assert lines[0] == "Imported 12 of 12 sheets; 2 items need a look."
    assert "1 text item on sheet 11 could not be brought in." in lines
    assert "Text at the edge of sheet 11 was left untrimmed." in lines
    assert lines[-1] == "Details: " + str(Path(report).parent)
    _assert_plain(lines)


def test_every_kind_of_problem_names_its_sheet():
    lines = outcome.plain_outcome_lines({
        "pages_requested": 4, "pages_imported": 4,
        "text_page_edge_warnings": [{"page": 2, "skipped_items": [{"entity_id": "a"}]}],
        "text_final_state_warnings": [{"item_id": "page:3:text:7", "page": 3}],
        "raster_delivery_failures": [{"page": 4, "stage": "embedded_image", "reason": "x.json"}],
        "geometry_delivery_issues": [{"page": 1, "status": "failed"}, {"page": 1, "status": "verified"}],
    })
    assert lines == [
        "Imported 4 of 4 sheets; 4 items need a look.",
        "1 line or shape on sheet 1 could not be drawn.",
        "1 picture on sheet 4 could not be placed.",
        "1 letter on sheet 2 was left untrimmed at the sheet edge.",
        "1 text item on sheet 3 could not be re-checked after the sheet was moved into place; "
        "it was kept.",
    ]
    _assert_plain(lines)


def test_clean_import_is_one_line():
    assert outcome.plain_outcome_lines({"pages_requested": 1, "pages_imported": 1}) == [
        "Imported 1 of 1 sheet."]


@pytest.mark.parametrize("stats", [{}, None, {"pages_imported": "x"}])
def test_damaged_stats_still_give_a_headline(stats):
    lines = outcome.plain_outcome_lines(stats)
    assert lines and lines[0].startswith("Imported 0 ")


def test_human_summary_note_lists_kept_items_and_pictures_only():
    note = outcome.human_summary_note({
        "text_delivery_failed_item_ids": ["page:1:text:1"],
        "text_page_edge_warnings": [{"page": 2, "skipped_items": [{}, {}]}],
        "raster_delivery_failures": [{"page": 4}],
    })
    assert note == ("2 letters on sheet 2 were left untrimmed at the sheet edge. "
                    "1 picture on sheet 4 could not be placed.")
