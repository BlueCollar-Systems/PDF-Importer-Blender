# Preserved draft improvements and current import controls

Final closure: [r8 native and v1.0.108 release acceptance](2026-10-11-r8-native-and-release-acceptance.md)
supersedes the pending r7/r8 and publication statements below. This record
preserves the earlier source tests, failures and partial native runs.

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
- Initial merge suite: 1,907 passed, 5 skipped, 22 subtests passed (91.90 seconds).
- Final source with the controls below: 1,924 passed, 5 skipped, 22 subtests
  passed (169.11 seconds).
- Whole-repository Ruff and Git whitespace checks passed.

These are source tests, including actual PDF extraction/renderer checks and
instrumented host consumers. They are not native Blender acceptance. The earlier
r6 package at `b42cee9` remains frozen and its native evidence applies only to r6.
The new source requires its own packaged all-mode/save/reopen, mixed-sheet and
cancellation/resume acceptance before publication. No release was triggered by
this reconciliation.

The final source also closes two concrete control gaps. Resume checkpoints bind
actual importer Python and bundled native runtime bytes with a streamed SHA256;
same-size/same-timestamp changes and old unbound checkpoints reject before scene
mutation and leave existing geometry/checkpoints intact. Legacy stack-height
recovery remains supported for correctly code-bound checkpoints.

The actual r6 Blender 3.1.2 probe rejected a standalone inferred spacing item in
Glyphs because its converted curve had no splines. Glyphs now retains an empty
typed carrier only with verified character/program binding, original and usable
font byte hashes, and independently empty outlines in both programs. A missing
painted glyph ID additionally requires the native synthetic-spacing marker and
flag. Original origin and advance remain; visible/unbound whitespace is not
accepted as empty. Material restoration on older hosts requires a main-database
owner. These source regressions passed; the new native r7 acceptance is pending.

The exact r7 native run on Blender 3.1.2 subsequently passed Text, 3D Text and
Glyphs with mixed-height Compact placement and save/reopen. Geometry exposed
the same standalone inferred-space gap: a verified empty mesh was rejected
because it had no vertices. Geometry now keeps an empty MESH only with the
original/usable font zero-ink proof, zero vertices/edges/faces, exact source
metric placement and owned material. It skips bbox fitting of an empty mesh.
Unbound, visible, unexpected-topology and missing-material-owner cases remain
failures. The nonpositioned Glyphs conversion also retains a surviving allocated
curve when construction rollback and temporary-curve release fail together.

The final source with prepared 1.0.108 metadata passed 1,932 tests, 5 skips and
22 subtests (159.16 seconds); affected consumer/proof tests passed 135 tests.
Whole Ruff passed. Earlier r7 packages and partial native receipts remain
unchanged. The new r8 package requires its own native Geometry, remaining-mode
and control acceptance; production publication remains explicit and held.
