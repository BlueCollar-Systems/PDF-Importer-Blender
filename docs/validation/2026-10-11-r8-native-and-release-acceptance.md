# Final r8 native acceptance and v1.0.108 publication

The r8 installable package passed all six modes in actual Windows Blender
3.1.2 and 5.2.2. Each host imported a neutral mixed-height, two-sheet PDF,
saved and reopened every mode, and passed independent post-exit validation.
This closes the pending r8 acceptance in the
[earlier reconciliation record](2026-10-11-pr60-reconciliation.md).
Earlier r6/r7 failures and partial results retain their original scope.

The tested archive was `Blender-PDF-Importer_v1.0.108-test-2026-10-11-r8.zip`:

- Source: `92716b86a2a35d1bca721e77083bb61594d9dfb6`.
- Archive SHA256: `1f72de38fa6068166b1acc405a7d8fdc2b8f2c5c5e31358f812d7810a79af9ac`.
- Installed content SHA256: `b762ec217361345b7408cafb3563bbd7b08eb191c72c9c95f5169d654dc17d00`.

| Actual host | Bound request SHA256 | Native result SHA256 |
| --- | --- | --- |
| Blender 3.1.2 | `f8fa932f0d044453870fd409b98e3b2b6651ce2c6b695225f9f8dd469f956bf6` | `43a81f43aa287880c297dde0395016fb903dced2dd605d4f4f953546b724bf44` |
| Blender 5.2.2 | `15442649c3b74fc099ce860d732da31bf42b62a95d14c3f4c924ff1adb06c911` | `8020b8fffc7fe7ed942effcd96f47d64510b813f0aa4f518eec886fb170e5a20` |

Text, 3D Text, Glyphs, Geometry, Raster and Labels each reported a ready
delivery contract and zero failed text items. Glyphs and Geometry retained
the source-proven empty spacing carriers. Raster delivered eight actual
images. Labels disclosed eight capability fallbacks to flat text. Other
modes reported zero fallback items.

Save/reopen compared actual mesh topology, curve control points, materials,
packed font bytes, native image pixels, custom properties, collections and
scene units. The two physical sheet rectangles retained source dimensions,
order and the selected Compact 5% gap without overlap; coordinate comparison
used a bounded native float32 precision allowance.

Registered operators and native timers also passed ESC rollback/resume,
the Cancel operator, duplicate/reversed page normalization, invalid-page
rejection and changed-importer checkpoint rejection. The rejection checks
compared complete scene state before and after, including an explicitly
empty diagnostic scene. Normal import/save acceptance still required objects.

These were headless host runs with explicitly driven operator/timer callbacks.
Save/reopen occurred in the same process with source assets available. This
does not certify desktop clicks, every event-loop timing, source-unavailable
fresh-process reopening, every PDF or all hardware.

[Published v1.0.108](https://github.com/BlueCollar-Systems/PDF-Importer-Blender/releases/tag/v1.0.108)
is immutable and targets `f0f799a48d73b27ff77e66c98ecc2fd706aae627`.
Its downloaded `Blender-PDF-Importer_v1.0.108.zip` is 20,152,315 bytes with
SHA256 `e0f9524d3deea4c5ab254e591f0a389f3d9e47a6a8a739675efe5633d994b438`.
The production archive has 442 payload files byte-identical to native-tested
r8; only generated release identity metadata differs, and four test-only
documents are omitted. The installed content hash above is unchanged.
The exact attested candidate and downloaded published asset matched; Windows
cold-import smoke passed on Python 3.10.11 and 3.12.10. The production ZIP
was not independently relaunched in Blender, so its native evidence is this
explicit payload equivalence to r8.

Local evidence remains in the test hub's `Blender/Validation-r8` directory:
`NATIVE-ACCEPTANCE-SUMMARY.json`, per-host native artifacts and
`PUBLISHED-1.0.108-VERIFIED.json`. Raw neutral models and historical runs stay
outside this repository.
