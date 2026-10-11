# Preserved draft improvements and current import controls

Draft #60 was closed before its six unique commits reached main. This merge
preserves their work against source `92286a1` while retaining the newer modal
pipeline, item-level failure accounting, packed assets and source fidelity gates.
The original draft branch and frozen test packages remain separate.

Mixed-height sheets now have their selected gap between facing page edges. A
612 pt sheet followed by a 792 pt sheet previously overlapped by 20.32 mm in
the default spread arrangement. Placement records each actual sheet rectangle;
the opening view frames their union. Compact retains its current 5% default.
Resume checkpoints record the previous sheet height, and legacy height recovery
requires the unchanged PDF, options and a completed page prefix.

Sparse-page raster planning preserves faint ink, removes blank backing images
where appropriate, and chooses readable source colors before building objects
over an opaque raster. The report records each decision. An embedded-image
failure keeps successfully delivered lines/text and remains a reported failure;
it cannot silently become a duplicate page raster. Scale overrides are validated
before extraction. Native projected glyph area uses doubles after reading the
host's float32 coordinates, without weakening clipping or containment checks.

Validation on the reconciled source:

- 288 focused page placement, framing, raster, clipping and remediation tests.
- Full source suite: 1,907 passed, 5 skipped, 22 subtests passed (91.90 seconds).
- Whole-repository Ruff and Git whitespace checks passed.

These are source tests, including actual PDF extraction/renderer checks and
instrumented host consumers. They are not native Blender acceptance. The earlier
r6 package at `b42cee9` remains frozen and its native evidence applies only to r6.
The new source requires its own packaged all-mode/save/reopen, mixed-sheet and
cancellation/resume acceptance before publication. No release was triggered by
this reconciliation.
