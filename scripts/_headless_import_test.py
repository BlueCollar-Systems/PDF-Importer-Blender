"""Headless Blender import check for pdf_vector_importer.

Run inside Blender:
    blender -b --factory-startup --python-exit-code 1 --python scripts/_headless_import_test.py

Environment (all optional except the PDF):
    TEST_PDF / BCS_PUBLIC_QA_PDF  PDF to import (no PDF -> prints SKIP, exits 0)
    TEST_PAGES                    page selection, default "1" ("all" for every sheet)
    TEST_TEXT_MODE                labels|text|3d_text|glyphs|geometry|raster, default "3d_text"
    TEST_EXPECT_PAGES             require exactly this many sheets imported, each with objects
    TEST_EXPECT_IMAGES            require at least this many pictures placed
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
import os
import re
import sys
import tempfile
import traceback

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


def _report_audit_mode(report_path):
    if not report_path or not os.path.isfile(report_path):
        return None
    try:
        with open(report_path, encoding="utf-8") as fh:
            report = json.load(fh)
    except Exception:  # noqa: BLE001 - diagnostics only
        return None
    for holder in (report, report.get("extra") or {}):
        for key in ("audit_mode", "audit_import"):
            if key in holder:
                return holder[key]
    return None


def _checks(summary, expect_pages, expect_images):
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
    summary["audit_mode"] = _report_audit_mode(stats.get("import_report_path") or report_path)

    problems = _checks(summary, _int_env("TEST_EXPECT_PAGES"), _int_env("TEST_EXPECT_IMAGES"))
    summary["problems"] = problems
    print("HEADLESS_IMPORT_RESULT", json.dumps(summary, default=str, sort_keys=True))
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
