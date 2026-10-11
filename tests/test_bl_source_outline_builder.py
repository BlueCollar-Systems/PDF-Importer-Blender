"""Bounded native-outline contracts; no Blender process is launched."""
from copy import deepcopy
from fractions import Fraction
import json
import math
import sys
from types import SimpleNamespace as NS

import pytest

from pdf_vector_importer import bl_source_outline_builder as b


@pytest.fixture(autouse=True)
def clear_qualification_cache():
    b._clear_qualification_cache()
    yield
    b._clear_qualification_cache()


@pytest.fixture(autouse=True)
def full_audit_proofs(monkeypatch):
    # These contracts pin the exact build-time proofs, which run in audit mode.
    monkeypatch.setenv("BC_PDF_AUDIT", "1")


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


class Sockets(dict):
    def __iter__(self):
        return iter(self.values())


class Nodes(list):
    def new(self, *, type):
        names = (["Color", "Strength"] if type == "ShaderNodeEmission"
                 else ["Surface", "Volume", "Displacement", "Thickness"])
        node = NS(bl_idname=type, mute=False, is_active_output=False, target="ALL")
        node.inputs = Sockets({name: NS(node=node, name=name, is_linked=False,
                                       default_value=(0., 0., 0.) if name == "Displacement" else 0.)
                               for name in names})
        node.outputs = Sockets({"Emission": NS(node=node, name="Emission", is_linked=False)})
        self.append(node)
        return node


class Links(list):
    def new(self, source, target):
        source.is_linked = target.is_linked = True
        self.append(NS(from_node=source.node, to_node=target.node, from_socket=source,
                       to_socket=target, is_valid=True, is_muted=False))


def material(name):
    return NS(name=name, node_tree=NS(nodes=Nodes(), links=Links()))


@pytest.fixture
def host(monkeypatch):
    data = NS(curves=Registry(lambda name, kind: NS(name=name, splines=Splines(), materials=[])),
              objects=Registry(Object), materials=Registry(material))
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
    lambda o: setattr(o.data.materials[0], "use_nodes", False),
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


def test_expanded_native_polygon_has_separate_finite_bound():
    ring = [(math.cos(2*math.pi*i/548), math.sin(2*math.pi*i/548)) for i in range(548)]
    with pytest.raises(b.OutlineTopologyUnavailable, match="segment count"):
        b.qualify_contours([polygon(ring)], "nonzero")
    assert b._polygon_qualification([ring], [0])["depth"] == [0]
    with pytest.raises(b.OutlineTopologyUnavailable, match="native polygon work bound"):
        b._polygon_qualification([[(0, 0)]*(b.MAX_PIECES+1)], [0])
    with pytest.raises(b.OutlineTopologyUnavailable, match="counter nesting"):
        b._polygon_qualification([ring], [1])


def tee_mesh():
    # A real triangulation pattern: two exactly collinear triangles connect
    # differently subdivided edges along a T's crossbar and upright.
    vertices = [[2., 2., 0.], [3., 2., 0.], [3., 3., 0.], [0., 3., 0.],
                [0., 2., 0.], [1., 2., 0.], [1., 0., 0.], [2., 0., 0.]]
    triangles = [(4, 2, 3), (4, 1, 2), (5, 1, 4), (6, 0, 5), (0, 1, 5), (6, 7, 0)]
    return vertices, triangles, [[tuple(p[:2]) for p in vertices]]


def test_exact_zero_triangles_preserve_complete_positive_fill_chain():
    vertices, triangles, expected = tee_mesh()
    original = deepcopy((vertices, triangles, expected))
    proof = b.verify_mesh_ink(vertices, triangles, expected)
    assert (vertices, triangles, expected) == original
    assert proof["positive_triangles"] == 4 and proof["exact_zero_area_triangles"] == 2
    assert proof["area_m2"] == 5 and proof["boundary_cycles"] == 1
    assert proof["degenerate_segments_in_positive_fill"]
    assert proof["mesh_sha256"] == b._digest({"vertices": vertices, "triangles": triangles})


