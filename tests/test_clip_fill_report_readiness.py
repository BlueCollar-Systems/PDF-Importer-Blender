"""Missing clipped ink cannot be certified by a large vector object count."""
from copy import deepcopy

import pytest

from pdf_vector_importer.pdfcadcore.import_report import (
    ImportReport,
    build_human_summary,
    build_import_contract_ready,
    build_import_report,
    enrich_import_report_extras,
)


def tally(*, dropped=0, approximated=0, exact=0, invisible=0):
    issues = [
        {"severity": "warning", "dropped": True, "seqno": index}
        for index in range(min(dropped, 200))
    ]
    issues += [
        {"severity": "warning", "dropped": False, "seqno": 1000 + index}
        for index in range(min(approximated, max(0, 200 - len(issues))))
    ]
    return {
        "resolved_exactly": exact, "dropped_invisible": invisible,
        "dropped": dropped, "approximated": approximated,
        "by_action": {"rect-intersection": exact, "dropped-invisible": invisible,
                      "dropped-unsupported": dropped, "synthetic-approximate": approximated},
        "issues": issues,
        "issues_truncated": dropped + approximated > len(issues),
    }


def report_with(block, *, warnings=0, diagnostics=None):
    extra = {"clip_fill_delivery": deepcopy(block), "result_status": "success"}
    if diagnostics is not None:
        extra["diagnostics"] = deepcopy(diagnostics)
    return build_import_report(
        host_app="blender", importer_version="1.0.110", pdf_path="synthetic-clip.pdf",
        pages=1, primitive_count=100, import_text=False, warnings=warnings, extra=extra,
    )


@pytest.mark.parametrize("dropped,approximated,quality", [(1, 0, "incomplete"), (0, 1, "approximate"), (2, 3, "incomplete")])
def test_visible_missing_or_approximate_ink_stays_partial_and_not_ready(dropped, approximated, quality):
    report = report_with(tally(dropped=dropped, approximated=approximated), warnings=dropped + approximated)
    contract = report.extra["import_contract_ready"]
    assert contract["ready"] is False and contract["checks"]["clip_fill_delivery"] is False
    assert report.extra["result_status"] == "success"
    assert "terminal_failure" not in report.extra
    assert report.result["primitives"] == 100
    assert report.fallback == {"used": False, "reason": None}
    assert report.extra["diagnostics"]["quality_level"] == quality
    summary = report.extra["human_summary"]
    assert "Overall fidelity: high" not in summary
    assert "Vector extraction completed" not in summary
    assert "Editable geometry was retained" in summary
    if dropped:
        assert f"{dropped} clipped filled path" in summary and "left out" in summary
    if approximated:
        assert f"{approximated} clipped filled path" in summary and "approximated" in summary


def test_full_loss_count_survives_warning_issue_cap_and_cached_high_quality():
    report = report_with(tally(dropped=1338), warnings=1338, diagnostics={"quality_level": "high"})
    assert len(report.extra["clip_fill_delivery"]["issues"]) == 200
    assert report.extra["clip_fill_delivery"]["issues_truncated"] is True
    assert report.extra["import_contract_ready"]["ready"] is False
    assert report.extra["diagnostics"]["quality_level"] == "incomplete"
    assert "1338 clipped filled paths were left out" in report.extra["human_summary"]
    assert report.result["warnings"] == 1338


@pytest.mark.parametrize("exact,invisible", [(10000, 0), (0, 10000), (1309, 29)])
def test_exact_and_proved_invisible_info_tallies_are_ready(exact, invisible):
    report = report_with(tally(exact=exact, invisible=invisible))
    assert report.extra["import_contract_ready"]["ready"] is True
    assert report.extra["import_contract_ready"]["checks"]["clip_fill_delivery"] is True
    assert report.extra["diagnostics"]["quality_level"] == "high"
    assert "left out" not in report.extra["human_summary"]
    assert report.extra["clip_fill_delivery"]["issues"] == []


