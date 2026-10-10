"""A fresh install opens drawings in the PDF's own colors on white paper.

Locks in the "Source Accurate" defaults so the grey/cyan preview look can
never ship as the default again. Pure Python (reads the source with ast),
so it runs in CI and in the release build without Blender.
"""
import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "pdf_vector_importer"


def _class(module: str, name: str) -> ast.ClassDef:
    tree = ast.parse((PACKAGE / module).read_text(encoding="utf-8"))
    found = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == name]
    assert len(found) == 1, f"{module} must define exactly one {name}"
    return found[0]


def _property_default(cls: ast.ClassDef, prop: str):
    for node in cls.body:
        if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and node.target.id == prop):
            call = node.annotation if isinstance(node.annotation, ast.Call) else node.value
            assert isinstance(call, ast.Call), f"{cls.name}.{prop} must be a Blender property"
            defaults = [kw.value for kw in call.keywords if kw.arg == "default"]
            assert len(defaults) == 1, f"{cls.name}.{prop} must name its default"
            return ast.literal_eval(defaults[0])
    pytest.fail(f"{cls.name} has no property {prop}")


@pytest.mark.parametrize("module,cls,prop,expected", [
    ("preferences.py", "PDFVectorImporterPreferences", "default_visual_style", "source"),
    ("operators.py", "IMPORT_OT_pdf_vector", "visual_style", "source"),
    ("operators.py", "IMPORT_OT_pdf_vector", "white_page_background", True),
])
def test_source_accurate_is_the_default_look(module, cls, prop, expected):
    assert _property_default(_class(module, cls), prop) == expected


def test_import_dialog_takes_its_look_from_the_preference():
    invoke = next(node for node in _class("operators.py", "IMPORT_OT_pdf_vector").body
                  if isinstance(node, ast.FunctionDef) and node.name == "invoke")
    assignments = [
        node for node in ast.walk(invoke)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Attribute) and target.attr == "visual_style"
                and isinstance(target.value, ast.Name) and target.value.id == "self"
                for target in node.targets)
    ]
    assert [ast.unparse(node.value) for node in assignments] == ["prefs.default_visual_style"]