def test_zero_only_vertex_must_be_covered_by_complete_positive_support():
    v, t, expected = tee_mesh()
    v.append([1.5, 2., 0.]); t.append((4, 8, 1))
    assert b.verify_mesh_ink(v, t, expected)["exact_zero_area_triangles"] == 3
    v[-1] = [4., 2., 0.]
    with pytest.raises(b.OutlineVerificationError, match="zero-area native edge"):
        b.verify_mesh_ink(v, t, expected)


def test_zero_segment_cannot_bridge_counter_despite_inside_endpoints():
    v, t, expected = ring_mesh()
    v.extend([[.5, 1.5, 0.], [2.5, 1.5, 0.], [1.5, 1.5, 0.]])
    t.append((8, 9, 10))
    with pytest.raises(b.OutlineVerificationError, match="zero-area native edge"):
        b.verify_mesh_ink(v, t, expected)


@pytest.mark.parametrize("mutation", [
    lambda v, t: t.append(t[0]),
    lambda v, t: t.pop(0),
    lambda v, t: v[5].__setitem__(1, math.nextafter(2., 3.)),
    lambda v, t: v[5].__setitem__(2, math.nextafter(0., 1.)),
    lambda v, t: t.__setitem__(2, (5, 5, 4)),
    lambda v, t: t.__setitem__(2, (5, 1, len(v))),
])
def test_degenerate_accounting_keeps_all_physical_negative_gates(mutation):
    v, t, expected = tee_mesh(); mutation(v, t)
    with pytest.raises(b.OutlineVerificationError):
        b.verify_mesh_ink(v, t, expected)


def test_geometry_proofs_fail_closed_at_candidate_work_limit(monkeypatch):
    monkeypatch.setattr(b, "MAX_GEOMETRY_CANDIDATES", 1)
    with pytest.raises(b.OutlineVerificationError, match="candidate work bound"):
        b.verify_mesh_ink(*tee_mesh())
    with pytest.raises(b.OutlineTopologyUnavailable, match="candidate work bound"):
        b.qualify_contours([OUTER], "nonzero")


@pytest.mark.parametrize("mutation", [
    lambda m: setattr(m.node_tree.nodes[0].inputs["Color"], "default_value", (0., 0., 0., 1.)),
    lambda m: setattr(m.node_tree.nodes[0].inputs["Color"], "default_value", (1., 0., 0., .5)),
    lambda m: setattr(m.node_tree.nodes[0].inputs["Strength"], "default_value", math.nextafter(1., 2.)),
    lambda m: setattr(m.node_tree.nodes[0].inputs["Color"], "is_linked", True),
    lambda m: setattr(m.node_tree.nodes[0], "bl_idname", "ShaderNodeBsdfPrincipled"),
    lambda m: setattr(m.node_tree.nodes[0], "mute", True),
    lambda m: setattr(m.node_tree.nodes[1], "is_active_output", False),
    lambda m: setattr(m.node_tree.nodes[1], "target", "CYCLES"),
    lambda m: setattr(m.node_tree.nodes[1].inputs["Displacement"], "default_value", (.001, 0., 0.)),
    lambda m: setattr(m.node_tree.nodes[1].inputs["Volume"], "is_linked", True),
    lambda m: setattr(m.node_tree.links[0], "is_muted", True),
    lambda m: setattr(m.node_tree.links[0], "is_valid", False),
    lambda m: setattr(m.node_tree.links[0], "to_socket", m.node_tree.nodes[1].inputs["Volume"]),
    lambda m: m.node_tree.nodes.append(NS(bl_idname="ShaderNodeTexImage")),
    lambda m: setattr(m.node_tree, "animation_data", object()),
    lambda m: setattr(m, "animation_data", object()),
])
def test_exact_constant_emission_readback_rejects_graph_changes(host, mutation):
    result = build(host); assert result.status == "delivered", result.evidence
    assert b.verify_source_outline_entity(result.entity)["material"]["shader"] == "constant_emission"
    mutation(result.entity.data.materials[0])
    with pytest.raises(b.OutlineVerificationError):
        b.verify_source_outline_entity(result.entity)


