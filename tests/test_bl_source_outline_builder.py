"""Bounded native-outline contracts; no Blender process is launched."""
from copy import deepcopy
from fractions import Fraction
import math
import sys
from types import SimpleNamespace as NS

import pytest

from pdf_vector_importer import bl_source_outline_builder as b


def polygon(points):
    return {"start": list(points[0]), "segments": [["L", list(p)] for p in points[1:]+points[:1]], "closed": True}


OUTER = polygon([(0, 0), (3, 0), (3, 3), (0, 3)])
INNER = polygon([(1, 1), (1, 2), (2, 2), (2, 1)])
LENS = {"start": [0, 0], "segments": [["C", [0, -1], [1, -1], [1, 0]],
                                     ["C", [1, 1], [0, 1], [0, 0]]], "closed": True}


def test_exact_simple_cubic_area_and_controls_are_not_tessellated():
    saved = deepcopy(LENS)
    result = b.qualify_contours([LENS], "nonzero")
    assert LENS == saved
    assert len(result["segments"][0]) == 2
    assert all(len(row) == 4 for row in result["segments"][0])
    assert math.isclose(float(result["area_m2"]), 1.2e-6, rel_tol=1e-15)


def test_piece_injectivity_rejects_backtracking_and_collapsed_loop():
    assert not b._injective_piece([(0, 0), (2, 0), (-1, 0), (1, 0)])
    assert not b._injective_piece([(0, 0), (2, 2), (-2, 2), (0, 0)])
    assert b._injective_piece([(0, 0), (0, 0), (1, 1), (1, 1)])


def test_single_piece_loop_cannot_hide_between_separated_hulls(monkeypatch):
    # Isolate the per-piece requirement: inter-piece separation alone cannot
    # certify a source cubic with a reversed derivative inside its interval.
    monkeypatch.setattr(b, "_pieces", lambda points: [points])
    contour = {"start": [0, 0], "closed": True,
               "segments": [["C", [2, 0], [-1, 0], [1, 0]],
                            ["L", [1, 1]], ["L", [0, 1]], ["L", [0, 0]]]}
    with pytest.raises(b.OutlineTopologyUnavailable, match="piece injectivity"):
        b.qualify_contours([contour], "nonzero")


def test_zero_endpoint_derivative_still_has_simple_source_boundary():
    contour = {"start": [0, 0], "closed": True,
               "segments": [["C", [0, 0], [1, 0], [1, 0]],
                            ["L", [1, 1]], ["L", [0, 1]], ["L", [0, 0]]]}
    assert b.qualify_contours([contour], "nonzero")["area_m2"] > 0


def test_unequal_adjacent_segments_use_exact_separating_direction():
    # The overall chord points downwards and rejects this ordinary corner;
    # an exact through-joint line still separates the complete two hulls.
    before = [(Fraction(3), Fraction(-1)), (Fraction(1), Fraction(-1, 8)), (Fraction(0), Fraction(0))]
    after = [(Fraction(0), Fraction(0)), (Fraction(0), Fraction(-100))]
    assert b._adjacent_separate(before, after, (Fraction(0), Fraction(0)))
    assert not b._adjacent_separate(before, list(reversed(before)), (Fraction(0), Fraction(0)))


@pytest.mark.parametrize("rule", ["nonzero", "evenodd"])
def test_hole_is_certified_from_complete_boundaries(rule):
    result = b.qualify_contours([OUTER, INNER], rule)
    assert result["depth"] == [0, 1] and result["holes"] == 1
    assert math.isclose(float(result["area_m2"]), 8e-6, rel_tol=1e-15)


def test_evenodd_same_winding_normalizes_without_changing_support():
    inner = polygon([(1, 1), (2, 1), (2, 2), (1, 2)])
    result = b.qualify_contours([OUTER, inner], "evenodd")
    assert result["reverse"] == [False, True]
    assert b._normalized_segments(result)[1][0] == list(reversed(result["segments"][1][-1]))
    with pytest.raises(b.OutlineVerificationError, match="redundant nonzero"):
        b.qualify_contours([OUTER, inner], "nonzero")


