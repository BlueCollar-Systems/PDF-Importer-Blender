# -*- coding: utf-8 -*-
# import_outcome.py — Plain-English end-of-import lines
# Copyright (c) 2024-2026 BlueCollar Systems — BUILT. NOT BOUGHT.
# License: MIT
"""Say what an import brought in, sheet by sheet, in words a shop worker reads.

Pure Python (no bpy) so the wording is testable outside Blender. The lines
never show report keys, counters or file names; the last line points at the
folder that holds the full report.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List

_ITEM_PAGE = re.compile(r"^page:(\d+):")


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _rows(stats: Dict, key: str) -> List[Dict]:
    return [row for row in list(stats.get(key) or ()) if isinstance(row, dict)]


def _item_page(item_id: Any) -> int:
    match = _ITEM_PAGE.match(str(item_id or ""))
    return int(match.group(1)) if match else 0


def _add(counts: Dict[int, int], page: int, amount: int = 1) -> None:
    counts[page] = counts.get(page, 0) + amount


def _tally(stats: Dict) -> Dict[str, Dict[int, int]]:
    """Per-sheet counts of each kind of problem."""
    tally: Dict[str, Dict[int, int]] = {
        "text": {}, "pictures": {}, "shapes": {}, "edge": {}, "edge_page": {}, "recheck": {},
    }
    for item_id in list(stats.get("text_delivery_failed_item_ids") or ()):
        _add(tally["text"], _item_page(item_id))
    for row in _rows(stats, "raster_delivery_failures"):
        _add(tally["pictures"], _int(row.get("page")))
    for row in _rows(stats, "geometry_delivery_issues"):
        if str(row.get("status") or "").strip().lower() not in {"verified", "skipped"}:
            _add(tally["shapes"], _int(row.get("page")))
    for row in _rows(stats, "text_page_edge_warnings"):
        page = _int(row.get("page"))
        items = [item for item in list(row.get("skipped_items") or ()) if isinstance(item, dict)]
        if items:
            _add(tally["edge"], page, len(items))
        else:
            tally["edge_page"][page] = 1
    for row in _rows(stats, "text_final_state_warnings"):
        _add(tally["recheck"], _int(row.get("page")) or _item_page(row.get("item_id")))
    return tally


def _where(page: int) -> str:
    return f" on sheet {page}" if page > 0 else ""


def _problem_lines(tally: Dict[str, Dict[int, int]]) -> List[str]:
    """Items that could not be brought in at all, per sheet."""
    lines: List[str] = []
    pages = set(tally["text"]) | set(tally["pictures"]) | set(tally["shapes"])
    for page in sorted(pages):
        count = tally["text"].get(page, 0)
        if count:
            lines.append(f"{count} text {_plural(count, 'item', 'items')}{_where(page)} "
                         f"could not be brought in.")
        count = tally["pictures"].get(page, 0)
        if count:
            lines.append(f"{count} {_plural(count, 'picture', 'pictures')}{_where(page)} "
                         f"could not be placed.")
        count = tally["shapes"].get(page, 0)
        if count:
            lines.append(f"{count} {_plural(count, 'line or shape', 'lines or shapes')}"
                         f"{_where(page)} could not be drawn.")
    return lines


def _step_down_lines(tally: Dict[str, Dict[int, int]]) -> List[str]:
    """Items that were kept in a lesser form (left untrimmed, not re-checked)."""
    lines: List[str] = []
    pages = set(tally["edge"]) | set(tally["edge_page"]) | set(tally["recheck"])
    for page in sorted(pages):
        count = tally["edge"].get(page, 0)
        if count:
            lines.append(f"{count} {_plural(count, 'letter', 'letters')}{_where(page)} "
                         f"{_plural(count, 'was', 'were')} left untrimmed at the sheet edge.")
        elif page in tally["edge_page"]:
            lines.append(f"Text at the edge of sheet {page} was left untrimmed.")
        count = tally["recheck"].get(page, 0)
        if count:
            lines.append(f"{count} text {_plural(count, 'item', 'items')}{_where(page)} "
                         f"could not be re-checked after the sheet was moved into place; "
                         f"{_plural(count, 'it was', 'they were')} kept.")
    return lines


def step_down_lines(stats: Dict) -> List[str]:
    return _step_down_lines(_tally(stats if isinstance(stats, dict) else {}))


def details_line(stats: Dict) -> str:
    report_path = str((stats or {}).get("import_report_path") or "").strip()
    folder = os.path.dirname(report_path) if report_path else ""
    return f"Details: {folder}" if folder else ""


def plain_outcome_lines(stats: Dict) -> List[str]:
    """Headline, one line per sheet problem, then where the details are."""
    stats = stats if isinstance(stats, dict) else {}
    tally = _tally(stats)
    requested = _int(stats.get("pages_requested")) or _int(stats.get("pages"))
    imported = _int(stats.get("pages_imported"))
    if requested:
        headline = f"Imported {imported} of {requested} {_plural(requested, 'sheet', 'sheets')}"
    else:
        headline = f"Imported {imported} {_plural(imported, 'sheet', 'sheets')}"
    needs_look = sum(sum(counts.values()) for counts in tally.values())
    if needs_look:
        headline += (f"; {needs_look} {_plural(needs_look, 'item needs', 'items need')} a look")
    lines = [headline + "."]
    lines.extend(_problem_lines(tally))
    lines.extend(_step_down_lines(tally))
    details = details_line(stats)
    if details:
        lines.append(details)
    return lines


def human_summary_note(stats: Dict) -> str:
    """Sentences for the report's human_summary about sheets kept in a lesser form."""
    tally = _tally(stats if isinstance(stats, dict) else {})
    pictures = {"text": {}, "pictures": tally["pictures"], "shapes": {}}
    return " ".join(_step_down_lines(tally) + _problem_lines(pictures))
