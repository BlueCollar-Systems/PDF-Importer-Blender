# Host Compatibility — PDF Vector Importer (Blender)

Modes are extraction **strategy** (Auto / Vector / Raster / Hybrid), not quality tiers.
Every mode uses consolidated maximum-fidelity parameters (BCS-ARCH-001).

**GUI:** Single **professional import** flow — **Auto** per page by default. Expand **Advanced Options** to override strategy. CLI/batch retain `--mode` for scripting.

## Blender

| Blender | Bundled Python | PyMuPDF | Status |
|---------|----------------|---------|--------|
| 4.5 LTS | 3.11 | >=1.24,<2.0 | ✅ Expected (vendored cp311 wheel) |
| **5.0–5.1** | **3.12–3.13** | >=1.24,<2.0 | ✅ **v1.0.42+** — cp310-abi3 vendored wheel **or** Preferences → **Install PyMuPDF** |
| 4.0–4.2 | 3.11 | >=1.24,<2.0 | ✅ Expected |
| 3.6 LTS | 3.10 | >=1.24,<2.0 | ✅ Expected |
| 3.0–3.5 | 3.10 | >=1.24,<2.0 | ⚠️ Expected after manual install |
| 2.83–2.93 | 3.9 | legacy pin | ⚠️ Expected after legacy testing |
| 2.79 and earlier | | | ❌ Not supported |

`bl_info["blender"]` declares **(3, 0, 0)** minimum in both add-on entrypoints (`pdf_vector_importer` and `blender_pdf_vector_importer`). Blender **3.6 LTS** is the recommended baseline for bundled-Python/PyMuPDF support; 3.0–3.5 are **Expected** after manual install of dependencies.

### Blender 5.x PyMuPDF bootstrap (v1.0.42+)

Blender **5.0–5.1** ships Python **3.12+**. Older cp311-only vendored wheels fail to import. The add-on handles this in three layers:

1. **Vendored cp310-abi3 wheel** under `pdf_vector_importer/lib/` (works on Python 3.10–3.13).
2. **Self-heal** — `dependency_manager.repair_vendored_pymupdf()` restores missing `pymupdf/extra.py` from `_vendored_pymupdf_extra.py`.
3. **Preferences → Install PyMuPDF** — pip installs into `pdf_vector_importer/lib/` using Blender's bundled Python (`dependency_manager.install_pymupdf()`).

Paths (relative to add-on root `pdf_vector_importer/`):

| Path | Purpose |
|------|---------|
| `lib/pymupdf/` | Vendored or pip-installed PyMuPDF |
| `lib/fitz/` | Legacy fitz shim (if present) |
| `_vendored_pymupdf_extra.py` | Backup pure-Python helper for partial installs |

Run diagnostics from a terminal:

```powershell
blender --background --python-expr "import addon_utils; addon_utils.enable('pdf_vector_importer'); from pdf_vector_importer.dependency_manager import print_diagnostics; print_diagnostics()"
```

Or use `python preflight_check.py` from the add-on repo root (no Blender GUI required).

### Text rendering (3D-capable host)

| Option | Blender result |
|--------|----------------|
| **Labels** | Font curve objects (editable text) |
| **3D Text** | Extruded / geometric text where supported |
| **Glyphs** | Per-character vector curves |
| **Geometry** | Text as mesh/curve outlines |

## CI coverage

GitHub Actions: Python **3.9, 3.10, 3.11, 3.12**, `pdfcadcore_sync_check.py`, pytest, BCS-ARCH mode smoke on synthetic PDFs.
