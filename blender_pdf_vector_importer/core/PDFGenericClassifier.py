# -*- coding: utf-8 -*-
# DEPRECATED compatibility shim - use pdf_vector_importer.pdfcadcore.generic_classifier instead.
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "PDFGenericClassifier is deprecated; import from pdf_vector_importer.pdfcadcore.generic_classifier instead.",
    DeprecationWarning,
    stacklevel=2,
)

from pdf_vector_importer.pdfcadcore.generic_classifier import *  # noqa: F401,F403
