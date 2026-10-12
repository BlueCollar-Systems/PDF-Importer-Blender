# Source fill and stroke repair

Actual desktop import of the immutable v1.0.108 r8 package in Blender 5.2.2
exposed a solid green counter and flat ends on the fictional source's red round
stroke. The historical model was retained read-only:
`C:\TMP\blender-native-5_2-20261011\vector-hole-bad-native108.blend`, SHA256
`4443677d5c1e6f280372d34cdcfd9f53b0ca36438ca7fb35a058e3eb3f46a2e4`.
Independent headless readback confirms the old green fill totals about
1099.16159 mm² and paints the counter; the source requires 716.84444444 mm².
This is historical desktop evidence, not desktop acceptance of v1.0.109.

The repair is Blender-only. Shared pdfcadcore source and manifests remain
unchanged. Each source fill keeps its source paint membership, vertices and
original PageData. Proper even-odd crossings become edge-equivalent simple
cycles. Simple nested/disjoint nonzero contours retain exactly those boundaries
where accumulated winding changes between zero and nonzero. A unique complete
original SVG path supplies direction, because MuPDF's detected rectangle
orientation and the extractor's canonical rectangle order are insufficient.
Ambiguous, masked, clipped or blended source bindings do not borrow a safe path
from another equal-shaped paint occurrence.

Point, work, candidate and split budgets bound topology work, with periodic
cancellation before native allocation. Over-budget or unsupported winding
preserves editable unfilled source contours and emits a failed geometry delivery
record. The report recommends explicit full-page Raster mode. An automatic page
crop has no positive proof that it would preserve unrelated editable text.

Positive Normal round-stroke qualification requires an opaque solid straight
source line, original SVG cap/width/transform binding, unchanged native-owned
centerline and absence of overlapping source paint. The new native footprint
uses the exact source width, with no display hairline expansion. It is parented
to its original centerline. Blender 5.2.2 accepts the rational quadratic
boundary; observed Blender 3.1.2 cyclic-NURBS area error triggers a checked
cubic-quarter-circle fallback. Its maximum geometric radial excess is below
0.000273 times the source radius, plus native coordinate storage error. Native
mesh area is checked against the source capsule area before accepting either.

The standalone scripts are run in actual host executables with
`--background --factory-startup --python-exit-code 1`; they create new owned
output directories and retain all earlier failure receipts. They do not operate
the desktop UI or upload PDFs.

| Probe | Meaningful controls |
| --- | --- |
| `scripts/native_fidelity_probe.py` | Full fictional fixture import; source counter and round-stroke area/bounds; strict native curve/material/parent snapshot through Save/Open; optional read-only historical bad-model inspection |
| `scripts/native_compound_fill_probe.py --topology-matrix` | Nested, overlapping, touching in both orders, compound and lone self-crossing even-odd loops; opposite/same nonzero winding; unsupported nonzero overlap and topology-budget path retention; native curve Save/Open |
| `scripts/native_nonzero_fill_probe.py` | Eight complete-engine rectangle start/direction controls, source pixels, exact expected area and empty/solid counter state; strict native snapshot through Save/Open |
| `scripts/native_round_stroke_probe.py` | Horizontal, vertical, slanted, dot and short positive source strokes; actual area/bounds; centerline-parent persistence through Save/Open |

Both Blender 3.1.2 and 5.2.2 passed these bounded source geometry controls.
Evidence remains in `C:\TMP\blender-native-fidelity-evidence-20261011` with
separate JSON receipts for each run. The original-direction controls were
also independently reviewed from eight immutable source PDFs. The independent
review found and drove regressions for the lone bow-tie, unsafe work loop,
nonzero winding and safe-path ownership cases.

Final independent source review passed 135 focused checks and binds twelve
runtime/test files in
`C:\TMP\blender109-independent-review-20261011\BLENDER109-FROZEN-SOURCE-REVIEW.json`,
SHA256 `2cfcf9f92ad16f17ec25f60b45e0bdcdb6d3d31549a99b92027acbfc978c7c81`.
The final compound adapter SHA256 is
`b7104039a5f35368ef7da7adcdb2ff43588beb09fbf02a32ae6c9e07a6be1844`.
This review confirms source behavior; packaged native and desktop acceptance
remains separate.

The 5,000-vertex dense-star source-control scan reaches its candidate budget in
2.71 seconds on this PC, with 381 cancellation heartbeats. This is a bounded
source scan result, not a promise about every PDF or older hardware.

Earlier r8 six-mode headless acceptance and v1.0.108 publication retain their
[documented scope](2026-10-11-r8-native-and-release-acceptance.md). They do not
certify the new package. Remaining acceptance includes installation/content
verification of v1.0.109, actual desktop import options and save/reopen, fresh
processes with source assets unavailable, additional installed host versions,
and PDFs outside these controlled cases. Shape extrusion UI remains disabled.
