# -*- coding: utf-8 -*-
# DEPRECATED compatibility shim - use pdf_vector_importer.pdfcadcore.regions instead.
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "PDFRegions is deprecated; import from pdf_vector_importer.pdfcadcore.regions instead.",
    DeprecationWarning,
    stacklevel=2,
)

from pdf_vector_importer.pdfcadcore.regions import *  # noqa: F401,F403
