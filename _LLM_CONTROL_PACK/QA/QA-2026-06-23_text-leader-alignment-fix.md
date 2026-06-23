# QA-2026-06-23 - Text & Leader Alignment Follow-up

## Scope

Primary alignment fix is in `C:\1PDF-Importer-SketchUp`: native SketchUp Labels now avoid leader-vector misuse, rotated label-mode text routes to mesh text, and 3D Text uses the same left/baseline anchor as Labels.

Blender text anchors were not part of the SketchUp leader/vector defect. During cross-repo validation, Blender exposed a headless test-order issue: `write_import_report` assumed `bpy.app.version` always exists, but an earlier headless test can create a partial `bpy` stub.

## Blender Action

- `pdf_vector_importer/bl_import_engine.py` now reads Blender host version through a guarded helper.
- `tests/test_import_report_writer.py` now repairs partial `bpy` stubs before importing the report writer.

## Validation

- `python pdfcadcore_sync_check.py` - ALL IN SYNC
- `python -m pytest tests/test_text_mode_builder.py tests/test_core_pipeline.py tests/test_import_report_writer.py tests/test_clean_break.py` - 34 passed

## Resolution

No Blender label/3D Text anchor code change was required. Validation hardening is safe, improves CI determinism, and removes a false red gate while the SketchUp alignment fix proceeds to commit/push.

## Release evidence

- Fix commit: `2a0410a fix: harden Blender import report version detection`
- Auto-release version bump: `v1.0.35`
- Release asset: `Blender-PDF-Importer_v1.0.35.zip`
- CI: `bl-pdfimporter-ci` passed on the `chore: bump version to 1.0.35` commit.
- Website dispatch: BlueCollar website `product-release` workflow passed for the Blender release.