def test_unrelated_warning_does_not_mean_missing_ink():
    report = report_with(tally(), warnings=5)
    report.extra["housekeeping_warnings"] = {"temp_cleanup": "synthetic cleanup warning"}
    report.extra["text_glyph_codes"] = {"unproven": 4}
    enrich_import_report_extras(report)
    assert report.extra["import_contract_ready"]["ready"] is True
    assert report.extra["diagnostics"]["quality_level"] == "high"
    assert "warnings_present" in report.extra["diagnostics"]["signals"]


def test_older_optional_absent_tally_retains_contract_shape():
    report = build_import_report(host_app="blender", importer_version="1.0.110", pdf_path="synthetic.pdf",
                                 primitive_count=100, import_text=False, warnings=1)
    assert report.extra["import_contract_ready"]["ready"] is True
    assert "clip_fill_delivery" not in report.extra["import_contract_ready"]["checks"]
    assert report.extra["diagnostics"]["quality_level"] == "high"


@pytest.mark.parametrize("bad", [None, [], {}, "lost"])
def test_present_malformed_tally_cannot_certify_completeness(bad):
    report = report_with(bad)
    assert report.extra["import_contract_ready"]["ready"] is False
    assert report.extra["diagnostics"]["quality_level"] == "unverified"
    assert "drawing completeness is unverified" in report.extra["human_summary"]


@pytest.mark.parametrize("key,bad", [
    ("dropped", -1), ("dropped", True), ("dropped", 0.0), ("dropped", "0"),
    ("approximated", None), ("resolved_exactly", False), ("dropped_invisible", -1),
    ("issues", None), ("issues", {}), ("issues", [None]),
    ("issues", [{"severity": "warning", "dropped": "true"}]),
    ("issues", [{"severity": "warning", "dropped": True}]),
    ("issues_truncated", "false"), ("issues_truncated", True),
    ("by_action", []), ("by_action", {"cut": False}), ("by_action", {"cut": -1}),
    ("by_action", {"dropped-unsupported": 1}),
])
def test_malformed_counters_or_warning_ledger_are_not_silently_zero(key, bad):
    block = tally()
    block[key] = bad
    report = report_with(block)
    assert report.extra["import_contract_ready"]["ready"] is False
    assert report.extra["diagnostics"]["quality_level"] == "unverified"
    assert report.extra["result_status"] == "success"


def test_missing_warning_records_and_false_truncation_fail_closed():
    block = tally(dropped=3)
    block["issues"] = block["issues"][:1]
    report = report_with(block)
    assert report.extra["import_contract_ready"]["ready"] is False
    assert report.extra["diagnostics"]["quality_level"] == "unverified"


def test_canonical_recomputation_and_direct_summary_ignore_cached_success():
    report = report_with(tally(dropped=1))
    saved = report.to_dict()
    saved["extra"]["import_contract_ready"]["ready"] = True
    saved["extra"]["diagnostics"]["quality_level"] = "high"
    restored = ImportReport.from_dict(saved)
    assert build_import_contract_ready(restored)["ready"] is False
    assert "Overall fidelity: high" not in build_human_summary(restored)
    assert "1 clipped filled path was left out" in build_human_summary(restored)
    enrich_import_report_extras(restored)
    first = restored.to_dict()
    enrich_import_report_extras(restored)
    assert restored.to_dict() == first


@pytest.mark.parametrize("pages", [None, {}, "1", [True], [0], [-1], [1], [1, 1]])
def test_complete_zero_warning_tally_rejects_invalid_or_nonempty_optional_warning_pages(pages):
    block = tally(exact=1000, invisible=29)
    block["warning_pages"] = pages
    report = report_with(block)
    assert report.extra["import_contract_ready"]["ready"] is False
    assert report.extra["diagnostics"]["quality_level"] == "unverified"


def test_optional_warning_pages_agree_with_retained_warning_records():
    block = tally(dropped=1)
    block["issues"][0]["page"] = 1
    block["warning_pages"] = [1]
    assert report_with(block).extra["diagnostics"]["quality_level"] == "incomplete"
    block["warning_pages"] = [2]
    assert report_with(block).extra["diagnostics"]["quality_level"] == "unverified"
    block["warning_pages"] = []
    assert report_with(block).extra["diagnostics"]["quality_level"] == "unverified"
    block["warning_pages"] = [1]
    block["issues"][0]["page"] = []
    assert report_with(block).extra["diagnostics"]["quality_level"] == "unverified"