def test_authenticated_optional_page_ledger_is_returned_for_independent_owner_check(host):
    value = record(); value["source_page_ledger"] = {"schema": "test", "ledger_sha256": "4"*64}
    value["source_outline_sha256"] = b._digest({k: v for k, v in value.items() if k != "source_outline_sha256"})
    result = b.build_source_outlines(value, NS(objects=NS(link=lambda obj: None)), representation="glyphs", requested="glyphs")
    assert result.status == "delivered", result.evidence
    assert b.verify_source_outline_entity(result.entity)["source_page_ledger"] == value["source_page_ledger"]


def qualification_counter(monkeypatch):
    calls = []
    original = b._qualify_contours
    def observed(*args, **kwargs):
        calls.append((deepcopy(args), dict(kwargs)))
        return original(*args, **kwargs)
    monkeypatch.setattr(b, "_qualify_contours", observed)
    return calls


def test_cached_math_cold_warm_fraction_parity_and_return_isolation(monkeypatch):
    calls = qualification_counter(monkeypatch)
    contours = deepcopy([OUTER, INNER])
    cold = b.qualify_contours(contours, "evenodd")
    original = deepcopy(cold)
    cold["segments"][0].clear()
    cold["depth"][0] = 99
    warm = b.qualify_contours(contours, "evenodd")
    assert warm == original and isinstance(warm["area_m2"], Fraction)
    warm["segments"].clear()
    assert b.qualify_contours(contours, "evenodd") == original
    assert len(calls) == 1


def test_cold_key_and_math_use_same_detached_snapshot(monkeypatch):
    contours = deepcopy([OUTER])
    expected = b._qualify_contours(deepcopy(contours), "nonzero", native=False,
                                   coordinates_in_metres=False, segment_limit=512)
    original = b._qualify_contours
    def mutate_caller(detached, *args, **kwargs):
        assert detached is not contours and detached[0] is not contours[0]
        contours[0]["closed"] = False
        return original(detached, *args, **kwargs)
    monkeypatch.setattr(b, "_qualify_contours", mutate_caller)
    assert b.qualify_contours(contours, "nonzero") == expected
    with pytest.raises(b.OutlineVerificationError, match="not explicitly closed"):
        b.qualify_contours(contours, "nonzero")


@pytest.mark.parametrize("change", ["controls", "fill", "native", "units", "segment_limit",
                                   "schema", "resolution", "pieces", "candidates"])
def test_every_math_input_or_domain_change_misses_cache(monkeypatch, change):
    calls = qualification_counter(monkeypatch)
    contours, rule = deepcopy([OUTER]), "nonzero"
    options = dict(native=False, coordinates_in_metres=False, segment_limit=512)
    b._cached_qualify_contours(contours, rule, **options)
    if change == "controls": contours = [polygon([(0, 0), (4, 0), (4, 3), (0, 3)])]
    elif change == "fill": rule = "evenodd"
    elif change == "native": options["native"] = True
    elif change == "units": options["coordinates_in_metres"] = True
    elif change == "segment_limit": options["segment_limit"] = 513
    elif change == "schema": monkeypatch.setattr(b, "_QUALIFICATION_SCHEMA", "different-algorithm")
    elif change == "resolution": monkeypatch.setattr(b, "RESOLUTION", b.RESOLUTION+1)
    elif change == "pieces": monkeypatch.setattr(b, "MAX_PIECES", b.MAX_PIECES+1)
    else: monkeypatch.setattr(b, "MAX_GEOMETRY_CANDIDATES", b.MAX_GEOMETRY_CANDIDATES+1)
    b._cached_qualify_contours(contours, rule, **options)
    assert len(calls) == 2