@pytest.mark.parametrize("other", [
    polygon([(0, 1), (1, 1), (1, 2), (0, 2)]),
    polygon([(2, 1), (4, 1), (4, 2), (2, 2)]),
    deepcopy(OUTER),
])
def test_touching_crossing_or_identical_contours_rejected(other):
    with pytest.raises(b.OutlineVerificationError):
        b.qualify_contours([OUTER, other], "evenodd")


def test_bowtie_and_open_or_nonfinite_source_fail_closed():
    for contour in [polygon([(0, 0), (2, 2), (0, 2), (2, 0)]),
                    dict(OUTER, closed=False),
                    dict(OUTER, start=[float("nan"), 0])]:
        with pytest.raises((b.OutlineVerificationError, ValueError)):
            b.qualify_contours([contour], "evenodd")


def test_float32_collapse_cannot_claim_native_geometry():
    narrow = polygon([(1e8, 0), (1e8+1e-4, 0), (1e8+1e-4, 1), (1e8, 1)])
    assert b.qualify_contours([narrow], "nonzero")["area_m2"] > 0
    with pytest.raises(b.OutlineVerificationError, match="collapsed"):
        b.qualify_contours([narrow], "nonzero", native=True)


def ring_mesh():
    vertices = [[0., 0., 0.], [3., 0., 0.], [3., 3., 0.], [0., 3., 0.],
                [1., 1., 0.], [2., 1., 0.], [2., 2., 0.], [1., 2., 0.]]
    triangles = [(0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
                 (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    expected = [[tuple(p[:2]) for p in vertices[:4]],
                [tuple(vertices[i][:2]) for i in (4, 7, 6, 5)]]
    return vertices, triangles, expected


def test_native_triangle_chain_proves_hole_and_area():
    proof = b.verify_mesh_ink(*ring_mesh())
    assert proof["exact_oriented_boundary_chain"] and proof["boundary_cycles"] == 2
    assert proof["area_m2"] == 8


@pytest.mark.parametrize("mutation", [
    lambda v, t: t.extend([(4, 5, 6), (4, 6, 7)]),
    lambda v, t: t.append(t[0]),
    lambda v, t: t.__setitem__(0, tuple(reversed(t[0]))),
    lambda v, t: v[0].__setitem__(0, .01),
    lambda v, t: v[0].__setitem__(2, .01),
    lambda v, t: v.append([99., 99., 0.]),
    lambda v, t: t.pop(),
])
def test_native_ink_mutations_cannot_pass_counts_or_bbox(mutation):
    v, t, expected = ring_mesh()
    mutation(v, t)
    with pytest.raises(b.OutlineVerificationError):
        b.verify_mesh_ink(v, t, expected)


class Point:
    def __init__(self):
        self.co = (0., 0., 0.)
        self.handle_left = self.handle_right = (0., 0., 0.)
        self.handle_left_type = self.handle_right_type = "FREE"
        self.tilt = 0.; self.radius = 1.

    def __setattr__(self, key, value):
        if key in {"co", "handle_left", "handle_right"}:
            value = tuple(b._f32(v) for v in value)
        super().__setattr__(key, value)


class Points(list):
    def add(self, count):
        self.extend(Point() for _ in range(count))


class Splines(list):
    def new(self, kind):
        value = NS(type=kind, bezier_points=Points([Point()]))
        self.append(value)
        return value


class Mesh:
    def __init__(self, coords):
        self.vertices = [NS(co=tuple(p)) for p in coords]
        self.loop_triangles = [NS(vertices=(0, i, i+1)) for i in range(1, len(coords)-1)]
        self.polygons = [NS(vertices=tuple(range(len(coords))), material_index=0)]
        self.edges = [NS(vertices=(i, (i+1) % len(coords))) for i in range(len(coords))]
        self.materials = []
        self.name = "test mesh"

    def calc_loop_triangles(self):
        pass

    def copy(self):
        return deepcopy(self)


class Object(dict):
    def __init__(self, name, data):
        super().__init__()
        self.name, self.data = name, data
        self.location = (0., 0., 0.)
        self.modifiers = []
        self.hide_render = self.hide_viewport = False
        self.clear_count = 0

    @property
    def type(self):
        return "MESH" if isinstance(self.data, Mesh) else "CURVE"

    @property
    def matrix_world(self):
        return [[1., 0., 0., self.location[0]], [0., 1., 0., self.location[1]],
                [0., 0., 1., self.location[2]], [0., 0., 0., 1.]]

    def to_mesh(self):
        mesh = Mesh([p.co for p in self.data.splines[0].bezier_points])
        mesh.materials = list(self.data.materials)
        return mesh

    def to_mesh_clear(self):
        self.clear_count += 1


class Registry(list):
    def __init__(self, factory):
        super().__init__(); self.factory = factory

    def new(self, *args):
        result = self.factory(*args); self.append(result); return result

    def get(self, name):
        return next((o for o in self if o.name == name), None)

    def remove(self, value, **_kwargs):
        self[:] = [row for row in self if row is not value]


@pytest.fixture
def host(monkeypatch):
    data = NS(curves=Registry(lambda name, kind: NS(name=name, splines=Splines(), materials=[])),
              objects=Registry(Object), materials=Registry(lambda name: NS(name=name)))
    fake = NS(data=data, context=NS(view_layer=NS(update=lambda: None)))
    monkeypatch.setitem(sys.modules, "bpy", fake)
    # Straight synthetic contours do not invoke Blender's cubic evaluator.
    monkeypatch.setattr(b, "_native_polygons", lambda rings: [[tuple(map(float, p[0])) for p in ring] for ring in rings])
    return fake


def record():
    result = {"item_id": "page:1:text:2", "page_number": 1, "source_text": "A",
              "pdf_sha256": "1"*64, "svg_sha256": "2"*64,
              "source_font_absence": {"reason": "no_exact_embedded_font_match", "proof_category": "source_font_absent_for_item", "source_page": 1},
              "placements": [{"index": 4, "glyph_id": 8, "unicode": "A", "definition_id": "font_1_8",
                              "definition_sha256": "3"*64, "color": [1., 0., 0.],
                              "fill_rule": "nonzero", "contours": [OUTER]}],
              "empty_placements": [], "clipped_placements": []}
    result["source_outline_sha256"] = b._digest(result)
    return result


def build(host, representation="glyphs"):
    return b.build_source_outlines(record(), NS(objects=NS(link=lambda obj: None)),
                                   representation=representation, requested="3d_text")


@pytest.mark.parametrize("representation,native", [("glyphs", "CURVE"), ("geometry", "MESH")])
def test_builder_delivers_requested_native_outline_with_honest_provenance(host, representation, native):
    outcome = build(host, representation)
    assert outcome.status == "delivered", outcome.evidence
    assert outcome.entity.type == native and outcome.entity["pdf_text_requested_mode"] == "3d_text"
    assert outcome.evidence["font_program_authenticity"] == "absent"
    assert not any("font_sha" in key or "font_packed" in key for key in outcome.entity)
    assert b.verify_source_outline_entity(outcome.entity)["native_ink"]["area_m2"] > 0
    assert outcome.owned_objects == (outcome.entity,)


@pytest.mark.parametrize("mutation", [
    lambda o: setattr(o.data.splines[0].bezier_points[0], "co", (.1, 0., 0.)),
    lambda o: setattr(o.data.splines[0], "use_cyclic_u", False),
    lambda o: setattr(o.data, "extrude", .001),
    lambda o: setattr(o.data, "offset", .001),
    lambda o: setattr(o.data, "bevel_object", object()),
    lambda o: setattr(o.data, "taper_object", object()),
    lambda o: setattr(o.data, "render_resolution_u", 1),
    lambda o: setattr(o.data.materials[0], "diffuse_color", (1., 0., 0., .5)),
    lambda o: setattr(o.data.materials[0], "use_nodes", True),
    lambda o: o.__setitem__("pdf_text_source", "B"),
    lambda o: setattr(o, "location", (0., 1., 0.)),
    lambda o: o.modifiers.append(NS(type="BEVEL")),
])
def test_final_native_readback_rejects_physical_and_semantic_corruption(host, mutation):
    outcome = build(host); assert outcome.status == "delivered", outcome.evidence
    mutation(outcome.entity)
    with pytest.raises(b.OutlineVerificationError):
        b.verify_source_outline_entity(outcome.entity)


def test_explicit_independent_stack_transform_required(host):
    outcome = build(host); obj = outcome.entity
    expected = b._matrix(obj.matrix_world); expected[1][3] += .25
    obj.location = (0., .25, 0.)
    with pytest.raises(b.OutlineVerificationError):
        b.verify_source_outline_entity(obj)
    assert b.verify_source_outline_entity(obj, expected_world_matrix=expected)["verified"]


def test_creation_failure_returns_every_owned_resource(host, monkeypatch):
    monkeypatch.setattr(b, "_mesh_readback", lambda *_: (_ for _ in ()).throw(ValueError("actual fill failed")))
    outcome = build(host)
    assert outcome.status == "failed" and outcome.evidence["detail"] == "actual fill failed"
    assert len(outcome.owned_objects) == 1 and len(outcome.owned_datablocks) == 2
    assert outcome.entity_ids == () and outcome.owned_objects[0].clear_count == 1


def test_empty_or_changed_source_record_cannot_create_placeholder(host):
    value = record(); value["placements"] = []; value["source_outline_sha256"] = b._digest({k:v for k,v in value.items() if k != "source_outline_sha256"})
    outcome = b.build_source_outlines(value, NS(), representation="glyphs", requested="glyphs")
    assert outcome.status == "failed" and not host.data.objects
    value = record(); value["source_text"] = "changed"
    outcome = b.build_source_outlines(value, NS(), representation="glyphs", requested="glyphs")
    assert outcome.status == "failed" and not host.data.objects


def test_exact_polyline_simplification_only():
    points = [(Fraction(x), Fraction(y)) for x, y in [(0, 0), (1, 0), (2, 0), (2, 2), (0, 2)]]
    assert len(b._simplify(points)) == 4
    points[1] = (Fraction(1), Fraction(1, 10**20))
    assert len(b._simplify(points)) == 5


def test_all_topology_is_qualified_before_first_native_allocation(host):
    value = record()
    bad = deepcopy(value["placements"][0]); bad["index"] = 5
    bad["contours"] = [OUTER, polygon([(2, 1), (4, 1), (4, 2), (2, 2)])]
    value["placements"].append(bad)
    value["source_outline_sha256"] = b._digest({k:v for k,v in value.items() if k != "source_outline_sha256"})
    result = b.build_source_outlines(value, NS(), representation="glyphs", requested="glyphs")
    assert result.status == "impossible" and result.reason == "source_outline_topology_unavailable_for_item"
    assert result.evidence["native_entities_created"] == 0 and not host.data.objects and not host.data.curves


def test_shared_record_owner_must_survive_final_readback(host):
    outcome = build(host); assert outcome.status == "delivered", outcome.evidence
    host.data.objects.clear()
    with pytest.raises(b.OutlineVerificationError, match="owner missing"):
        b.verify_source_outline_entity(outcome.entity)


@pytest.mark.parametrize("absence", [
    {"reason": "ambiguous_exact_embedded_font_match", "proof_category": "source_font_absent_for_item", "source_page": 1},
    {"reason": "no_exact_embedded_font_match", "proof_category": "source_font_absent_for_item", "source_page": 2},
    {"reason": "embedded_font_asset_build_failed", "proof_category": "source_specific_impossibility",
     "detail": "unsupported native font runtime", "source_page": 1, "source_xref": 5},
])
def test_absence_evidence_must_be_positive_and_bound_before_allocation(host, absence):
    value = record(); value["source_font_absence"] = absence
    value["source_outline_sha256"] = b._digest({k:v for k,v in value.items() if k != "source_outline_sha256"})
    result = b.build_source_outlines(value, NS(), representation="glyphs", requested="glyphs")
    assert result.status == "failed" and not host.data.objects and not host.data.curves


def test_creation_matrix_is_not_certified_from_observed_native_transform(host, monkeypatch):
    original = Object.matrix_world.fget
    def corrupted(obj):
        value = original(obj); value[0][3] = .125; return value
    monkeypatch.setattr(Object, "matrix_world", property(corrupted))
    result = build(host)
    assert result.status == "failed" and "requested frame" in result.evidence["detail"]
    assert len(result.owned_objects) == 1


def test_storage_bound_reports_only_exact_float32_conversion():
    bound = b._storage_bound([OUTER])
    assert bound["euclidean_upper_bound_m"] > 0
    exact = Fraction(bound["exact_l1_upper_bound"])
    assert Fraction(bound["euclidean_upper_bound_m"]) >= exact
    point = b._point(OUTER["segments"][0][1])
    assert sum(abs(Fraction(b._f32(x))-Fraction(x)) for x in point) <= exact
