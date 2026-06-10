# -*- coding: utf-8 -*-
# DEPRECATED compatibility shim - use pdf_vector_importer.pdfcadcore.validation instead.
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "PDFValidation is deprecated; import from pdf_vector_importer.pdfcadcore.validation instead.",
    DeprecationWarning,
    stacklevel=2,
)

from pdf_vector_importer.pdfcadcore.validation import *  # noqa: F401,F403
