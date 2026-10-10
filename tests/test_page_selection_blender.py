"""A mistyped page selection must never expand into a whole-document import."""
import ast
import re
from pathlib import Path
from typing import List

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "pdf_vector_importer/bl_import_engine.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
NODE = next(node for node in TREE.body if isinstance(node, ast.FunctionDef)
            and node.name == "_parse_pages")
NAMESPACE = {"re": re, "List": List}
exec(compile(ast.Module(body=[NODE], type_ignores=[]), str(SOURCE), "exec"), NAMESPACE)
parse_pages = NAMESPACE["_parse_pages"]


@pytest.mark.parametrize("spec", ["0", "9", "banana", "1,banana", "1-9", "0-2", "1,,2"])
def test_invalid_selection_is_rejected(spec):
    with pytest.raises(ValueError):
        parse_pages(spec, 4)


@pytest.mark.parametrize("spec,expected", [
    ("all", [0, 1, 2, 3]), ("", [0, 1, 2, 3]), ("*", [0, 1, 2, 3]),
    ("1,3-4,3", [0, 2, 3]), ("4-2", [1, 2, 3]), (" 2 - 3 ", [1, 2]),
])
def test_valid_selection_is_normalized(spec, expected):
    assert parse_pages(spec, 4) == expected


def test_huge_out_of_range_selection_is_rejected_before_expansion(monkeypatch):
    def unexpected_range(*_args):
        raise AssertionError("validate endpoints before allocating the range")

    monkeypatch.setitem(NAMESPACE, "range", unexpected_range)
    with pytest.raises(ValueError):
        parse_pages("1-999999999999", 4)
