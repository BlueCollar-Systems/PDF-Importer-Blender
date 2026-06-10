# -*- coding: utf-8 -*-
# DEPRECATED compatibility shim - use pdf_vector_importer.pdfcadcore.geometry_cleanup instead.
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "PDFGeometryCleanup is deprecated; import from pdf_vector_importer.pdfcadcore.geometry_cleanup instead.",
    DeprecationWarning,
    stacklevel=2,
)

from pdf_vector_importer.pdfcadcore.geometry_cleanup import *  # noqa: F401,F403
