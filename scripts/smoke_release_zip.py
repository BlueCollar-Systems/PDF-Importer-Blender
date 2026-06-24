#!/usr/bin/env python3
"""Smoke-test the shipped Blender add-on ZIP."""
from __future__ import annotations

import argparse
import glob
import importlib
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


REQUIRED_MEMBERS = {
    "pdf_vector_importer/__init__.py",
    "pdf_vector_importer/bl_import_engine.py",
    "pdf_vector_importer/operators.py",
    "pdf_vector_importer/pdfcadcore/fitz_loader.py",
    "pdf_vector_importer/_vendored_pymupdf_extra.py",
    "pdf_vector_importer/lib/pymupdf/__init__.py",
    "pdf_vector_importer/lib/pymupdf/extra.py",
    "pdf_vector_importer/lib/pymupdf/mupdf.py",
    "pdf_vector_importer/lib/pymupdf/_extra.pyd",
    "pdf_vector_importer/lib/pymupdf/_mupdf.pyd",
    "pdf_vector_importer/lib/pymupdf/mupdfcpp64.dll",
}


def _resolve_zip(pattern: str) -> Path:
    matches = sorted(glob.glob(pattern))
    if not matches:
        raise SystemExit(f"No release ZIP matched {pattern!r}")
    return Path(matches[-1]).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_path", help="Release ZIP path or glob pattern")
    args = parser.parse_args()

    zip_path = _resolve_zip(args.zip_path)
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())
        missing = sorted(REQUIRED_MEMBERS - names)
        if missing:
            raise SystemExit(
                "Release ZIP is missing required add-on members: "
                + ", ".join(missing)
            )
        with tempfile.TemporaryDirectory(prefix="bl_addon_zip_") as tmp:
            zf.extractall(tmp)
            sys.path.insert(0, tmp)
            addon = importlib.import_module("pdf_vector_importer")
            version = addon.bl_info.get("version")
            if not isinstance(version, tuple) or len(version) != 3:
                raise SystemExit("pdf_vector_importer.bl_info version is invalid")
            if not callable(getattr(addon, "register", None)):
                raise SystemExit("pdf_vector_importer.register is missing")
            if sys.platform == "win32":
                lib_dir = str(Path(tmp) / "pdf_vector_importer" / "lib")
                code = (
                    "import sys; "
                    f"sys.path.insert(0, r'{tmp}'); "
                    "from pdf_vector_importer.pdfcadcore.fitz_loader import import_fitz; "
                    f"fitz = import_fitz(prefer_lib_dir=r'{lib_dir}'); "
                    "assert callable(getattr(fitz, 'open', None))"
                )
                proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
                if proc.returncode != 0:
                    raise SystemExit(
                        "Vendored PyMuPDF import failed from release ZIP: "
                        + (proc.stderr.strip() or proc.stdout.strip())
                    )

    print(f"Release ZIP smoke passed: {zip_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
