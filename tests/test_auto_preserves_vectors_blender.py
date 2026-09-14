import ast
from pathlib import Path
import pytest

SOURCE = Path(__file__).resolve().parents[1] / "pdf_vector_importer/bl_import_engine.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
NODE = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_should_rasterize_auto_page")
NS = {}
exec(compile(ast.Module(body=[NODE], type_ignores=[]), str(SOURCE), "exec"), NS)

@pytest.mark.parametrize("kind,count,expected", [
    ("glyph_flood", 32000, False),
    ("fill_art", 32000, False),
    ("vectors", 20, False),
    ("text_only", 0, False),
    ("raster_candidate", 0, True),
    ("raster_candidate", 1, False),
    ("unknown", 0, False),
])
def test_auto_keeps_existing_vector_content(kind, count, expected):
    assert NS["_should_rasterize_auto_page"]({"type": kind, "drawing_count": count}) is expected

def test_auto_rasterization_requires_affirmative_no_vector_count():
    assert not NS["_should_rasterize_auto_page"]({"type": "raster_candidate"})

def test_long_notes_do_not_rasterize_a_dense_vector_drawing():
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_looks_like_text_cloud_page")
    ns = {"_text_item_profile": lambda items: {"total": 324, "longish": 120, "alpha": 280}}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), ns)
    assert not ns["_looks_like_text_cloud_page"](32000, [])
