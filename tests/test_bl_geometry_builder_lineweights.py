from __future__ import annotations

import ast
import unittest
from pathlib import Path


GEOMETRY_BUILDER = (
    Path(__file__).resolve().parents[1]
    / "pdf_vector_importer"
    / "bl_geometry_builder.py"
)


class TestBlenderGeometryBuilderLineweights(unittest.TestCase):
    def test_lineweight_uses_radius_scale_and_thin_hairline_floor(self) -> None:
        source = GEOMETRY_BUILDER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assignments = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if isinstance(target, ast.Name):
                assignments[target.id] = ast.get_source_segment(source, node.value)

        self.assertEqual(assignments.get("_LINEWIDTH_SCALE"), "MM_TO_M * 0.5")
        self.assertEqual(assignments.get("_MIN_BEVEL_DEPTH"), "0.0000125")
        self.assertEqual(assignments.get("_DEFAULT_HAIRLINE_BEVEL_DEPTH"), "0.000025")
        # Tube radius helper remains; flat sheets gate it through _curve_bevel_depth.
        self.assertIn("def _line_bevel_depth(line_width", source)
        self.assertGreaterEqual(source.count("_curve_bevel_depth(line_width, use_tubes)"), 3)
        self.assertIn("def _use_paper_space_tubes", source)
        self.assertIn("use_line_tubes = _use_paper_space_tubes(config)", source)

    def test_default_preserves_visible_source_width_strokes(self) -> None:
        # Import helpers without bpy by exec'ing just the pure functions.
        source = GEOMETRY_BUILDER.read_text(encoding="utf-8")
        # Pull the two pure helpers via AST exec of a minimal stub.
        stub = "from typing import Optional\nMM_TO_M = 0.001\n"
        # Extract function sources
        import ast as _ast
        tree = _ast.parse(source)
        keep = []
        for node in tree.body:
            if isinstance(node, _ast.FunctionDef) and node.name in {
                "_line_bevel_depth",
                "_use_paper_space_tubes",
                "_curve_bevel_depth",
            }:
                keep.append(_ast.get_source_segment(source, node))
            if isinstance(node, _ast.Assign) and any(
                isinstance(t, _ast.Name)
                and t.id
                in {"_LINEWIDTH_SCALE", "_MIN_BEVEL_DEPTH", "_DEFAULT_HAIRLINE_BEVEL_DEPTH"}
                for t in node.targets
            ):
                keep.append(_ast.get_source_segment(source, node))
        ns = {}
        exec(stub + "\n\n".join(keep), ns, ns)
        self.assertTrue(ns["_use_paper_space_tubes"]({}))
        self.assertTrue(ns["_use_paper_space_tubes"]({"model3d_mode": "extrude"}))
        self.assertFalse(ns["_use_paper_space_tubes"]({"paper_space_tubes": False}))
        self.assertFalse(ns["_use_paper_space_tubes"]({"line_bevel": False}))
        self.assertTrue(ns["_use_paper_space_tubes"]({"paper_space_tubes": True}))
        self.assertEqual(ns["_curve_bevel_depth"](1.0, False), 0.0)
        self.assertGreater(ns["_curve_bevel_depth"](1.0, True), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
