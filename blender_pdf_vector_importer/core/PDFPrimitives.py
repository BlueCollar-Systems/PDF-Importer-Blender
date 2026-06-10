# -*- coding: utf-8 -*-
# DEPRECATED compatibility shim - use pdf_vector_importer.pdfcadcore.primitives instead.
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "PDFPrimitives is deprecated; import from pdf_vector_importer.pdfcadcore.primitives instead.",
    DeprecationWarning,
    stacklevel=2,
)

from pdf_vector_importer.pdfcadcore.primitives import *  # noqa: F401,F403
