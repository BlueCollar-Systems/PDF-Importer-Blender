"""Auto mode only rasterizes a page whose text, not whose vectors, is the content.

Replacing a page with a raster plane discards every vector on it, so the
text-cloud classifier must see the text dominate. A 48 x 36 in foundation sheet
with a concrete hatch (30,270 primitives, 324 labels) used to trip the heavy-page
rule on primitive count alone and came in as one 14400 x 10800 bitmap.
"""

from __future__ import annotations

from pathlib import Path
import sys
import types

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if "bpy" not in sys.modules:
    sys.modules["bpy"] = types.SimpleNamespace()
if not hasattr(sys.modules["bpy"], "app"):
    sys.modules["bpy"].app = types.SimpleNamespace(version=(4, 1, 0))
if not hasattr(sys.modules["bpy"], "types"):
    sys.modules["bpy"].types = types.SimpleNamespace(Collection=object, Object=object)
if "bmesh" not in sys.modules:
    sys.modules["bmesh"] = types.SimpleNamespace()

from pdf_vector_importer.bl_import_engine import (  # noqa: E402
    _looks_like_text_cloud_page,
    _text_item_profile,
)


def _items(specs):
    return [types.SimpleNamespace(text=text) for text in specs]


def _cad_sheet_labels(count: int):
    """The label mix measured on the S-505 foundation sheet: 40.5 % longish, 84 % alphabetic."""
    longish = round(count * 0.405)
    alpha_short = round(count * 0.84) - longish
    labels = [f"CONCRETE SLAB ON GRADE - REF PLAN {index}" for index in range(longish)]
    labels += [f"#{index % 9 + 4} TIE" for index in range(alpha_short)]
    labels += [f'{index % 12 + 1}"' for index in range(count - len(labels))]
    return _items(labels)


def test_the_foundation_sheet_profile_is_not_a_text_cloud():
    items = _cad_sheet_labels(324)
    profile = _text_item_profile(items)
    assert profile["total"] == 324
    long_ratio = profile["longish"] / profile["total"]
    assert 0.38 <= long_ratio <= 0.42  # the sheet measured 0.405
    # 324 labels against 30,270 primitives: the vectors are the content.
    assert _looks_like_text_cloud_page(30270, items) is False


def test_heavy_page_stays_vector_across_the_old_rule_b_range():
    """The old rule fired on primitive count and text count alone."""
    items = _cad_sheet_labels(600)
    for primitives in (12000, 31840, 120000):
        assert _looks_like_text_cloud_page(primitives, items) is False


def test_narrative_page_is_still_a_text_cloud():
    """Rule A: long multi-word runs that outnumber the vectors."""
    items = _items([f"Parcel {index} is subject to the recorded easement" for index in range(300)])
    profile = _text_item_profile(items)
    assert profile["longish"] == 300 and profile["alpha"] == 300
    assert _looks_like_text_cloud_page(100, items) is True


def test_heavy_text_dominated_page_with_numeric_labels_is_still_a_text_cloud():
    """Rule B: less alphabetic than rule A demands, but the text still dominates."""
    labels = []
    for index in range(40000):
        labels.append(
            f"{index} 1234567890 1234567" if index % 4 else f"Station {index} reference marker"
        )
    items = _items(labels)
    profile = _text_item_profile(items)
    total = profile["total"]
    assert profile["longish"] / total >= 0.20
    assert profile["alpha"] / total < 0.55  # rule A does not apply
    assert total / 12000 >= 2.5
    assert _looks_like_text_cloud_page(12000, items) is True


def test_thin_pages_are_never_text_clouds():
    assert _looks_like_text_cloud_page(30270, _items(["A", "B"])) is False
    assert _looks_like_text_cloud_page(0, []) is False
