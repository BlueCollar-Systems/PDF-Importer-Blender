#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# preflight_check.py — one-click pre-import guidance for Blender PDF Importer
# Copyright (c) 2024-2026 BlueCollar Systems — BUILT. NOT BOUGHT.
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "pdf_vector_importer"))

from pdfcadcore.preflight_copy import preflight_paragraph  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Print Blender PDF Importer pre-import guidance")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Print pre-import guidance and exit (alias for default behavior)",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Also print PyMuPDF dependency diagnostics when Blender Python is available",
    )
    args = parser.parse_args()

    print(preflight_paragraph("blender"))

    if args.diagnostics:
        try:
            from pdf_vector_importer.dependency_manager import print_diagnostics

            print_diagnostics()
        except ImportError as exc:
            print(f"[PDF Vector Importer] Diagnostics unavailable outside Blender: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