def test_exact_keys_distinguish_values_signed_zero_sequence_order_and_metadata():
    key = lambda value: b._qualification_snapshot(value, [0, 0], set())[1]
    values = [False, 0, 0., -0., [0], {"a": 0, "b": 1}]
    assert len({key(value) for value in values}) == len(values)
    assert key([0]) == key((0,))
    assert key({"a": 0, "b": 1}) == key({"b": 1, "a": 0})
    assert key({"frame": [1., 0.]}) != key({"frame": [0., 1.]})
    assert key({"frame": [1., 0.]}) != key({"frame": [1, 0.]})
    assert key({"frame": [1., 0.]}) != key({"other": [1., 0.]})
    assert key({"frame": [1., 0.]}) != key({"frame": [1., 0.], "extra": None})


@pytest.mark.parametrize("contours,rule", [([OUTER], "nonzero"), ([OUTER, INNER], "evenodd"),
                                          ([LENS], "nonzero")])
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("json_first", [False, True])
def test_producer_and_saved_containers_share_exact_proof_with_original_cold_form(
        monkeypatch, contours, rule, native, json_first):
    from pdf_vector_importer.source_text_outlines import IDENTITY, map_contours
    produced = map_contours(deepcopy(contours), IDENTITY)
    saved = json.loads(json.dumps(produced, sort_keys=True))
    assert type(produced[0]["start"]) is tuple and type(saved[0]["start"]) is list
    assert list(produced[0]) != list(saved[0])
    options = dict(native=native, coordinates_in_metres=False, segment_limit=512)
    expected = b._qualify_contours(produced, rule, **options)
    assert b._qualify_contours(saved, rule, **options) == expected
    first, second = (saved, produced) if json_first else (produced, saved)
    original = b._qualify_contours
    observed = []
    def observe(detached, *args, **kwargs):
        observed.append((type(detached[0]["start"]), type(detached[0]["segments"][0]), list(detached[0])))
        assert detached is not first and detached[0] is not first[0]
        return original(detached, *args, **kwargs)
    monkeypatch.setattr(b, "_qualify_contours", observe)
    cold = b.qualify_contours(first, rule, native=native)
    assert cold == expected
    cold["segments"][0].clear()
    assert b.qualify_contours(second, rule, native=native) == expected
    assert observed == [(type(first[0]["start"]), type(first[0]["segments"][0]), list(first[0]))]


def test_cached_polygon_proof_still_checks_current_expected_counter_depth(monkeypatch):
    polygons = [[(0., 0.), (3., 0.), (3., 3.), (0., 3.)]]
    b._polygon_qualification(polygons, [0])
    calls = qualification_counter(monkeypatch)
    with pytest.raises(b.OutlineTopologyUnavailable, match="counter nesting"):
        b._polygon_qualification(polygons, [1])
    assert calls == []


@pytest.mark.parametrize("invalid", [dict(OUTER, closed=False), dict(OUTER, start=[float("nan"), 0])])
def test_primed_valid_input_cannot_admit_or_cache_invalid_input(monkeypatch, invalid):
    calls = qualification_counter(monkeypatch)
    b.qualify_contours([OUTER], "nonzero")
    for _ in range(2):
        with pytest.raises((b.OutlineVerificationError, ValueError)):
            b.qualify_contours([invalid], "nonzero")
    assert len(calls) == 3 and len(b._qualification_cache) == 1


def test_custom_and_cyclic_inputs_follow_original_uncached_path(monkeypatch):
    calls = qualification_counter(monkeypatch)
    class CustomList(list):
        pass
    for _ in range(2): assert b.qualify_contours(CustomList([OUTER]), "nonzero")["area_m2"] > 0
    cyclic = []
    cyclic.append(cyclic)
    for _ in range(2):
        with pytest.raises(AttributeError): b.qualify_contours(cyclic, "nonzero")
    assert len(calls) == 4 and not b._qualification_cache


