"""Headless Blender import check for pdf_vector_importer.

Run inside Blender:
    blender -b --factory-startup --python-exit-code 1 --python scripts/_headless_import_test.py

Environment (all optional except the PDF):
    TEST_PDF / BCS_PUBLIC_QA_PDF  PDF to import (no PDF -> prints SKIP, exits 0)
    TEST_PAGES                    page selection, default "1" ("all" for every sheet)
    TEST_TEXT_MODE                labels|text|3d_text|glyphs|geometry|raster, default "3d_text"
    TEST_EXPECT_PAGES             require exactly this many sheets imported, each with objects
    TEST_EXPECT_IMAGES            require at least this many pictures placed
    TEST_EXPECT_TEXT_REPRESENTATION require every text item to use this mode;
                                  3d_text also requires native extruded font
                                  entities with the verified packed font
    TEST_EXPECT_FAIL              a reason (e.g. a fix-list id): the run is a known
                                  failure. Failing checks print XFAIL and exit 0; if
                                  every check passes it prints XPASS (time to remove
                                  the marker) and also exits 0.
    TEST_ADDON_SOURCE             "repo" (default): load the add-on from this checkout,
                                  never from an installed copy. "installed": enable the
                                  add-on Blender finds in its scripts/addons folders
                                  (CI installs the built release ZIP there).
    TEST_RESULT_JSON              also write the summary to this JSON file

Prints one "HEADLESS_IMPORT_RESULT {...}" line and "HEADLESS_IMPORT_PASS" /
"HEADLESS_IMPORT_FAIL" / "HEADLESS_IMPORT_XFAIL" / "HEADLESS_IMPORT_XPASS".
"""
import json
import hashlib
import math
import os
import re
import sys
import tempfile
import traceback

sys.dont_write_bytecode = True

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_SOURCE = (os.environ.get("TEST_ADDON_SOURCE") or "repo").strip().lower()
if ADDON_SOURCE == "repo":
    # Prefer the repo add-on over any user-installed copy (API drift breaks headless QA).
    for p in (REPO, os.path.join(REPO, "pdf_vector_importer")):
        if p not in sys.path:
            sys.path.insert(0, p)

PDF = os.environ.get("TEST_PDF") or os.environ.get("BCS_PUBLIC_QA_PDF") or ""
PAGES = (os.environ.get("TEST_PAGES") or "1").strip()
TEXT_MODE = (os.environ.get("TEST_TEXT_MODE") or "3d_text").strip()
EXPECT_FAIL = (os.environ.get("TEST_EXPECT_FAIL") or "").strip()


def _int_env(name):
    raw = (os.environ.get(name) or "").strip()
    return int(raw) if raw else None


def _load_addon():
    """Import and register the add-on; return the package module."""
    import importlib

    if ADDON_SOURCE == "installed":
        import addon_utils

        mod = addon_utils.enable("pdf_vector_importer", default_set=True, handle_error=None)
        if mod is None:
            raise RuntimeError("installed add-on 'pdf_vector_importer' could not be enabled")
        addon_file = os.path.abspath(mod.__file__)
        if addon_file.startswith(os.path.abspath(REPO) + os.sep):
            raise RuntimeError(f"expected the installed add-on, got the checkout copy: {addon_file}")
        return mod

    if ADDON_SOURCE != "repo":
        raise RuntimeError(f"TEST_ADDON_SOURCE must be 'repo' or 'installed', not {ADDON_SOURCE!r}")
    # Drop stale installed addon modules so repo code wins.
    for name in list(sys.modules):
        if name == "pdf_vector_importer" or name.startswith("pdf_vector_importer."):
            del sys.modules[name]
    mod = importlib.import_module("pdf_vector_importer")
    mod.register()
    return mod


def _page_object_counts(bpy):
    counts = {}
    for coll in bpy.data.collections:
        match = re.match(r"^PDF_Page_(\d+)(?:\.\d+)?$", coll.name)
        if match:
            page = int(match.group(1))
            counts[page] = counts.get(page, 0) + len(coll.all_objects)
    return dict(sorted(counts.items()))


