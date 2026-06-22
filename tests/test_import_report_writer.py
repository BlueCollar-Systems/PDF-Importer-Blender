from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


if "bpy" not in sys.modules:
    sys.modules["bpy"] = types.SimpleNamespace(
        app=types.SimpleNamespace(version=(4, 1, 0)),
        types=types.SimpleNamespace(),
    )
if "bmesh" not in sys.modules:
    sys.modules["bmesh"] = types.SimpleNamespace()

from pdf_vector_importer.bl_import_engine import write_import_report  # noqa: E402
from pdf_vector_importer import bl_info  # noqa: E402


class TestImportReportWriter(unittest.TestCase):
    def test_write_import_report_records_raster_fallback_reason(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bl_import_report_") as tmp:
            report_path = Path(tmp) / "import_report.json"
            stats = {
                "pages_imported": 2,
                "primitives": 1,
                "text_items": 0,
                "collections": 1,
                "elapsed": 0.1,
            }
            with patch(
                "pdf_vector_importer.bl_import_engine._pymupdf_version",
                return_value="",
            ):
                write_import_report(
                    str(Path(tmp) / "sample.pdf"),
                    {},
                    stats,
                    import_mode="auto",
                    raster_pages=2,
                    output_path=str(report_path),
                )
            data = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(data["fallback"]["used"])
            self.assertEqual(data["fallback"]["reason"], "raster_fallback_2_pages")

    def test_write_import_report_uses_shared_schema(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bl_import_report_") as tmp:
            report_path = Path(tmp) / "import_report.json"
            stats = {
                "pages_imported": 2,
                "primitives": 9,
                "text_items": 3,
                "collections": 4,
                "elapsed": 0.25,
                "curves": 5,
                "meshes": 1,
                "images": 0,
            }

            with patch(
                "pdf_vector_importer.bl_import_engine._pymupdf_version",
                return_value="",
            ):
                result = write_import_report(
                    str(Path(tmp) / "sample.pdf"),
                    {"import_text": True, "text_mode": "glyphs"},
                    stats,
                    import_mode="vector",
                    output_path=str(report_path),
                )

            self.assertEqual(result, str(report_path))
            data = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], "bcs.import_report/1.1")
            self.assertEqual(data["host"]["app"], "blender")
            self.assertEqual(data["host"]["version"], "4.1.0")
            expected_version = ".".join(str(part) for part in bl_info["version"])
            self.assertEqual(data["importer"]["version"], expected_version)
            self.assertEqual(data["result"]["primitives"], 9)
            self.assertEqual(data["result"]["text_entities"], 3)
            self.assertEqual(data["result"]["layers"], 4)
            self.assertEqual(data["extra"]["curves"], 5)
            self.assertEqual(data["extra"]["import_text"], True)
            self.assertEqual(data["extra"]["text_mode"], "glyphs")


if __name__ == "__main__":
    unittest.main(verbosity=2)
