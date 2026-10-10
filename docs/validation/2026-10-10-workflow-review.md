# Workflow review - 2026-10-10

## Changes made in this review

`pdf_vector_importer/bl_import_engine.py` now validates the whole page selection
before scene creation. Previously `0`, an out-of-range page, a typo, or a reversed
range could silently select the entire PDF. Invalid entries now explain the
problem; reversed ranges are normalized and overlapping selections import once.
Range endpoints are checked before expansion, including extremely large values.

Interactive imports now advance the existing engine through main-thread modal
timer events, yielding between geometry, text, image, and page phases. Escape
and the Cancel command are processed between phases. The next object heartbeat
uses the existing unfinished-page rollback and verified resume checkpoint.
No background thread mutates Blender objects, and scripted/headless imports
retain the blocking `import_pdf` API over the same delivery pipeline.
Cancellation checkpoints now retain only the last verified completed-page
aggregate. Native cancellation exposed a previous bug that saved source text
counts from the discarded page and counted them again on resume, falsely
rejecting an exact text delivery. Cancelled-attempt diagnostics remain in their
own report; the fidelity gate is unchanged.

`tests/test_page_selection_blender.py` adds 14 regressions. The reproduced
baseline was 9 failures / 5 passes; all 14 pass with the fix. README usage now
describes this behavior. `tests/test_modal_import_operator.py` adds control
dispatch and forced-cancel cleanup tests. No shared `pdfcadcore` files were
changed by this workflow review.

## Validation

- Initial complete suite with an explicit writable temporary directory:
  **1,458 passed, 5 skipped, 22 subtests passed** (214.51 seconds).
- Focused new regression suite: **14 passed**.
- Repeat complete suite: **1,475 passed, 5 skipped, 22 subtests passed**
  (343.43 seconds), including the additional concurrently authored clip tests.
- Ruff on the changed Python files: passed.
- After the modal change: **181 focused fidelity/cancellation/resume tests** and
  **5 modal-control tests** passed. A final combined fidelity, source-font,
  cancellation, and page-selection run passed **197 tests**; the additional
  failed-phase/released-timer guards passed in an **18-test** driver/remediation
  run. Native Blender 5.2.2 operator registration and
  main-thread event callbacks accepted Escape, rolled back the unfinished page,
  released the timer, and cleared the active-import state. Resume reused the
  original root collection, completed with zero failed text items, and removed
  the finished checkpoint. The source-count checkpoint regression now reaches
  the actual geometry cancellation heartbeat rather than stopping before it.
- `python pdfcadcore_sync_check.py`: passed.
- Initial pytest runs using the machine's existing default temporary directories
  encountered Windows ACL errors and were stopped. The complete run used
  `--basetemp=C:/TMP/audit-blender-20261010-01`; no assertions were weakened.

Native Blender **5.2.2 LTS**, with its bundled Python 3.13, imported an exact-font
synthetic fixture in Text, 3D Text, Glyphs, Geometry, and Raster modes with zero
reported text failures or fallbacks, zero raster failures, and zero geometry
issues. Labels recorded its expected, item-specific fallback to editable Text.
Each resulting report marks `import_contract_ready.ready=true`.

One private shop drawing imported 89 primitives and 13 text items in Auto / 3D
Text with zero reported text failures/fallbacks or raster/geometry failures.
An out-of-range native import rejected page 999 and left the scene unchanged.
Native results are under `C:/TMP/pdf-import-audit-20261010/`, including
`native-blender-5.2.2 LTS.json` and the per-mode import reports. The compact native
result snapshot is also retained as `2026-10-10-native-probe.json` beside this record.
The final settled-source modal cancellation/resume snapshot is
`2026-10-10-modal-probe.json`; temporary report locations are redacted there.

The current host agrees with the official [Blender download page](https://www.blender.org/download/).

## Boundaries and remaining work

These are **source checkout changes**; no installed add-on was replaced, no
release was rebuilt, and no publication was performed. The minimum host remains
Blender 3.1 / Python 3.10. The portable Blender 4.4.3 executable present on this
machine exited with `0xC0000135` (missing DLL dependency); older-host native
acceptance is therefore unavailable from this installation.

The native results verify the recorded delivery contracts for those inputs;
they do not certify every PDF, old machine, or pixel-level side-by-side display.
Native modal tests used real registered operators and WindowManager timers with
explicit event callbacks in headless Blender; a human interactive usability pass
remains useful. A single expensive delivery phase still finishes before its
queued input can be processed.

Concurrent edits to `text_page_clip.py` and `tests/test_text_page_clip.py` were
observed and preserved. They concern float32 page-clip verification tolerance
and are separate from the page-selection change in this review.