def _read_report(report_path):
    if not report_path or not os.path.isfile(report_path):
        return {}
    try:
        with open(report_path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001 - diagnostics only
        return {}


def _report_audit_mode(report):
    for holder in (report, report.get("extra") or {}):
        for key in ("audit_mode", "audit_import"):
            if key in holder:
                return holder[key]
    return None


def _report_text_failures(report, limit=6):
    """Why text items failed, from extra.text_delivery in the import report."""
    delivery = (report.get("extra") or {}).get("text_delivery") or {}
    reasons = []
    for item in delivery.get("items") or []:
        if item.get("status") != "failed":
            continue
        parts = [str(item.get("requested_representation") or ""), str(item.get("reason") or "")]
        viewport = item.get("page_viewport_failure") or {}
        if viewport:
            parts.append(f"{viewport.get('stage')}: {viewport.get('reason')}")
        for attempt in item.get("attempts") or []:
            if attempt.get("status") in ("verified", "delivered"):
                continue
            evidence = attempt.get("evidence") or {}
            parts += [
                str(attempt.get("reason") or ""),
                str(evidence.get("reason") or ""),
                str(evidence.get("error_type") or ""),
                str(evidence.get("detail") or "")[:160],
            ]
        reasons.append(f"{item.get('item_id')}: " + " / ".join(p for p in parts if p))
        if len(reasons) >= limit:
            break
    return reasons


def _native_text_delivery(report, bpy):
    delivery = (report.get("extra") or {}).get("text_delivery") or {}
    rows = []
    for item in delivery.get("items") or []:
        entities = []
        for name in item.get("entity_ids") or []:
            obj = bpy.data.objects.get(name)
            packed_sha = expected_sha = ""
            extrude = 0.0
            if obj is not None and obj.type == "FONT":
                extrude = float(obj.data.extrude)
                packed = obj.data.font.packed_file
                packed_sha = hashlib.sha256(bytes(packed.data)).hexdigest() if packed is not None else ""
                expected_sha = str(obj.get("pdf_exact_font_sha256") or "")
            entities.append({"name": name, "type": obj.type if obj is not None else None,
                "extrude": extrude, "packed_font_sha_verified": bool(packed_sha and packed_sha == expected_sha)})
        rows.append({"status": item.get("status"), "final_representation": item.get("final_representation"),
                     "native_entities": entities})
    return {"summary": delivery.get("summary") or {}, "items": rows}


def _checks(summary, expect_pages, expect_images, expect_text_representation=None):
    problems = []
    if summary["outcome"] != "returned":
        problems.append(f"import raised {summary.get('exception_type')}: {summary.get('exception')}")
    stats = summary["stats"]
    if expect_pages is not None:
        if stats.get("pages_imported") != expect_pages:
            problems.append(f"pages_imported={stats.get('pages_imported')} (expected {expect_pages})")
        per_page = summary["objects_per_page"]
        for page in range(1, expect_pages + 1):
            if per_page.get(page, 0) <= 0:
                problems.append(f"sheet {page} has no objects in the scene")
    if sum(summary["objects_per_page"].values()) <= 0:
        problems.append("no objects were created on any sheet")
    if not (stats.get("primitives", 0) > 0 or stats.get("images", 0) > 0):
        problems.append("no primitives or pictures were imported")
    if expect_images is not None and (stats.get("images") or 0) < expect_images:
        problems.append(f"images={stats.get('images')} (expected at least {expect_images})")
    if expect_pages is not None:
        if (stats.get("text_items") or 0) <= 0:
            problems.append("no text items were imported")
        if int(stats.get("text_delivery_failed_items") or 0) > 0:
            problems.append(f"text_delivery_failed_items={stats.get('text_delivery_failed_items')}")
    if expect_text_representation:
        delivery = summary.get("native_text_delivery") or {}
        ledger = delivery.get("summary") or {}
        items = delivery.get("items") or []
        source_count = int(ledger.get("source_items") or 0)
        if (source_count <= 0 or len(items) != source_count
                or ledger.get("delivered_items") != source_count
                or (ledger.get("final_counts") or {}) != {expect_text_representation: source_count}
                or ledger.get("fallback_items") != 0 or ledger.get("failed_items") != 0):
            problems.append(f"text delivery is not entirely requested {expect_text_representation} without fallback")
        for item in items:
            entities = item.get("native_entities") or []
            if item.get("status") != "delivered" or item.get("final_representation") != expect_text_representation or not entities:
                problems.append("requested text has no complete native delivery")
            if expect_text_representation == "3d_text" and any(
                row.get("type") != "FONT" or not math.isfinite(row.get("extrude", 0))
                or row.get("extrude", 0) <= 0 or row.get("packed_font_sha_verified") is not True
                for row in entities
            ):
                problems.append("requested 3D text lacks native extrusion or verified packed font")
    return problems


def main():
    if not os.path.isfile(PDF):
        print("SKIP: set TEST_PDF or BCS_PUBLIC_QA_PDF to run headless import smoke test")
        return 0

    import collections
    import importlib

    import bpy

    addon = _load_addon()
    bl_import_engine = importlib.import_module("pdf_vector_importer.bl_import_engine")
    print("pdf_vector_importer from:", bl_import_engine.__file__)

    report_dir = os.environ.get("BC_PDF_TEMP_DIR") or tempfile.gettempdir()
    os.makedirs(report_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(PDF))[0]
    report_path = os.path.join(report_dir, f"{stem}_{TEXT_MODE}_import_report.json")
    config = {
        "mode": "auto",
        "pages": PAGES,
        "text_mode": TEXT_MODE,
        "auto_focus_view": False,
        "import_report_path": report_path,
    }

    summary = {
        "blender": bpy.app.version_string,
        "python": sys.version.split()[0],
        "addon_source": ADDON_SOURCE,
        "addon_file": os.path.abspath(addon.__file__),
        "pdf": os.path.basename(PDF),
        "pages": PAGES,
        "text_mode": TEXT_MODE,
    }
    try:
        stats = bl_import_engine.import_pdf(PDF, config=config, context=None)
        summary["outcome"] = "returned"
    except Exception as exc:  # noqa: BLE001 - the check reports every failure
        stats = dict(getattr(exc, "stats", None) or {})
        summary["outcome"] = "raised"
        summary["exception_type"] = type(exc).__name__
        summary["exception"] = str(exc)[:600]
        traceback.print_exc()

    keep = (
        "pages_imported", "primitives", "text_items", "images", "curves", "meshes",
        "text_delivery_delivered_items", "text_delivery_fallback_items",
        "text_delivery_failed_items", "import_report_path",
    )
    summary["stats"] = {key: stats.get(key) for key in keep if key in stats}
    summary["object_types"] = dict(collections.Counter(obj.type for obj in bpy.data.objects))
    summary["object_total"] = len(bpy.data.objects)
    summary["objects_per_page"] = _page_object_counts(bpy)
    report = _read_report(stats.get("import_report_path") or report_path)
    summary["audit_mode"] = _report_audit_mode(report)
    summary["text_failure_reasons"] = _report_text_failures(report)
    expected_text = (os.environ.get("TEST_EXPECT_TEXT_REPRESENTATION") or "").strip()
    if expected_text:
        summary["native_text_delivery"] = _native_text_delivery(report, bpy)

    problems = _checks(summary, _int_env("TEST_EXPECT_PAGES"), _int_env("TEST_EXPECT_IMAGES"), expected_text)
    summary["problems"] = problems
    print("HEADLESS_IMPORT_RESULT", json.dumps(summary, default=str, sort_keys=True))
    for reason in summary["text_failure_reasons"]:
        print("HEADLESS_IMPORT_TEXT_FAILURE", reason)
    result_json = os.environ.get("TEST_RESULT_JSON")
    if result_json:
        with open(result_json, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=1, default=str, sort_keys=True)

    label = f"Blender {bpy.app.version_string}, text mode {TEXT_MODE}, {os.path.basename(PDF)}"
    if EXPECT_FAIL:
        if problems:
            print(f"HEADLESS_IMPORT_XFAIL ({label}) known failure [{EXPECT_FAIL}]: " + "; ".join(problems))
        else:
            print(
                f"::warning::{label} now passes; the known-failure marker [{EXPECT_FAIL}] can be removed"
            )
            print(f"HEADLESS_IMPORT_XPASS ({label}) [{EXPECT_FAIL}]")
        return 0
    if problems:
        print(f"HEADLESS_IMPORT_FAIL ({label}): " + "; ".join(problems))
        return 1
    print(f"HEADLESS_IMPORT_PASS ({label}) pages_imported {summary['stats'].get('pages_imported')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
