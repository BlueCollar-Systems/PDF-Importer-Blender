# Blender compatibility evidence checked on 2026-10-12

The current published v1.0.109 source is
`66ebf1940b610b9c7711505e416e62a7bfd855c4`. The
[five-host actual Blender matrix](https://github.com/BlueCollar-Systems/PDF-Importer-Blender/actions/runs/38196670910)
passed at PR source `c6339283c9904c7a30c71e9cffd0f55fc646ea1e` before that merge.
The source trees are identical. All five jobs completed successfully:
3.1.2, 3.6.23, 4.2.23, 4.5.14 and 5.2.2. Each installed the built add-on,
registered its options, imported both fictional sheets as Text, 3D Text and
Glyphs, and retained both sheets in the distant sheet-edge text fixture.
That run completed on 2026-10-12 UTC, 2026-10-11 in America/Chicago.

The earlier Glyphs failures on 3.6.23 and 4.2.23 are historical findings.
They are not the result for the current checked source and fixtures. The
headless matrix establishes actual host data delivery, not desktop view,
button, calibrated color or arbitrary-PDF acceptance.

Separate r11 desktop acceptance used the same published add-on code on
3.1.2 and 5.2.2. Ordinary default Source Accurate, automatic focus and 3D Text
imports passed one mixed-content and one vector fixture in each host.
Four saved models were reopened in fresh desktop sessions while the owned
source PDF copies were unavailable. Pictures, text, counter holes and round
stroke ends remained visible. The 3.1.2 vector journey also proved automatic
recovery from Local View. This covers four default journeys, not every text
mode or an arbitrary source corpus.

The preserved final r11 desktop receipt has SHA-256
`cb907a343d59f67d3d69156783390f1f3a02e84e2ffb377f0cc60d950e1c521b`;
its independent review has SHA-256
`ff0d3511e8fe8e3901175a392f0e9b92e7460cdd99565f66068aca49ce5cff6a`.
These are separate from the older r8 six-mode native-data acceptance. No
foreground view code was forced in a background host for desktop proof.

The fresh source sweep passed 2,034 Python tests and 22 subtests, with five
optional checks skipped. Ruff and the shared-core manifest check passed.
The sweep found stale compatibility tables and corrected their evidence
labels; it did not change import code, runtime payloads or existing releases.
