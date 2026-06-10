# -*- coding: utf-8 -*-
# DEPRECATED compatibility shim - use pdf_vector_importer.pdfcadcore.primitive_extractor instead.
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "PDFPrimitiveExtractor is deprecated; import from pdf_vector_importer.pdfcadcore.primitive_extractor instead.",
    DeprecationWarning,
    stacklevel=2,
)

from pdf_vector_importer.pdfcadcore.primitive_extractor import *  # noqa: F401,F403
