# Local installable test package - 2026-10-10

The owner requested usable packages for local testing. The final Blender ZIP is
`C:/BlueCollar-Test-Builds/2026-10-10/Blender/Blender-PDF-Importer_v1.0.106-test-2026-10-10-r5.zip`
(20,134,343 bytes), SHA-256
`b74b6a95c1556a6dd2ee23534f3890ac1c1922391fba2eba72f3707be4b27d02`.
`START-HERE.txt` names this installer and provides plain installation, initial
import, cancellation, resume, and rollback steps. The add-on is installed from
the compressed ZIP in Blender Preferences. Earlier candidate archives and hashes
are preserved under `Superseded`, leaving one current ZIP at the folder's top
level. No user-installed add-on was replaced, original PDF modified, or official
release published by building and testing.

The working-tree snapshot includes the current shared font-program/spacing
guards, strict page controls, modal cancellation, and resume fixes. Its generated
release identity binds shipped content; it is explicitly a local test build,
not an official release. Base version 1.0.106 remains. `BUILD-INFO.json` binds the
shared files and workflow/text modules by SHA-256. Source edits and externally
created commits are distinguished from a published package; this review made no
commits or pushes.

Native floor testing exposed two actual Blender 3.1 problems which are fixed in
the package. `Object.to_curve()` returns a temporary TextCurve on that host;
copying its datablock retains FONT-specific links and crashes the dependency
graph after source cleanup. The importer now creates an owned CURVE for that
case and copies Bezier/NURBS/POLY spline geometry, rational weights, handles,
curve settings, and owning materials. Bulk coordinate writes avoid RNA's
per-point handle realignment; exact coordinate/handle readback and existing
visual/affine gates remain enforced. No representation is downgraded. Construction
and rollback failures retain datablock ownership, including a simultaneous
temporary-curve release failure, so cleanup can retry or stop delivery.

That old host also drops material slots when converting an empty FONT to MESH.
Only the already proven zero-ink carrier with zero vertices and zero material
slots restores materials from its still-live source, resolving each material to
its main-database owner. Missing ownership rejects delivery. The editable empty
MESH keeps semantic text, source metric affine, and advance; material, geometry,
and JSON verification gates were not weakened.

Validation:

- **131 focused tests passed** for text building, requested representations,
  source glyph reporting, owned spline reconstruction, exact readback, cleanup
  double/triple faults, and empty-mesh material ownership. Ruff/diff checks passed.
- The final archive passed the repository release ZIP smoke, including offline
  runtime imports/lifetime checks. Official CPython **3.10.11** compiled **71
  first-party files**, and every extracted member matched the ZIP bytes.
- Actual **Blender 3.1.2 / Python 3.10.2** and **Blender 5.2.2 LTS / Python
  3.13.13** accepted the same isolated extracted final package. Each accepted all
  six text modes on the synthetic fixture and one private shop drawing with
  ready reports, no failed text, raster failures, or geometry issues. Labels used
  their recorded editable Text fallback where host-native labels are absent.
- Both hosts rejected pages 0/out-of-range/nonnumeric without scene changes and
  normalized reversed duplicate ranges. Real registered operators/native timers,
  driven through explicit headless event callbacks, accepted Esc/unfinished-page
  rollback, checkpoint resume with the same root, and the separate Cancel
  operator. No user GUI event delivery or pixel comparison is inferred.
- The native inferred-space fixture retained flags 532, exact original font
  binding, no invented painted glyph ID, and semantic spacing in every mode.
- The official Blender 3.1.2 portable archive was checksum verified against its
  published SHA-256 before use; its host installation was isolated under `C:/TMP`.

Detailed synthetic reports, sample native `.blend` files, and native acceptance
records for each host are in the package's `Validation` folder. Private drawing
reports remain under `C:/TMP` and no customer PDFs/names are included in this
repository record. The separate pre-existing portable Blender 4.4.3 still has a
missing DLL dependency. The current tested host is listed on the official
[Blender download page](https://www.blender.org/download/).

This is a Windows 64-bit bundle, with offline PyMuPDF 1.28.2 ABI3 and pure Python
FontTools 4.60.2 plus their upstream notices and source URLs. macOS/Linux binaries
are not included. Selected-fixture contract acceptance does not certify every
PDF, host release, machine, or pixel-for-pixel appearance. A costly individual
main-thread phase can finish before a queued cancel event is processed.