def test_input_snapshot_work_bounds_fall_back_without_changing_math(monkeypatch):
    calls = qualification_counter(monkeypatch)
    monkeypatch.setattr(b, "_CACHE_INPUT_MAX_NODES", 1)
    first = b.qualify_contours([OUTER], "nonzero")
    assert b.qualify_contours([OUTER], "nonzero") == first
    assert len(calls) == 2 and not b._qualification_cache


def test_unsupported_custom_input_does_not_invoke_custom_size_hook(monkeypatch):
    calls = qualification_counter(monkeypatch)
    class CustomList(list):
        def __sizeof__(self):
            raise AssertionError("cache must not inspect custom object internals")
    value = CustomList([OUTER])
    assert b.qualify_contours(value, "nonzero")["area_m2"] > 0
    assert b.qualify_contours(value, "nonzero")["area_m2"] > 0
    assert len(calls) == 2 and not b._qualification_cache


def test_snapshot_byte_budget_includes_complete_dictionary_keys(monkeypatch):
    calls = qualification_counter(monkeypatch)
    value = dict(OUTER, **{"x"*4096: None})
    monkeypatch.setattr(b, "_CACHE_INPUT_MAX_BYTES", 1024)
    assert b.qualify_contours([value], "nonzero")["area_m2"] > 0
    assert b.qualify_contours([value], "nonzero")["area_m2"] > 0
    assert len(calls) == 2 and not b._qualification_cache


def test_cache_lru_eviction_and_byte_limit_are_bounded(monkeypatch):
    calls = qualification_counter(monkeypatch)
    monkeypatch.setattr(b, "_CACHE_MAX_ENTRIES", 2)
    contours = [[polygon([(x, 0), (x+3, 0), (x+3, 3), (x, 3)])] for x in (0, 4, 8)]
    for row in contours: b.qualify_contours(row, "nonzero")
    assert len(b._qualification_cache) == 2
    b.qualify_contours(contours[1], "nonzero")
    assert len(calls) == 3
    b.qualify_contours(contours[0], "nonzero")
    assert len(calls) == 4 and len(b._qualification_cache) == 2
    assert b._qualification_cache_bytes + sys.getsizeof(b._qualification_cache) <= b._CACHE_MAX_BYTES
    b._clear_qualification_cache()
    monkeypatch.setattr(b, "_CACHE_MAX_BYTES", 1)
    b.qualify_contours(contours[0], "nonzero")
    b.qualify_contours(contours[0], "nonzero")
    assert len(calls) == 6 and not b._qualification_cache and b._qualification_cache_bytes == 0


def test_fraction_integer_payloads_are_included_in_retained_byte_accounting():
    value = Fraction(2**2048+1, 2**1024+3)
    assert b._qualification_retained_bytes(value) == (sys.getsizeof(value)
           + sys.getsizeof(value.numerator) + sys.getsizeof(value.denominator))


def test_retained_graph_counts_shared_objects_once_but_each_entry_independently():
    value = Fraction(2**2048+1, 2**1024+3)
    child = [value]
    entry = (child, child, value)
    expected = (sys.getsizeof(entry) + sys.getsizeof(child) + sys.getsizeof(value)
                + sys.getsizeof(value.numerator) + sys.getsizeof(value.denominator))
    assert b._qualification_retained_bytes(entry) == expected
    assert b._qualification_retained_bytes(entry) + b._qualification_retained_bytes(entry) == 2*expected


def test_equal_independent_fraction_objects_are_not_deduplicated_by_value():
    first = Fraction(2**2048+1, 2**1024+3)
    second = Fraction(str(first))
    assert first == second and first is not second
    assert first.numerator is not second.numerator and first.denominator is not second.denominator
    shared, independent = [first, first], [first, second]
    assert sys.getsizeof(shared) == sys.getsizeof(independent)
    assert (b._qualification_retained_bytes(independent) - b._qualification_retained_bytes(shared)
            == sys.getsizeof(second) + sys.getsizeof(second.numerator) + sys.getsizeof(second.denominator))


