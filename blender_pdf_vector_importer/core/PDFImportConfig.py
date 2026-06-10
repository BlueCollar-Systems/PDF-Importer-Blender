# -*- coding: utf-8 -*-
# DEPRECATED compatibility shim - use pdf_vector_importer.pdfcadcore.import_config instead.
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "PDFImportConfig is deprecated; import from pdf_vector_importer.pdfcadcore.import_config instead.",
    DeprecationWarning,
    stacklevel=2,
)

from pdf_vector_importer.pdfcadcore.import_config import *  # noqa: F401,F403