def test_capped_warning_pages_may_include_pages_beyond_retained_list():
    block = tally(dropped=205)
    for issue in block["issues"]:
        issue["page"] = 1
    block["warning_pages"] = [1, 2]
    report = report_with(block)
    assert report.extra["diagnostics"]["quality_level"] == "incomplete"
    block["warning_pages"] = [2]
    assert report_with(block).extra["diagnostics"]["quality_level"] == "unverified"


def test_optional_empty_warning_pages_do_not_change_complete_info_readiness():
    block = tally(exact=1309, invisible=29)
    block["warning_pages"] = []
    assert report_with(block).extra["import_contract_ready"]["ready"] is True


@pytest.mark.parametrize("block", [tally(dropped=1), tally(approximated=1), None])
def test_repaired_tally_refreshes_only_owned_clip_diagnostics(block):
    baseline = {
        "quality_level": "moderate", "signals": ["source_check", "housekeeping_warning"],
        "recommended_actions": ["Inspect source placement", "Check cleanup"],
        "unrelated_native_proof": {"unchanged": True},
    }
    report = report_with(block, diagnostics=baseline)
    report.extra["housekeeping_warnings"] = {"temp_cleanup": "keep me"}
    assert report.extra["diagnostics"]["quality_level"] in {"incomplete", "approximate", "unverified"}
    report.extra["clip_fill_delivery"] = tally(exact=1)
    enrich_import_report_extras(report)
    assert report.extra["import_contract_ready"]["ready"] is True
    assert report.extra["diagnostics"] == baseline
    assert report.extra["housekeeping_warnings"] == {"temp_cleanup": "keep me"}
    assert "Overall fidelity: moderate" in report.extra["human_summary"]
    assert "left out" not in report.extra["human_summary"]
    once = report.to_dict()
    enrich_import_report_extras(report)
    assert report.to_dict() == once


@pytest.mark.parametrize("quality", ["incomplete", "approximate", "unverified"])
def test_previous_owned_signal_without_saved_base_can_be_repaired(quality):
    report = report_with(tally(exact=1), diagnostics={
        "quality_level": quality,
        "signals": [f"clipped_fill_delivery_{quality}", "unrelated_check"],
        "recommended_actions": ["Keep this unrelated action"],
    })
    assert report.extra["import_contract_ready"]["ready"] is True
    assert report.extra["diagnostics"]["quality_level"] == "high"
    assert report.extra["diagnostics"]["signals"] == ["unrelated_check"]
    assert report.extra["diagnostics"]["recommended_actions"] == ["Keep this unrelated action"]


def test_independent_later_quality_change_is_not_erased_on_clip_repair():
    report = report_with(tally(dropped=1))
    report.extra["diagnostics"]["quality_level"] = "low"
    report.extra["diagnostics"]["signals"].append("independent_geometry_limit")
    report.extra["clip_fill_delivery"] = tally(exact=1)
    enrich_import_report_extras(report)
    assert report.extra["diagnostics"]["quality_level"] == "low"
    assert "independent_geometry_limit" in report.extra["diagnostics"]["signals"]
    assert not any(s.startswith("clipped_fill_delivery_") for s in report.extra["diagnostics"]["signals"])


@pytest.mark.parametrize("bad_quality", [[], {"malformed": True}])
@pytest.mark.parametrize("block,quality", [(tally(dropped=1), "incomplete"), (None, "unverified")])
def test_nonhashable_cached_quality_cannot_abort_partial_report(bad_quality, block, quality):
    report = report_with(block, diagnostics={
        "quality_level": bad_quality, "signals": ["clipped_fill_delivery_incomplete"],
    })
    assert report.extra["diagnostics"]["quality_level"] == quality
    assert report.extra["import_contract_ready"]["ready"] is False
    assert report.extra["result_status"] == "success" and report.result["primitives"] == 100
    enrich_import_report_extras(report)
    assert report.extra["diagnostics"]["quality_level"] == quality