@pytest.mark.parametrize("container", [list, dict])
def test_retained_graph_rejects_cycles_without_confusing_repeated_aliases(container):
    cyclic = container()
    if container is list:
        cyclic.append(cyclic)
    else:
        cyclic["cycle"] = cyclic
    with pytest.raises(b._UncacheableInput):
        b._qualification_retained_bytes(cyclic)


def test_retained_graph_rejects_custom_types_before_their_size_hooks():
    class CustomList(list):
        def __sizeof__(self):
            raise AssertionError("must reject the type before invoking custom code")
    class CustomFraction(Fraction):
        def __sizeof__(self):
            raise AssertionError("must reject the type before invoking custom code")
    for value in (CustomList(), CustomFraction(1, 3), object()):
        with pytest.raises(b._UncacheableInput):
            b._qualification_retained_bytes(value)


def test_complete_cached_entry_charge_includes_value_tuple_and_upper_cost_integer():
    b.qualify_contours([OUTER], "nonzero")
    key, (result, cost) = next(iter(b._qualification_cache.items()))
    # Charge the actual reachable graph as well as an extra outer pair; the
    # stored cost integer may be smaller than the conservative sentinel.
    actual = b._qualification_retained_bytes((key, (result, cost)))
    assert actual <= cost
    assert cost - actual <= sys.getsizeof(sys.maxsize)
    assert cost > b._qualification_retained_bytes((key, result))


def test_retained_byte_cap_evicts_successful_old_entries(monkeypatch):
    calls = qualification_counter(monkeypatch)
    b.qualify_contours([OUTER], "nonzero")
    one_cost = next(iter(b._qualification_cache.values()))[1]
    monkeypatch.setattr(b, "_CACHE_MAX_BYTES", one_cost*2+2048)
    for width in range(4, 12):
        b.qualify_contours([polygon([(0, 0), (width, 0), (width, 3), (0, 3)])], "nonzero")
        assert b._qualification_cache_bytes + sys.getsizeof(b._qualification_cache) <= b._CACHE_MAX_BYTES
    assert len(b._qualification_cache) < len(calls)
    before = len(calls)
    b.qualify_contours([OUTER], "nonzero")
    assert len(calls) == before+1


@pytest.mark.parametrize("damage", ["control", "mesh", "shader"])
def test_warm_expected_math_never_hides_actual_native_corruption(host, monkeypatch, damage):
    outcome = build(host, "geometry" if damage == "mesh" else "glyphs")
    assert outcome.status == "delivered", outcome.evidence
    calls = qualification_counter(monkeypatch)
    obj = outcome.entity
    if damage == "control": obj.data.splines[0].bezier_points[0].co = (.1, 0., 0.)
    elif damage == "mesh": obj.data.vertices[0].co = (.1, 0., 0.)
    else: obj.data.materials[0].node_tree.nodes[0].inputs["Strength"].default_value = 2.
    with pytest.raises(b.OutlineVerificationError): b.verify_source_outline_entity(obj)
    assert calls == []  # Expected math hit; the independent native check still failed.


def _multiple_placement_record(count):
    value = record()
    value["placements"] = [dict(deepcopy(value["placements"][0]), index=4+i) for i in range(count)]
    value["source_outline_sha256"] = b._digest({k: v for k, v in value.items() if k != "source_outline_sha256"})
    return value


