"""Shared release check for the pinned runtime and repeated text-trace disposal."""
from pathlib import Path
import subprocess

PYMUPDF_VERSION = "1.28.2"
SMOKE_MARKER = "OK PyMuPDF " + PYMUPDF_VERSION


def probe_code(lib_dir: Path, addon_root: Path | None = None) -> str:
    lines = ["import pathlib,sys", f"lib = pathlib.Path({str(lib_dir)!r}).resolve()",
             "sys.path.insert(0,str(lib))"]
    if addon_root is None:
        lines.append("import pymupdf as fitz")
    else:
        lines.extend([f"sys.path.insert(0,{str(addon_root)!r})",
                      "from pdf_vector_importer.pdfcadcore.fitz_loader import import_fitz",
                      "fitz = import_fitz(prefer_lib_dir=str(lib))"])
    lines.extend([
        f"assert (getattr(fitz,'__version__','') or getattr(fitz,'VersionBind','')) == {PYMUPDF_VERSION!r}",
        "assert pathlib.Path(fitz.__file__).resolve().is_relative_to(lib/'pymupdf')",
        "with fitz.open() as doc:",
        "    page = doc.new_page()",
        "    for row in range(10):",
        "        page.insert_text((72,72+20*row), 'Release runtime span %d' % row)",
        "    for _ in range(2048):",
        "        spans = page.get_texttrace()",
        "        assert len(spans) == 10",
        "        del spans",
        f"print({SMOKE_MARKER!r})",
    ])
    return "\n".join(lines)


def verify_runtime(python_exe: str | Path, lib_dir: Path, addon_root: Path | None = None) -> None:
    """The marker alone is insufficient: finalization must exit successfully."""
    try:
        result = subprocess.run(
            [str(python_exe), "-I", "-B", "-c", probe_code(lib_dir, addon_root)],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Vendored PyMuPDF runtime lifetime smoke could not finish") from error
    if result.returncode != 0 or result.stdout.strip() != SMOKE_MARKER:
        raise RuntimeError(
            "Vendored PyMuPDF 1.28.2 runtime lifetime smoke failed: "
            + (result.stderr.strip() or result.stdout.strip() or str(result.returncode))
        )
