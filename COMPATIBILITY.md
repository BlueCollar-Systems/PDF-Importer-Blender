# Host Compatibility — PDF Vector Importer (Blender)

Modes are extraction **strategy** (Auto / Vector / Raster / Hybrid), not quality tiers.
Every mode uses consolidated maximum-fidelity parameters (BCS-ARCH-001).

**GUI:** Single **professional import** flow — **Auto** per page by default. Expand **Advanced Options** to override strategy. CLI/batch retain `--mode` for scripting.

## Blender

| Blender | Bundled Python | PyMuPDF | Status |
|---------|----------------|---------|--------|
| 4.5 LTS | 3.11 | >=1.24,<2.0 | ⚠️ Expected |
| **5.0–5.1** | **3.12+** | >=1.24,<2.0 | ⚠️ **Re-vendor or Preferences → Install PyMuPDF** (shipped cp311 wheels fail on cp312) |
| 4.0–4.2 | 3.11 | >=1.24,<2.0 | ⚠️ Expected |
| 3.6 LTS | 3.10 | >=1.24,<2.0 | ⚠️ Expected |
| 3.0–3.5 | 3.10 | >=1.24,<2.0 | ⚠️ Expected |
| 2.83–2.93 | 3.9 | legacy pin | ⚠️ Expected after legacy testing |
| 2.79 and earlier | | | ❌ Not supported |

`bl_info["blender"]` declares **(3, 0, 0)** minimum in both add-on entrypoints (`pdf_vector_importer` and `blender_pdf_vector_importer`). Blender **3.6 LTS** is the recommended baseline for bundled-Python/PyMuPDF support; 3.0–3.5 are **Expected** after manual install of dependencies.

### Text rendering (3D-capable host)

| Option | Blender result |
|--------|----------------|
| **Labels** | Font curve objects (editable text) |
| **3D Text** | Extruded / geometric text where supported |
| **Glyphs** | Per-character vector curves |
| **Geometry** | Text as mesh/curve outlines |

## CI coverage

GitHub Actions: Python **3.9, 3.10, 3.11, 3.12**, `pdfcadcore_sync_check.py`, pytest, BCS-ARCH mode smoke on synthetic PDFs.