class _SynchronizedHost:
    """Matrix reads remain stale until synchronization; allocations stay observable."""

    def __init__(self, monkeypatch, fail=None, corrupt=None):
        self.events, self.calls = [], {}
        self.fail, self.corrupt = fail, corrupt
        owner = self

        class TrackedMesh(Mesh):
            def copy(self):
                owner.event("mesh_copy")
                result = deepcopy(self)
                owner.data.meshes.append(result)
                return result

        class TrackedObject(Object):
            def __init__(self, name, data):
                super().__init__(name, data)
                self.actual_matrix = [[1., 0., 0., 0.], [0., 1., 0., 0.],
                                      [0., 0., 1., 0.], [0., 0., 0., 1.]]

            @property
            def matrix_world(self):
                owner.event("matrix_read")
                return self.actual_matrix

            def to_mesh(self):
                owner.event("to_mesh")
                mesh = TrackedMesh([p.co for p in self.data.splines[0].bezier_points])
                mesh.materials = list(self.data.materials)
                return mesh

            def to_mesh_clear(self):
                owner.event("to_mesh_clear")
                self.clear_count += 1

            def __setitem__(self, key, value):
                if key.startswith("pdf_"):
                    owner.event("metadata")
                return super().__setitem__(key, value)

        class TrackedRegistry(Registry):
            def __init__(self, kind, factory):
                super().__init__(factory)
                self.kind = kind

            def new(self, *args):
                owner.event(self.kind+"_new")
                if self.kind == "object" and isinstance(args[1], TrackedMesh):
                    owner.event("converted_new")
                return super().new(*args)

            def remove(self, value, **kwargs):
                owner.event(self.kind+"_remove")
                return super().remove(value, **kwargs)

        self.data = NS(
            curves=TrackedRegistry("curve", lambda name, kind: NS(name=name, splines=Splines(), materials=[])),
            objects=TrackedRegistry("object", TrackedObject), materials=TrackedRegistry("material", material),
            meshes=TrackedRegistry("mesh", lambda: None),
        )
        self.context = NS(view_layer=NS(update=self.update))
        self.collection = NS(objects=NS(link=lambda obj: self.event("link")))
        monkeypatch.setitem(sys.modules, "bpy", self)
        monkeypatch.setattr(b, "_native_polygons",
                            lambda rings: [[tuple(map(float, p[0])) for p in ring] for ring in rings])

    def event(self, name):
        count = self.calls[name] = self.calls.get(name, 0)+1
        self.events.append((name, count))
        if self.fail == (name, count):
            raise RuntimeError("injected "+name)

    def update(self):
        self.event("update")
        for obj in self.data.objects:
            obj.actual_matrix = [[1., 0., 0., obj.location[0]], [0., 1., 0., obj.location[1]],
                                 [0., 0., 1., obj.location[2]], [0., 0., 0., 1.]]
        if self.corrupt:
            self.corrupt(self)

    def run(self, mode="glyphs", count=3):
        return b.build_source_outlines(_multiple_placement_record(count), self.collection,
                                       representation=mode, requested="3d_text", z_offset_m=.012)


@pytest.mark.parametrize("mode,updates", [("glyphs", 1), ("geometry", 2)])
@pytest.mark.parametrize("count", [1, 3])
def test_item_sync_preserves_source_order_owner_and_every_fresh_proof(monkeypatch, mode, updates, count):
    host = _SynchronizedHost(monkeypatch)
    verified = []
    real = b.verify_source_outline_entity

    def observed(obj, **kwargs):
        verified.append(obj)
        owner = host.data.objects.get(json.loads(obj["pdf_source_outline_record"])["source_record_owner"])
        assert owner is not None and owner.get("pdf_source_outline_source_record")
        assert all(row.get("pdf_source_outline_record") for row in host.data.objects)
        return real(obj, **kwargs)

    monkeypatch.setattr(b, "verify_source_outline_entity", observed)
    result = host.run(mode, count)
    assert result.status == "delivered", result.evidence
    assert host.calls["update"] == updates
    assert [id(obj) for obj in verified] == [id(obj) for obj in result.owned_objects]
    assert result.entity_ids == tuple(obj.name for obj in result.owned_objects)
    assert [obj["pdf_source_placement_index"] for obj in result.owned_objects] == list(range(4, 4+count))
    assert sum("pdf_source_outline_source_record" in obj for obj in result.owned_objects) == 1
    assert "pdf_source_outline_source_record" in result.owned_objects[0]
    assert len(result.evidence["placements"]) == count
    for proof in result.evidence["placements"]:
        assert proof["verified"] and proof["material"]["exact_node_graph"]
        assert proof["creation_world_matrix"][2][3] == b._f32(.012)
        assert proof["native_ink"]["exact_oriented_boundary_chain"]


