# Compatibility — PDF Vector Importer (Blender)

**Canonical path:** `C:\1PDF-Importer-Blender`  
Modes are extraction **strategy** (Auto / Vector / Raster / Hybrid), not quality tiers.

---

## Operating system

**Windows 64-bit (x64) only for now.** The release ZIP bundles PyMuPDF as the
official `cp310-abi3-win_amd64` wheel: Windows x64 `.pyd`/`.dll` files only.

| Operating system | Status |
|------------------|--------|
| Windows 10/11, 64-bit (x64) | ✅ Supported |
| Windows on ARM (ARM64 Blender) | ❌ Not supported (the bundled PDF reader is x64 only) |
| macOS (Intel or Apple silicon) | ❌ Not supported |
| Linux | ❌ Not supported |

On an unsupported system the import error and the add-on's Preferences panel
say "works only on Windows 64-bit (x64) PCs" and that reinstalling will not
help, instead of asking the user to reinstall. The add-on list shows the
warning "Windows 64-bit only" before the add-on is enabled.

## Minimum host version

**Blender 3.1** (`bl_info["blender"]` minimum). **Recommended: Blender 5.2 LTS**
(4.5 LTS also passes the automatic check). On 3.6 LTS and 4.2 LTS, Text and
3D Text work but the Glyphs text mode does not yet.

Blender 3.0 is **not supported**: it bundles CPython 3.9.7, and the release ZIP
ships a cp310-abi3 PyMuPDF wheel (`Requires-Python: >=3.10`) with no cp39
fallback. Raising the floor to 3.1 matches the packaged ABI (Blender 3.1+
bundles Python 3.10+).

## Oldest tested

| Host | Status |
|------|--------|
| Blender 5.2 LTS | ✅ Requested-representation host acceptance (2026-07-16); automatic check on 5.2.2 passes Text, 3D Text, Glyphs (2026-10-10) |
| Blender 5.0–5.1 | ✅ Smoke-tested at v1.0.42 (cp310-abi3 wheel); not re-tested since |
| Blender 4.5 LTS | ✅ Automatic check on 4.5.14 passes Text, 3D Text, Glyphs (2026-10-10) |
| Blender 4.0–4.2 | ⚠️ 4.2.23: Text and 3D Text pass; **Glyphs fails** (no text delivered). 4.0–4.1 not tested |
| Blender 3.6 LTS | ⚠️ 3.6.23: Text and 3D Text pass; **Glyphs fails** (no text delivered) |
| Blender 3.1–3.5 | ⚠️ 3.1.2: Text and 3D Text pass; **Glyphs crashes Blender**. 3.2–3.5 not tested |
| Blender 3.0.x | ❌ Not supported (Python 3.9; wheel ABI floor) |
| Blender 2.83–2.93 | ❌ Not supported |
| Blender 2.79 and earlier | ❌ Not supported |

"Automatic check" = the `blender-host-matrix` GitHub check (weekly, on demand,
and on pull requests that change the add-on). It installs the release ZIP built
from the commit into portable Blender 3.1.2, 3.6.23, 4.2.23, 4.5.14 and 5.2.2,
runs headless (`blender -b --factory-startup`), confirms every import option
registers, and imports a made-up two-sheet PDF (Letter + Tabloid, one picture,
non-embedded Helvetica, one text line crossing the sheet edge) as Text, 3D Text
and Glyphs, requiring both sheets with no failed text. Results above are from
the 2026-10-10 runs. What was seen:

- Glyphs on 3.6.23 and 4.2.23: every text item fails its check because the
  converted outline comes back as a `FONT` object instead of a `CURVE`
  (`actual_object_type_not_CURVE`); the import is reported incomplete.
- Glyphs on 3.1.2: Blender itself stops with an access violation.
- All five versions: a sheet with edge-crossing text that ends up several
  metres down the stack is rolled back (known defect, fix-list item
  BL-1010-keep-every-sheet). The check runs this case as a known failure.
- Drag-and-drop import registers on 4.2.23, 4.5.14 and 5.2.2 and is
  correctly absent on 3.1.2 and 3.6.23 (Blender added it in 4.1).

Geometry, Labels and Raster text modes, the GUI, and real customer drawings
are not part of the automatic check.

## Ruby / Python ABI

| Runtime | Notes |
|---------|-------|
| **Blender bundled Python** | 3.10 (3.1–3.6) through 3.13 (5.x) |
| cp310-abi3 PyMuPDF wheel | v1.0.42+; requires host Python >=3.10 |
| Ruby | Not used |

## Bundled dependencies

