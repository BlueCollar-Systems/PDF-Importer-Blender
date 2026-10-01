# Drawing fidelity and opening view validation

This change preserves visible source geometry, editable text and a straight-on
view of the imported batch. Private PDFs, native models, screenshots and machine
paths remain outside this repository. New regressions use synthetic drawings
and host doubles; native acceptance was checked separately in Blender 5.2.1 LTS.

## Source changes

- Source paint fully outside proved clip bounds is excluded. A narrow analytic
  rule clips eligible axis-parallel, butt-cap, undashed lines to the visible
  page/source rectangle without changing their visible ink. Unknown paths and
  clipping remain conservative.
- Text uses the actual painted font program and a complete character/origin
  census, including distinct same-family subsets and annotation font resources.
  Equivalent program resources remain explicit in provenance.
- Text crossing the page receives an owned native Geometry Nodes intersection.
  FONT bodies, glyph curves, transforms and text depth remain editable; no
  raster substitution is introduced by this clipping operation. Independent
  planar intersection measures the expected visible area. Native evaluated
  containment, area, depth, representation and helper cross-links are verified
  again after stacking.
- Failed clipping or final text verification stops the page and rolls back its
  owned objects. Only a positively verified rollback can retain the prior
  completed-page resume state; an unverifiable rollback blocks resume. Failed
  pages cannot be reported as complete or ready.
- Distinct source RGB values no longer collide in a cache rounded to three
  decimals. Source style and existing alternate preview styles remain intact.
- View fitting sets the final top-view quaternion and orthographic mode directly
  instead of starting an asynchronous axis animation. The native window matrix
  supplies the actual lens/aspect projection; the greatest projected half-extent
  sets the final distance with a 10% margin. All batch viewports are updated.
- Solid preview uses flat material colors and disables extra Workbench shadows,
  cavity, object outlines and specular highlights. Source images/transparent
  paint retain material preview. The user's scene color-management transform is
  unchanged, so this is not a calibrated PDF color preview.

## Verification

- Final full suite: **1218 passed, 5 skipped, 22 subtests passed**. The run also
  emitted one non-failing pytest cache-option warning. The independent review
  also passed `git diff --check` and compared the shared module hashes.
- Shared extraction was exercised across **106 pages from 39 unique PDFs**.
  This is a headless extraction result, not a native acceptance claim for every
  page. Exact source-font and clipping modules match the independently reviewed
  shared-core copies.
- Earlier native edge tests covered Text, 3D Text, Glyphs, Geometry and Labels
  on two source pages. Subset-font and resume scenarios also passed. The final
  preview-style change does not alter their geometry path.
- Final exact-source native runs covered two original edge cases plus a scan,
  annotation and clipped-line case. Both host runs exited **0**; all five cases
  were ready. Each saved model's hash was verified and its reopened content
  digest matched the original. Initial and reopened views were top orthographic,
  with every measured projected corner inside the viewport.
- Both final native receipts matched all **432 source files** and the current
  import engine. Owned native processes exited and their exclusive host slot
  was released. Installed applications and user projects were not changed.

The committed tests cover partial-ink loss, out-of-page ink, lost 3D depth,
explicit empty visible results, failed/unverifiable rollback, prior-page resume,
nearby source colors, mixed viewport aspects and synchronous batch framing.