def test_all_original_curve_mesh_proofs_finish_before_conversion_removal(monkeypatch):
    host = _SynchronizedHost(monkeypatch)
    result = host.run("geometry")
    assert result.status == "delivered", result.evidence
    first_remove = next(i for i, row in enumerate(host.events) if row[0] == "object_remove")
    for name in ("to_mesh", "mesh_copy", "to_mesh_clear"):
        assert sum(event == name for event, _ in host.events[:first_remove]) == 3


@pytest.mark.parametrize("mode,failure", [
    ("glyphs", ("curve_new", 2)), ("glyphs", ("material_new", 2)), ("glyphs", ("update", 1)),
    ("glyphs", ("matrix_read", 2)), ("glyphs", ("to_mesh", 2)), ("glyphs", ("metadata", 2)),
    ("geometry", ("mesh_copy", 2)), ("geometry", ("converted_new", 2)),
    ("geometry", ("object_remove", 2)), ("geometry", ("curve_remove", 2)),
    ("geometry", ("update", 2)), ("geometry", ("matrix_read", 5)),
])
def test_item_sync_failure_returns_all_surviving_owned_resources(monkeypatch, mode, failure):
    host = _SynchronizedHost(monkeypatch, fail=failure)
    result = host.run(mode)
    assert result.status == "failed" and result.entity_ids == (), result.evidence
    assert "injected" in result.evidence["detail"]
    assert {id(obj) for obj in host.data.objects} == {id(obj) for obj in result.owned_objects}
    actual_blocks = list(host.data.curves)+list(host.data.materials)+list(host.data.meshes)
    assert {id(block) for block in actual_blocks} == {id(block) for block in result.owned_datablocks}
    if failure == ("mesh_copy", 2):
        assert host.calls["to_mesh_clear"] == 2


@pytest.mark.parametrize("corruption", ["matrix", "control", "material"])
def test_corruption_after_item_sync_is_still_rejected(monkeypatch, corruption):
    def corrupt(host):
        obj = host.data.objects[-1]
        if corruption == "matrix":
            obj.actual_matrix[0][3] = .5
        elif corruption == "control":
            obj.data.splines[0].bezier_points[0].co = (.2, 0., 0.)
        else:
            obj.data.materials[0].node_tree.nodes[0].inputs["Strength"].default_value = 2.

    host = _SynchronizedHost(monkeypatch, corrupt=corrupt)
    result = host.run()
    assert result.status == "failed" and len(result.owned_objects) == 3


@pytest.mark.parametrize("failure", ["mesh", "final_verifier"])
def test_item_sync_proof_failure_clears_temporary_mesh_and_keeps_ownership(monkeypatch, failure):
    host = _SynchronizedHost(monkeypatch)

    def reject(*args, **kwargs):
        raise RuntimeError("injected proof failure")

    monkeypatch.setattr(b, "_mesh_readback" if failure == "mesh" else "verify_source_outline_entity", reject)
    result = host.run()
    assert result.status == "failed" and len(result.owned_objects) == 3
    assert host.calls["to_mesh_clear"] == (1 if failure == "mesh" else 3)


def test_item_sync_failure_does_not_claim_unrelated_resources(monkeypatch):
    host = _SynchronizedHost(monkeypatch)
    data = host.data.curves.new("unrelated", "CURVE")
    unrelated = host.data.objects.new("unrelated", data)
    unrelated["keep"] = "unchanged"
    host.fail = ("update", 1)
    result = host.run()
    assert result.status == "failed"
    assert unrelated in host.data.objects and dict(unrelated) == {"keep": "unchanged"}
    assert all(value is not unrelated for value in result.owned_objects)
    assert all(value is not data for value in result.owned_datablocks)
    assert len(result.owned_objects) == 3