| Dependency | Release ZIP | Fallback |
|------------|-------------|----------|
| PyMuPDF (>=1.24, &lt;2.0) | ✅ Vendored under `pdf_vector_importer/lib/` | Reinstall the official release ZIP; explicit source/dev installs may use Preferences → **Install PyMuPDF** |
| pdfcadcore | ✅ In add-on | Same |

No system Python or pip is required. Import-time checks never run pip or contact
the network; they may restore only the exact bundled `extra.py` helper from the
copy already shipped inside the release.

## Legacy hardware notes

- **Text** and **3D Text** remain editable `FONT` objects. **Glyphs** and
  **Geometry** can create high curve/mesh counts; on **&lt; 8 GB RAM** PCs,
  import one page first when fixed outlines are required.
- Blender has no persistent, model-scaled, renderable Label entity. A Labels
  request records that item-specific host limitation before trying Text.
- Headless import validated; interactive UI still needs human confirmation (T-01).
- Glyphs produces real Blender `CURVE` outline data; Geometry produces real
  Blender `MESH` data. They are not aliases.

## Preflight command

```powershell
cd C:\1PDF-Importer-Blender
python preflight_check.py
python preflight_check.py --diagnostics
```

If an official release install cannot load PyMuPDF, reinstall the official
release ZIP. The Preferences installer is hidden in packaged releases; it is an
explicit source/development tool that confirms network use and package mutation.

Headless diagnostics:

```powershell
blender --background --python-expr "import addon_utils; addon_utils.enable('pdf_vector_importer'); from pdf_vector_importer.dependency_manager import print_diagnostics; print_diagnostics()"
```

---

## Blender version matrix

| Blender | Bundled Python | PyMuPDF | Status |
|---------|----------------|---------|--------|
| 5.2 LTS | 3.13 | >=1.24,<2.0 | ✅ Text/3D/Glyphs/Geometry/Raster host acceptance; automatic check passes |
| 5.0–5.1 | 3.12–3.13 | >=1.24,<2.0 | ✅ Smoke-tested at v1.0.42; not re-tested since |
| 4.5 LTS | 3.11 | >=1.24,<2.0 | ✅ Automatic check passes (Text, 3D Text, Glyphs) |
| 4.0–4.2 | 3.11 | >=1.24,<2.0 | ⚠️ 4.2: Glyphs fails; Text and 3D Text pass. 4.0–4.1 not tested |
| 3.6 LTS | 3.10 | >=1.24,<2.0 | ⚠️ Glyphs fails; Text and 3D Text pass |
| 3.1–3.5 | 3.10 | >=1.24,<2.0 | ⚠️ 3.1: Glyphs crashes Blender; Text and 3D Text pass. 3.2–3.5 not tested |
| 3.0.x | 3.9.7 | (none packaged) | ❌ Not supported |

All rows: Windows 64-bit (x64) only.

### Blender 5.x PyMuPDF bootstrap (v1.0.42+)

1. Vendored **cp310-abi3** wheel under `pdf_vector_importer/lib/`
2. Self-heal for missing `pymupdf/extra.py`
3. Official release damage/incompatibility → reinstall the official release ZIP

Unmanifested source/development trees may deliberately use the separately
labelled Preferences installer after accepting its network/package-mutation
confirmation.

### Text rendering

| Option | Blender result |
|--------|----------------|
| **Labels** | Persistent model label when supported; currently item-specific impossible in Blender, then Text fallback |
| **Text** | Flat editable `FONT` using the exact embedded PDF font program |
| **3D Text** | Extruded editable `FONT` using the exact embedded PDF font program |
| **Glyphs** | Non-editable real Blender `CURVE` outlines |
| **Geometry** | Non-editable real Blender `MESH` geometry |
| **Raster** | Aligned image patch clipped from the individual source text span |

The structural modes do not search Windows/macOS/Linux fonts by name. When an
exact embedded font cannot be used, only item-specific proven fallback is
allowed. Every attempt and final entity is recorded under
`extra.text_delivery`; an unverified terminal raster is a loud failure.

## CI coverage

GitHub Actions:

- `bl-pdfimporter-ci`: Python 3.9 compile (a stricter syntax floor, not a
  supported host), unit tests on Python **3.10–3.13** (3.13 is what Blender 5.2
  runs), `pdfcadcore_sync_check.py`, BCS-ARCH mode smoke, release ZIP smoke on
  Windows.
- `blender-host-matrix`: real Blender 3.1.2, 3.6.23, 4.2.23, 4.5.14 and 5.2.2,
  headless, with the release ZIP installed (see "Oldest tested" above). Not a
  required check; a failing leg is a compatibility finding for that version.
