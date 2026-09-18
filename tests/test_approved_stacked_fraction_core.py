from __future__ import annotations

import hashlib
import inspect
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = REPO_ROOT / "pdf_vector_importer" / "pdfcadcore"
sys.path.insert(0, str(REPO_ROOT / "pdf_vector_importer"))

from pdfcadcore import primitive_extractor  # noqa: E402


# September 12: drawing-list reuse and FIFO glyph queues were reviewed with
# full-field equivalence on ten drawing pages plus fraction/layout regressions.
# No fraction predicate, scale, source-character proof, or renderer changed.
# Previous reviewed hash: ab6d03e38066c42214e6272f503a0aef5bf53d50fbd0c8aafa91cc0c8b3e6538.
# September 16: extended vector clip fills retain grouped contour metadata.
# Overlapping source strokes also keep their polygon vertices, avoiding circle-fit
# crescents around exact masks. Text/fraction/source-character proofs are unchanged.
# September 17: additive raw paint RGB/alpha/order survives beside unchanged
# legacy composite colors; finite integer order validation rejects invented order.
# September 17: preserve original MuPDF affine character quads; synthesized
# recovered font-metric shear is rejected by anisotropic/sheared source tests.
# September 17: a single filled contour with an exactly repeated endpoint is
# closed even without h; no tolerance-based closure or compound-hole changes.
# This is the reviewed integration output: current main plus the fraction-core
# port. It deliberately does not claim that these combined bytes were approved
# independently before the merge review.
REVIEWED_COMBINED_SUCCESSOR_SHA256 = (
    "f14df4ec93e6e5d8a1d24b5ac86e2a906c6a51da72a0d185b0e321d8a44258f9"
)


def test_shared_core_matches_reviewed_combined_successor() -> None:
    raw = (CORE_ROOT / "primitive_extractor.py").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest() == REVIEWED_COMBINED_SUCCESSOR_SHA256


def test_obsolete_stacked_fraction_scale_clamp_is_absent() -> None:
    source = inspect.getsource(primitive_extractor)
    assert "_FRAC_STACKED_SCALE" not in source
    assert "font_size * 0.6" not in source
