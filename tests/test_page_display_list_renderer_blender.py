"""Item-scoped raster clips of one page render from one retained display list.

``Page.get_pixmap`` rebuilds the page display list on every call; 135 raster text
items on a 452k-path sheet rebuilt it 135 times.  The renderer keeps one list per
page and renders every clip from it — the same MuPDF path ``Page.get_pixmap`` takes
internally, so the pixels must be identical — and falls back to ``page.get_pixmap``
for pages that cannot provide a list.
"""

from __future__ import annotations

from pathlib import Path
import sys
import types

import pytest

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

from pdf_vector_importer import bl_import_engine  # noqa: E402

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - vendored name
    import fitz  # type: ignore


class _Pixmap:
    width = 160
    height = 40
    samples = b"non-empty-raster"

    def save(self, path):
        Path(path).write_bytes(b"verified-png")


class _DisplayList:
    def __init__(self):
        self.calls = []

    def get_pixmap(self, **kwargs):
        self.calls.append(kwargs)
        return _Pixmap()


class _ListPage:
    """A page that can hand out a display list, like a real PyMuPDF page."""

    def __init__(self):
        self.display_list = _DisplayList()
        self.list_calls = []
        self.page_calls = []

    def get_displaylist(self, **kwargs):
        self.list_calls.append(kwargs)
        return self.display_list

    def get_pixmap(self, **kwargs):
        self.page_calls.append(kwargs)
        return _Pixmap()


class _PlainPage:
    def __init__(self):
        self.page_calls = []

    def get_pixmap(self, **kwargs):
        self.page_calls.append(kwargs)
        return _Pixmap()


class _BrokenListPage(_PlainPage):
    def get_displaylist(self, **kwargs):
        raise RuntimeError("display list unavailable")


def _item(index: int):
    return types.SimpleNamespace(
        id=index,
        text=f"W{index}",
        source_bbox_pdf=(30.0 + index, 50.0, 140.0 + index, 68.0),
        bbox=(12.0, 24.0 + index, 52.0, 30.0 + index),
    )


class _RasterObject(dict):
    """What _create_image_plane hands back: a named mesh object with custom properties."""

    def __init__(self, name="PDF_Text_Raster"):
        super().__init__()
        self.name = name
        self.type = "MESH"
        self.data = types.SimpleNamespace(name=f"{name}_mesh", type="MESH")


def _render_three(page, renderer, tmp_path, monkeypatch):
    monkeypatch.setattr(
        bl_import_engine,
        "_create_image_plane",
        lambda placement, collection, z_offset_m=0.0: _RasterObject(),
    )
    config = types.SimpleNamespace(raster_dpi=288)
    results = []
    for index in range(3):
        results.append(
            bl_import_engine._render_text_item_raster(
                page,
                _item(index),
                object(),
                page_num=1,
                item_id=f"page:1:text:{index}",
                import_cfg=config,
                image_dir=str(tmp_path),
                renderer=renderer,
            )
        )
    return results


def test_one_display_list_serves_every_clip_on_the_page(tmp_path, monkeypatch):
    page = _ListPage()
    renderer = bl_import_engine._PageDisplayListRenderer(page)
    results = _render_three(page, renderer, tmp_path, monkeypatch)
    assert all(result is not None for result in results)
    assert page.list_calls == [{"annots": True}]
    assert page.page_calls == []
    assert len(page.display_list.calls) == 3
    for index, call in enumerate(page.display_list.calls):
        assert call["alpha"] is True
        assert tuple(call["matrix"]) == tuple(fitz.Matrix(4.0, 4.0))
        assert tuple(call["clip"]) == (30.0 + index, 50.0, 140.0 + index, 68.0)


def test_without_a_renderer_the_page_renders_as_before(tmp_path, monkeypatch):
    page = _ListPage()
    _render_three(page, None, tmp_path, monkeypatch)
    assert page.list_calls == []
    assert len(page.page_calls) == 3


def test_pages_without_a_display_list_fall_back_to_page_get_pixmap(tmp_path, monkeypatch):
    page = _PlainPage()
    renderer = bl_import_engine._PageDisplayListRenderer(page)
    _render_three(page, renderer, tmp_path, monkeypatch)
    assert len(page.page_calls) == 3

    broken = _BrokenListPage()
    renderer = bl_import_engine._PageDisplayListRenderer(broken)
    _render_three(broken, renderer, tmp_path, monkeypatch)
    assert len(broken.page_calls) == 3


def test_display_list_pixels_match_page_get_pixmap_exactly(tmp_path):
    pdf_path = tmp_path / "clips.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=200)
    for index in range(40):
        page.draw_line((10, 5 + index * 4.5), (290, 15 + index * 4.0), width=0.6)
    page.draw_rect((120, 80, 180, 120), color=(0, 0, 1), fill=(1, 0.5, 0), width=1.2)
    page.insert_text((60, 100), "3/8 PLATE", fontsize=11)
    page.insert_text((200, 60), "W12x26", fontsize=9)
    document.save(str(pdf_path))
    document.close()

    document = fitz.open(str(pdf_path))
    page = document[0]
    renderer = bl_import_engine._PageDisplayListRenderer(page)
    matrix = fitz.Matrix(300 / 72.0, 300 / 72.0)
    for clip in (fitz.Rect(55, 88, 130, 104), fitz.Rect(195, 50, 260, 64), fitz.Rect(100, 70, 200, 130)):
        expected = page.get_pixmap(matrix=matrix, clip=clip, alpha=True)
        actual = renderer.get_pixmap(matrix=matrix, clip=clip, alpha=True)
        assert (actual.width, actual.height, actual.n, actual.stride, actual.alpha) == (
            expected.width, expected.height, expected.n, expected.stride, expected.alpha
        )
        assert bytes(actual.samples) == bytes(expected.samples)
        assert actual.tobytes("png") == expected.tobytes("png")
    document.close()
