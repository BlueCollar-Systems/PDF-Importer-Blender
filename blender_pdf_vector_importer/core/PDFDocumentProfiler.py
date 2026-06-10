# -*- coding: utf-8 -*-
# DEPRECATED compatibility shim - use pdf_vector_importer.pdfcadcore.document_profiler instead.
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "PDFDocumentProfiler is deprecated; import from pdf_vector_importer.pdfcadcore.document_profiler instead.",
    DeprecationWarning,
    stacklevel=2,
)

from pdf_vector_importer.pdfcadcore.document_profiler import *  # noqa: F401,F403
