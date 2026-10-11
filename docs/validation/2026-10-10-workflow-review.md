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

## Final shared-core review

The shared font-binding changes were reviewed separately from the initial
workflow changes above. Same-name font programs are identified by their exact
original program SHA and character occurrence. Missing printable paint traces
remain unverified. An inferred U+0020 space is accepted only with the native
MuPDF synthetic flag, a unique original occurrence without a painted trace,
and an empty space glyph in the original exact font program. It retains its
source quad and semantic gap with no invented painted glyph ID. Visible or
missing original space glyphs remain rejected. The CFF proof uses the existing
original CFF wrapper directly; an empty PDF Unicode census cannot gate that
nonpainting proof.

After the synthetic-spacing files and tests were synchronized, **276 focused
font, source-outline, text delivery, modal, and raster tests passed**
(51.57 seconds). The real Helvetica A-gap-B extraction probe retained native
synthetic flags 532, exact font binding, and no paint glyph ID for the inferred
space. The shared-core synchronization check passed.

Native Blender 5.2.2, using its bundled Python 3.13 and the source checkout,
also imported that new synthetic-spacing fixture in all six text modes with
zero text failures. Labels recorded its expected fallback to editable Text
for the three source spans. Every mode's report marks
`extra.import_contract_ready.ready=true`. The synthetic-only result snapshot
is `2026-10-10-synthetic-spacing-probe.json` beside this record; full temporary
reports remain under `C:/TMP/pdf-import-audit-20261010/`.

The final competing-paint guard also rejects a synthetic space when exactly
one painted occurrence shares its identity/origin, and clears any stale trace
ID. The settled source-font regression file passed **28 tests** (9.93 seconds)
after that final guard was synchronized. The native spacing fixture has no
competing paint at its inferred space, so this tightening preserves its
recorded acceptance. The earlier broad and native runs precede this last
negative-case change.

This review did not create commits. Concurrent workspace activity committed
the earlier workflow changes as `7816290`; the final synthetic-spacing changes
were reviewed as subsequent source checkout edits. No installed extension or
release artifact was replaced by these checks.

## Boundaries and remaining work

The initial review above covered **source checkout changes**. The subsequent
owner-authorized installable test snapshot, legacy compatibility fixes, and
native extracted-package checks are recorded in
`2026-10-10-test-package-review.md`. No installed add-on was replaced and no
publication was performed. The minimum host remains
Blender 3.1 / Python 3.10. The portable Blender 4.4.3 executable present on this
machine exited with `0xC0000135` (missing DLL dependency). A separately verified
official Blender 3.1.2 portable was subsequently used for the native floor check.

The native results verify the recorded delivery contracts for those inputs;
they do not certify every PDF, old machine, or pixel-level side-by-side display.
Native modal tests used real registered operators and WindowManager timers with
explicit event callbacks in headless Blender; a human interactive usability pass
remains useful. A single expensive delivery phase still finishes before its
queued input can be processed.

Concurrent edits to `text_page_clip.py` and `tests/test_text_page_clip.py` were
observed and preserved. They concern float32 page-clip verification tolerance
and are separate from the page-selection change in this review.
