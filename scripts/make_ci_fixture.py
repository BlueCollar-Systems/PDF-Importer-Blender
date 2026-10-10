#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# make_ci_fixture.py - Fictional PDF fixtures for the real-Blender CI matrix
# Copyright (c) 2024-2026 BlueCollar Systems - BUILT. NOT BOUGHT.
# License: MIT
"""Write a small, fictional two-sheet PDF for headless Blender import checks.

Everything on the sheets is made up (job D042, marks EX###); no customer data.
Nothing is committed: the PDF is generated on the fly (``*.pdf`` is ignored).

Variants:

``standard`` (default) - what every Blender leg must import completely:
    sheet 1  Letter portrait (8.5 x 11 in): border, lines, a circle, a filled
             box, two text lines and one embedded picture;
    sheet 2  Tabloid landscape (17 x 11 in): border, a line, a text line and
             one text line whose letters cross the bottom edge of the sheet.
    All text is Helvetica that is NOT embedded in the PDF (a base-14 font),
    which is how many CAD and office programs write simple text.

``edge-far`` - the known "one edge-crossing text line stops the import" case
    (BL-1010-keep-every-sheet): a very tall first sheet pushes sheet 2 about
    5.9 m down the stack, and sheet 2 has text crossing its top edge (same
    non-embedded Helvetica). On origin/main 6b8e93a (Blender 5.2.2) sheet 2 is
    rolled back by the stacked text re-check ("Native text viewport changed the
    visible source ink area") and only sheet 1 stays, so CI runs it as an
    expected failure until that fix lands.

Usage:
    python scripts/make_ci_fixture.py OUT.pdf [--variant standard|edge-far]

Needs PyMuPDF (``pip install PyMuPDF`` or the copy vendored in
``pdf_vector_importer/lib``). Prints one line describing what it wrote.
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_VENDORED = os.path.join(os.path.dirname(_HERE), "pdf_vector_importer", "lib")

try:
    import pymupdf
except ImportError:  # fall back to the copy shipped inside the add-on
    if os.path.isdir(_VENDORED) and _VENDORED not in sys.path:
        sys.path.append(_VENDORED)
    import pymupdf

LETTER = (8.5 * 72, 11 * 72)
TABLOID_LANDSCAPE = (17 * 72, 11 * 72)
# PDF page sizes stop at 200 in (14400 pt); 14000 pt reproduces the 5.9 m move.
TALL_SHEET = (17 * 72, 14000)

STANDARD_PAGES = 2
EDGE_FAR_PAGES = 2


def _picture_png(width: int = 48, height: int = 32) -> bytes:
    """A small colour-gradient picture, made in memory (no image files)."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    for y in range(height):
        for x in range(width):
            pix.set_pixel(x, y, (int(255 * x / (width - 1)), int(255 * y / (height - 1)), 96))
    return pix.tobytes("png")


def _border(page, inset: float = 36.0) -> None:
    rect = page.rect
    page.draw_rect(
        pymupdf.Rect(inset, inset, rect.width - inset, rect.height - inset),
        color=(0, 0, 0),
        width=1.0,
    )


def build_standard(path: str) -> dict:
    doc = pymupdf.open()

    w, h = LETTER
    p1 = doc.new_page(width=w, height=h)
    _border(p1)
    p1.draw_line((72, 150), (540, 150), color=(0, 0, 0), width=0.5)
    p1.draw_line((72, 160), (300, 400), color=(0.8, 0, 0), width=0.75)
    p1.draw_circle((420, 320), 40, color=(0, 0, 0.8), width=0.5)
    p1.draw_rect(pymupdf.Rect(72, 600, 200, 660), color=(0, 0, 0), fill=(0.85, 0.85, 0.85), width=0.5)
    # fontname="helv" writes base-14 Helvetica without embedding the font program.
    p1.insert_text((72, 120), "JOB D042 SHEET 1 MARK EX101", fontname="helv", fontsize=14)
    p1.insert_text((72, 140), "PL 3/8 x 6 x 1'-2 1/2\"  (2) REQ'D", fontname="helv", fontsize=10)
    p1.insert_image(pymupdf.Rect(300, 560, 444, 656), stream=_picture_png())

    w, h = TABLOID_LANDSCAPE
    p2 = doc.new_page(width=w, height=h)
    _border(p2)
    p2.draw_line((100, 300), (900, 300), color=(0, 0, 0), width=0.5)
    p2.insert_text((100, 200), "JOB D042 SHEET 2 MARK EX102", fontname="helv", fontsize=14)
    # Baseline 3 pt below the sheet: the letters cross the bottom edge.
    p2.insert_text((400, h + 3), "EDGE BOTTOM EX102", fontname="helv", fontsize=10)

    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return {"variant": "standard", "pages": STANDARD_PAGES}


def build_edge_far(path: str) -> dict:
    doc = pymupdf.open()
    for number, (w, h), edge in ((1, TALL_SHEET, False), (2, TABLOID_LANDSCAPE, True)):
        page = doc.new_page(width=w, height=h)
        _border(page)
        page.insert_text((100, 200), f"JOB D042 SHEET {number} MARK EX{100 + number}", fontname="helv", fontsize=14)
        if edge:
            # Baseline 4 pt into the sheet: the letters cross the top edge.
            page.insert_text((400, 4), f"EDGE TOP EX{100 + number}", fontname="helv", fontsize=10)

    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return {"variant": "edge-far", "pages": EDGE_FAR_PAGES}


BUILDERS = {"standard": build_standard, "edge-far": build_edge_far}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", help="PDF file to write")
    parser.add_argument("--variant", choices=sorted(BUILDERS), default="standard")
    args = parser.parse_args(argv)

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    info = BUILDERS[args.variant](out)

    check = pymupdf.open(out)
    sizes = [(round(pg.rect.width / 72, 2), round(pg.rect.height / 72, 2)) for pg in check]
    images = sum(len(pg.get_images(full=True)) for pg in check)
    page_count = check.page_count
    check.close()
    if page_count != info["pages"]:
        print(f"make_ci_fixture: wrote {page_count} pages, expected {info['pages']}", file=sys.stderr)
        return 1
    print(f"CI_FIXTURE {info['variant']} pages={page_count} sizes_in={sizes} images={images} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
