# Agent guidance — PDF Vector Importer for Blender

This repository is [BlueCollar-Systems/PDF-Importer-Blender](https://github.com/BlueCollar-Systems/PDF-Importer-Blender). It imports PDF vector drawings into Blender 3.1+ on 64-bit Windows (`win_amd64`) as native Curves, Collections, and Materials.

## Worktrees, coordination, and pull requests

- Work in an isolated branch and worktree created from a fresh `origin/main`. Never edit the primary checkout `C:\1PDF-Importer-Blender` directly.
- Coordinate in the owner's Desktop `PDFTest Files\Q&A` folder:
  - Authoritative handoff: `MASTER_HANDOFF_ALL_7_REPOS_2026-10-27.md`
  - Active coordination log: `Achived Q&A\WORKER_STATUS_LOG.md` (append-only; claim before starting and log `DONE` when finished).
- Use pull requests for all changes to `main`. Include `[skip release]` in commit messages for documentation, test, or CI-only changes that do not ship a new add-on version.

## Standing product rules

- **Deliver, step down, report (owner ruling 2026-10-05):** One failing item never costs the page or the file. Deliver the page. When one item cannot be delivered in the requested mode, step that item down along the host's finite closest ladder (`REPRESENTATION_FIDELITY.md`) and record every step-down and any item left out in `import_report.json` and the summary.
- **No silent mode switching:** Requested text mode is the deliverable. Fix position, rotation, or scale bugs inside the requested mode rather than switching representations to hide a transform bug.
- **Host & distribution facts:**
  - Supported on 64-bit Windows (`win_amd64`) with Blender 3.1 or newer (Blender 5.2 LTS recommended). Each Blender major/minor version stores its own user add-ons and preferences separately.
  - Because Windows locks the bundled native `.pyd`/`.dll` while Blender runs, upgrading the add-on requires disabling it, closing all Blender windows, and installing the new ZIP.
  - Releases are published via manual `auto-release.yml` workflow dispatch, not on plain pushes to `main`.

## Local verification and locks

Run the lint and test suites locally before opening a pull request:

```powershell
python -m ruff check .
python -m pytest tests/ -v
```

- When launching a real Blender GUI or headless host check on the shared workstation, acquire the `native-cad` lock via `C:\BlueCollar-Shared\bc_lock.py` (`C:\BlueCollar-Shared\locks`, `--ram-gb 0`) and never terminate another worker's CAD process.
- Store validation artifacts and receipts under `G:\BlueCollar-Test-Results`, never on `D:`.

## Privacy gate (mandatory before commit and push)

Never commit or display customer PDFs, CAD drawings, job numbers, piece marks, company names, or personal paths. Use fictional identifiers (such as `D042-10` or `SYNTHETIC-PRIVATE-DRAWING-ID`) in tests and documentation.

Run the private privacy guard before every commit and push:

```powershell
py -3 -B -I C:\BlueCollar-Private\hooks\privacy_guard.py staged
py -3 -B -I C:\BlueCollar-Private\hooks\privacy_guard.py range origin/main..HEAD
```
