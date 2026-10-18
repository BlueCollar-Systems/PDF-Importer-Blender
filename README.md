# PDF Vector Importer for Blender

[![Version](https://img.shields.io/badge/Version-1.0.112-blue.svg)]()

**BUILT. NOT BOUGHT.** -- BlueCollar-Systems

Import PDF vector drawings as native Blender geometry (Curves, Collections, Materials).
Powered by the pdfcadcore shared extraction library and PyMuPDF.

## Features

- **4 Import Modes** (BCS-ARCH-001) -- Auto (default, picks strategy per page), Vector, Raster, Hybrid
- **6 Text Representation Options** -- Labels, Text, 3D Text (default), Glyphs, Geometry, and Raster (orthogonal to page strategy)
- **Maximum fidelity by default** -- Source Accurate colors on first use; optional preview styles and saved preferences remain available
- **Arc & Circle Detection** -- Reconstruct true arcs and circles from polyline approximations
- **OCG Layer Support** -- Map PDF Optional Content Groups to Blender sub-collections
- **Color Grouping** -- Organize geometry into sub-collections by stroke color
- **Material Assignment** -- Automatic diffuse materials from PDF stroke colors
- **Text Import** -- Import text as Blender font objects with position, size, and rotation
- **Face Generation** -- Convert closed loops and rectangles to mesh faces
- **Line Width Mapping** -- PDF stroke widths mapped to curve bevel depth
- **Dash Pattern Preservation** -- Retain PDF dash styling information

## Candidate update (v1.0.112)

Dense drawings with rounded strokes could show thousands of dashed Blender relationship guides across the sheet. The importer now keeps each verified rounded footprint visible while retaining its redundant source centerline as a hidden editable helper. Both objects, their geometry, materials and parent links remain available; the drawing uses native editable curves. Your viewport overlay settings stay unchanged.

Only source-bound, fully verified replacements qualify. Changed native widths, extra geometry or uncertain construction cannot hide the original drawing. If construction fails, the importer attempts every part of the visibility, width and reference restoration and reports any incomplete cleanup.



## Dated validation update — October 18, 2026

The local v1.0.112 r18 package README and delivery addendum retain their
build-time dense-GUI pending state and the earlier 5.2 owner failure on a
descriptive version-string check. Those records remain unchanged.

The later master test-package guide records exact r18 dense import/save/reopen
and ordinary desktop Save As/fresh reopen in Blender 5.2.2. It reports a clean
full sheet with Overlays and Relationship Lines enabled, plus focused native
compatibility/edit/persistence controls in 3.1.2. This is the later operator
guide's recorded scope, not a new native test performed for this note.

The exact ZIP and source identity are retained, but the later raw desktop
receipts were not located in the targeted durable package/validation inventory.
Locate and bind those receipts, or repeat the bounded exact-r18 desktop checks,
before treating this reconciliation as independently reverified acceptance.
The older five-host v1.0.109 matrix is not transferred to v1.0.112. No whole-page
pixel parity, every-mode coverage or non-Windows runtime support is established.

New imports receive r18's persisted helper-visibility repair. Older saved
models keep their existing flags. Local r18 is 1.0.112; GitHub Latest remains
1.0.111. No release or ZIP archive bytes were changed by this dated note.

## Previous candidate (v1.0.111)

PDF Type0 fonts labeled `cid` can contain ordinary name-keyed CFF programs. When the original bytes prove that structure, the importer uses its existing validated font conversion and original character-to-glyph map to retain editable text. Actual CID-keyed, malformed or ambiguous programs remain rejected. Original PDF inventory labels and exact program hashes are retained separately from the canonical font format.

Failed font builds now retain their evidence by the verified original font program, so a different inventory/span spelling cannot incorrectly stop the text fallback ladder. Missing, mixed, wrong or ambiguous program evidence remains unverified. Existing Type3 and empty-stream absence behavior is preserved.

Inferred word gaps in symbolic subset fonts can use an empty space glyph observed in the PDF's original font program, preserving editable text instead of an unnecessary image fallback. Visible, unobserved, ambiguous or changed source glyphs remain unverified and follow the existing reported fallback.

Opaque-fill ownership checks now use a temporary native-pointer index when every collection member has a readable nonzero pointer. Each selected object still passes the original native-identity, geometry and material checks; collections that cannot be indexed retain the ordered scan. A final pointer readback rejects changed ownership before fill-depth placement. Source capsule cleanup also indexes primitive IDs once while retaining the original uniqueness and unchanged-point checks.

Qualified round-stroke footprints now share one scene update before their existing filled-surface and measured-area checks, including on dense sheets. Older-host cubic fallback uses one additional update and rechecks every retained footprint. Cancellation remains available during construction, validation and linking; rejected batches verify removal of private geometry and restoration of original line widths and references. Final source, ownership, material and local/world placement readbacks run after the last progress callback. Density alone no longer discards qualified round ends.

Single convex linear fills under convex linear PDF clips now retain their original commands when wholly contained, or use exact rational intersections when cut. Their implicit fill closure reaches the existing filled-curve route, with original closure, style, paint order and cut-area provenance preserved. Exactly collinear source fills are recorded as nonpainting; unsupported curved, compound or uncertain intersections remain reported.

The preceding v1.0.110 test candidate completed an actual Blender 5.2.2 import of a dense foundation sheet with 4,485 qualified round footprints, 1,309 filled surfaces and editable text, then saved and reopened in a fresh process. Geometry, source bindings, materials, packed assets and placement passed persistence comparisons. On that instrumented PC the import took about 211 seconds, versus 408 seconds for the preceding candidate. This is one file and one PC; rendered visual comparison and broader desktop compatibility checks remain pending. The additional cleanup indexing optimization is covered by source tests.

## Installation

**Windows 64-bit (x64) only for now.** macOS, Linux and Windows on ARM are not
supported by the release ZIP: the PDF reader it bundles (PyMuPDF) is built for
Windows x64 only, so the add-on cannot import there and reinstalling will not
change that.

### Blender Add-on (Recommended)

1. Download `Blender-PDF-Importer_vX.Y.Z.zip` from Releases, or build it with:
   ```bash
   python build_release.py
   ```
2. If upgrading, disable **PDF Vector Importer**, save Preferences if needed,
   and close every Blender window. Reopen Blender with the add-on still disabled.
3. In **Edit > Preferences > Add-ons**, choose **Install...** on older Blender,
   or the menu's **Install from Disk...** on newer Blender, then select the ZIP.
4. Enable **PDF Vector Importer**. If the list still shows an older version,
   refresh the local add-on list or restart Blender before importing.

Release ZIPs include a private PyMuPDF runtime under `pdf_vector_importer/lib`,
so users do not need system Python, pip, or operating-system packages. Import
never runs pip or downloads packages. It may restore only the exact bundled
`extra.py` helper from the copy already inside the release. The preferences-
panel installer is hidden in packaged releases. It is an explicit
source/development tool that warns and requires confirmation before pip may use
the network and replace private package files. Customers should reinstall the
official release ZIP for any runtime damage beyond the bundled helper repair.

The bundled Windows runtime is PyMuPDF 1.28.2. It fixes the upstream repeated
text-trace reference-count failure; release checks exercise repeated extraction
and require a clean interpreter shutdown. Source installations require 1.28.2
or newer. The bundled `extra.py` and its repair copy remain byte-identical.

**Offline install:** Release ZIPs from GitHub work without internet after download.

## Upgrading / skipping versions

Disable the existing add-on and restart Blender before installing an upgrade.
Disabling alone leaves its PDF reader DLL loaded on Windows, so a hot update can
leave files from two versions. Install the ZIP before re-enabling the add-on.
If an import reports an installed-content hash mismatch, repeat that cold
installation sequence. Skipping versions is supported; import a representative
PDF after a major jump.

### Manual Install

Copy the `pdf_vector_importer/` directory from the release ZIP into your
Blender add-ons folder (Windows 64-bit only):
- `%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\`

## Usage

After enabling the addon:

1. **File > Import > PDF Vector (.pdf)**
2. Select a PDF file
3. Choose import mode (Auto, Vector, Raster, or Hybrid) and adjust options in the import panel
4. Click **Import PDF Vector**

On Blender 4.1 and newer you can also drag a PDF from Windows Explorer onto the
3D Viewport or the Outliner: the import options open for that file, and
**Import PDF Vector** imports it. One PDF per drop.

Geometry is grouped into collections by page and (optionally) by source layer or color.
Ordinary even-odd fills retain their counters in one editable curve. Proper
self-crossings are divided into edge-equivalent simple loops so Blender does
not fill the entire crossing's box, including a single crossing contour.
Simple nested or disjoint nonzero fills use their original directed source
paths to preserve holes and same-winding solid interiors. Unknown source
winding, crossing nonzero contours or excessive topology work keep the original
editable paths and report incomplete fill delivery. Choose Raster mode when
full-page appearance is needed for an unsupported fill. The source topology
work is bounded and remains cancellable. Isolated opaque round-ended straight lines
keep their editable centerline and gain a filled editable stroke boundary;
moving the centerline object moves that boundary with it. Their exact rational
curve is checked against the actual host fill. Hosts that evaluate it differently
use an explicitly reported cubic approximation, with radial error below
0.000273 times the pen radius, plus native coordinate storage error. Overlapping
or unproven clipped/blended round strokes retain their lines and are reported
for review. This route does not change butt or projecting-square strokes.
Page selections such as `1,3-5` import each selected page once. Reversed ranges
are accepted; invalid entries and pages outside the PDF are rejected before
scene creation, so a typo cannot unexpectedly import the whole document.
Source Accurate is the initial visual style and preserves PDF colors against a
separately hideable white sheet. Blueprint and High Contrast are optional previews;
any previously saved default visual style is respected.
Automatic focus opens ordinary opaque Source Accurate drawings in flat Solid
Texture view so embedded images and material-colored vectors appear together.
Images with alpha or an unproven shader graph, and existing transparent/composite
paint, retain the real material preview route. Scene color-management settings
remain unchanged; this viewport aid is not a calibrated PDF color proof. Turning
off automatic focus leaves the current viewport alone.

Python callers can pass `user_scale` in the `config` dictionary to
`pdf_vector_importer.bl_import_engine.import_pdf`. It must be a finite positive
number and defaults to `1.0`. The multiplier applies to PDF extraction, page
dimensions and placement, and resume checkpoints. Resume with the same scale as
the interrupted import; a changed scale is a different import configuration.

Long imports publish phase/page/object progress in Blender's status area and in
`import_report.json`. The report includes a representation-aware complexity
estimate (`work_units` and `tier`) so Geometry, Glyphs, and 3D Text requests are
not presented as equivalent to flat Text. Interactive imports use Blender's
main-thread modal timers, allowing **Esc** and **Cancel Active PDF Import**
between geometry, text, image, and page phases. The current phase finishes
before a queued input is processed; the next object heartbeat then applies
cooperative cancellation. The incomplete page is
rolled back, completed pages remain, and an atomic source/config-bound checkpoint
records exactly which pages remain. Enable **Resume Interrupted Import** on the
same PDF and settings to continue in the existing root collection without
duplicating completed pages.

One item that cannot be brought in as asked never costs its sheet or the
sheets after it. A letter whose trim at the sheet edge cannot be proven is
kept as drawn (untrimmed), a picture that cannot be placed is left out, and
every sheet is still built. The import finishes with plain lines such as
"Imported 12 of 12 sheets; 1 item needs a look", one line per sheet that
needs a look, and the folder that holds the full report.

Everyday imports run the checks that decide how each item is brought in plus
quick identity, position and colour checks. The slow exact re-proofs of every
letter shape are off by default; turn on **Audit import (slow self-checks)** in
the add-on preferences, or set `BC_PDF_AUDIT=1`, for testing and support. Each
import report records `audit_mode`.

Choose the representation you actually need: **Text** is flat editable Blender
`FONT`, **3D Text** is extruded editable `FONT`, **Glyphs** is fixed `CURVE`
outline data, **Geometry** is fixed `MESH` data, and **Raster** is one aligned
image patch per source text item. **Labels** requests a persistent,
model-scaled label entity; Blender currently has no such renderable entity, so
that item-specific capability result is recorded before the closest fallback
is attempted.

### Text-mode fallback ladder (TEXTMODE-1)

**The requested text mode is the delivered text mode.** Alignment, rotation,
or scaling defects are fixed *inside* the requested mode — never by
substituting a different mode. Substitution is permitted only when the
requested mode is genuinely impossible for this importer + option + PDF, must
walk the documented ladder below (most closely related rung first), and is
always recorded. If even the terminal raster patch cannot be verified, the
item is reported as failed instead of being claimed as delivered.
(Owner directive 2026-07-13.)

FINAL Blender ladder (left rung first):

| Requested | Ladder |
|-----------|--------|
| **Labels** | Labels → Text → 3D Text → Glyphs → Geometry → Raster |
| **Text** | Text → 3D Text → Glyphs → Geometry → Raster |
| **3D Text** | 3D Text → Text → Glyphs → Geometry → Raster |
| **Glyphs** | Glyphs → Geometry → Raster |
| **Geometry** | Geometry → Glyphs → Raster |
| **Raster** | Raster only |

Notes:
- Text, 3D Text, Glyphs, and Geometry use the exact font program stored in
  the PDF for the source span. When the PDF names a font but does not store it
  (common on CAD and Tekla drawings that use Arial), the installed font of that
  name is used only if the PDF proves it is the same font: the font uses plain
  WinAnsi letters and has no ToUnicode letter map of its own, exactly one
  installed font file has that name and style (font collections such as .ttc
  files are not read, and a folder search only looks at single .ttf/.otf
  files), every width the PDF lists matches that installed font to within
  1/1000 of the text height, that font has every character the page draws with
  it, and each text item's letters are the letters the page actually draws
  there (an `/ActualText` replacement keeps that item on the outline route). If
  any check fails, the text keeps the outline route below. The import report
  lists each check under `extra.installed_fonts`, and each such text item
  records `font_source: installed` and `widths_matched`.
- Text and 3D Text make one editable text object per line of PDF text when the
  PDF places every letter where the font's own widths put it (within 0.5% of
  the text height: no extra letter spacing, word spacing or spacing gaps).
  Otherwise, or if the one-object version fails its placement check, each
  letter is placed as its own text object as before. The placement check uses
  the font's own widths, which is how Blender 5.2.2 was measured to lay out a
  line (it does not kern); on other Blender versions a line whose font kerns
  two neighbouring letters stays letter by letter. Each text item records
  `text_object_granularity` (`span_object` or `per_character`) and the reason.
- Glyphs and Geometry are distinct host types and conversion paths: Glyphs
  verifies a real Blender `CURVE`; Geometry verifies a real Blender `MESH`.
- A generic exception is a failure, not proof that the requested type is
  impossible. Fallback continues only after item-specific capability evidence
  and complete cleanup of artifacts owned by that attempt.
- Unknown `text_mode` values raise before scene mutation. They are never
  silently normalized to another requested outcome.
- Auto/Vector/Raster/Hybrid is a page strategy and does not override a text
  representation request. A raster page can still contain requested native
  Text, 3D Text, Glyphs, or Geometry objects.
- `import_report.json` stores every source item, attempted rung, result,
  evidence, cleanup record, final representation, and entity ID under
  `extra.text_delivery`; failed item IDs are surfaced in diagnostics.
- These invariants are locked by
  `tests/test_representation_fidelity_blender.py` and
  `tests/test_terminal_raster_delivery_blender.py`.
- The proof, oracle, rollback, and reporting contract is maintained in
  [REPRESENTATION_FIDELITY.md](REPRESENTATION_FIDELITY.md).

## Compatibility

See **[COMPATIBILITY.md](COMPATIBILITY.md)** for the full matrix. Summary:

Operating system: **Windows 64-bit (x64) only.** macOS, Linux and Windows on
ARM are not supported.

| Blender Version | Bundled Python | Status |
|----------------|---------------|--------|
| 5.2 LTS | 3.13 | ✅ Host acceptance; automatic check passes (Text, 3D Text, Glyphs) |
| 5.0–5.1 | | ✅ Smoke-tested at v1.0.42; not re-tested since |
| 4.5 LTS | 3.11 | ✅ Automatic check passes (Text, 3D Text, Glyphs) |
| 4.0–4.2 | 3.11 | 4.2.23: automatic check passes Text, 3D Text and Glyphs; 4.0–4.1 not tested |
| 3.6 LTS | 3.10 | ✅ 3.6.23 automatic check passes Text, 3D Text and Glyphs |
| 3.1–3.5 | 3.10 | 3.1.2: six text modes passed the r8 neutral fixture; earlier matrix crash remains historical evidence. 3.2–3.5 not re-tested |
| 3.0 and earlier | 3.9 or older | ❌ Not supported |

Evidence levels:
- `✅`: run inside that Blender and passed.
- `⚠️`: run inside that Blender, but a text mode fails or part of the range is
  not tested.
- `❌ Not supported`: the bundled PDF reader cannot load there.

"Automatic check" is the `blender-host-matrix` GitHub check: it installs the
release ZIP into real Blender 3.1.2, 3.6.23, 4.2.23, 4.5.14 and 5.2.2 (headless)
and imports a small made-up two-sheet PDF. The current matrix passed all five
hosts for the v1.0.109 source on 2026-10-11 (America/Chicago), including the
previously failing Glyphs cases. These checks do not certify every PDF or
desktop interaction; [COMPATIBILITY.md](COMPATIBILITY.md) has the details.

## Requirements

- Windows 64-bit (x64) only for now. macOS, Linux and Windows on ARM are not supported by the release ZIP.
- Blender 3.1 or newer (Blender 3.0 ships Python 3.9; the vendored PyMuPDF wheel requires Python >=3.10)
- Bundled Blender Python 3.10+
- PyMuPDF >=1.28.2,<2.0, bundled in release ZIPs

## Development

```bash
# Lint
python -m ruff check .

# Tests
python -m pytest tests/ -v
```

## Batch Import

Run batch import summaries across a folder of PDFs:

```bash
python -m blender_pdf_vector_importer.batch_cli "C:\path\to\pdfs" --recursive --mode auto --pages all --json batch_report.json
```

Recursive batch summaries preserve the source subfolders inside `--summary-dir`,
so equally named drawings from different jobs cannot overwrite one another.
A summary write failure is reported once as a failed file; the next file continues.

## Project Structure

| Directory | Purpose |
|-----------|---------|
| `pdf_vector_importer/` | Blender addon (install via Edit > Preferences > Add-ons) |
| `blender_pdf_vector_importer/` | Standalone CLI and library for headless/batch processing |

## Known Limitations

| Limitation | Details |
|-----------|---------|
| Encrypted PDFs | Password-protected PDFs must be unlocked before import |
| Compression filters | Decoding is delegated to PyMuPDF. Malformed or non-standard compressed object streams may fail to parse |
| Raster-only scans | Pure raster PDFs produce no vector geometry |
| Clipped/XObject-heavy PDFs | Complex clip stacks and deeply nested form XObjects can produce partial geometry |
| Very large PDFs | Documents with >10,000 primitives can remain slow because Blender creates/verifies native objects individually; use the reported complexity/progress, cooperative Cancel, and page-resume checkpoint |
| Missing or malformed exact font programs | The affected item walks the reported structural ladder to a verified raster patch; an unverified terminal patch is a loud item failure |
| PyMuPDF required | Release ZIPs bundle PyMuPDF; source/dev installs can use the preferences-panel installer |
| Legacy hosts | Blender/Python combinations outside the listed compatibility matrix are expected-only until verified |

## Import report / scale trust

Imports emit `import_report.json` (`bcs.import_report/1.1`) with optional `extra.resolved_scale`.

Every report also records the exact Blender host version, source-PDF SHA-256,
importer version/tag/source commit, installed-package content hash, and the
absolute paths plus SHA-256 hashes of the critical engine/text modules under
`extra.package_identity`.

- Use `factor` only when `confidence >= 0.70` and `fallback_reason` is not `no_scale_detected`.
- Otherwise treat scale as unknown.

## Bad-PDF open gate

Blender refuses bad PDFs at open time (**fail closed**). SketchUp may fail open on rare gate errors; messages are aligned, detection parity is not.

## License

MIT -- Copyright (c) 2024-2026 BlueCollar-Systems

See [LICENSE](LICENSE) for full text.

---

AI-assisted development by Claude (Anthropic).
