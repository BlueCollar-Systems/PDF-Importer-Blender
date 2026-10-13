"""Exact RNA ownership selection must retain the native source checks."""
import copy
from types import SimpleNamespace as NS

import pytest

from pdf_vector_importer import fill_paint_order as order
from test_fill_paint_order import native_faces


def pointer_faces(monkeypatch):
    collection, owned = native_faces(monkeypatch)
    states = [[101], [202]]
    for spec, state in zip(owned, states, strict=True):
        spec["object"].as_pointer = lambda state=state: state[0]
    aliases = [NS(as_pointer=lambda state=state: state[0]) for state in states]
    collection.all_objects = aliases
    return collection, owned, states, aliases


def test_many_native_fills_keep_full_validation_with_linear_identity_comparisons(monkeypatch):
    from pdf_vector_importer import image_paint_order

    collection, templates = native_faces(monkeypatch)
    count = 200
    owned = []
    for index in range(count):
        spec = copy.deepcopy(templates[0])
        obj = spec["object"]
        obj.name = "fictional-fill-" + str(index)
        obj.as_pointer = lambda index=index: index + 1
        for vertex in obj.data.vertices:
            vertex.co[0] += index * .02
        spec["points_mm"] = [(x + index * 20., y) for x, y in spec["points_mm"]]
        spec["primitive_id"] = obj.name
        spec["source_draw_order"] = index
        owned.append(spec)
    collection.all_objects = [spec["object"] for spec in owned]
    comparisons, validated, evaluated = [], [], []
    compare = order.same_native_object
    validate = order.validate_source_fill
    corners = image_paint_order._world_corners

    def check_identity(first, second):
        comparisons.append((id(first), id(second)))
        return compare(first, second)

    def check_source(spec, members, graph):
        validated.append(spec["primitive_id"])
        return validate(spec, members, graph)

    def check_evaluated(obj, graph):
        evaluated.append(id(obj))
        return corners(obj, graph)

    monkeypatch.setattr(order, "same_native_object", check_identity)
    monkeypatch.setattr(order, "validate_source_fill", check_source)
    monkeypatch.setattr(image_paint_order, "_world_corners", check_evaluated)
    before = [[tuple(v.co) for v in spec["object"].data.vertices] for spec in owned]
    assert order.apply_fill_depths(collection, owned) == []
    assert validated == [spec["primitive_id"] for spec in owned]
    assert evaluated == [id(spec["object"]) for spec in owned]
    assert len(comparisons) <= count * 2
    assert [[tuple(v.co) for v in spec["object"].data.vertices] for spec in owned] == before
    assert all(spec["object"].location.z == 0. for spec in owned)


def test_distinct_rna_alias_wrappers_keep_source_style_and_move_readbacks(monkeypatch):
    collection, owned, _states, _aliases = pointer_faces(monkeypatch)
    before = [[tuple(v.co) for v in row["object"].data.vertices] for row in owned]
    result = order.apply_fill_depths(collection, owned)
    assert result[0]["status"] == "qualified"
    assert owned[1]["object"].location.z == .00005
    assert [[tuple(v.co) for v in row["object"].data.vertices] for row in owned] == before


@pytest.mark.parametrize("pointer", [0, 101.0, True, None])
def test_unindexed_pointer_values_retain_original_comparison_semantics(monkeypatch, pointer):
    collection, owned = native_faces(monkeypatch)
    obj = owned[0]["object"]
    obj.as_pointer = lambda: pointer
    collection.all_objects = [NS(as_pointer=lambda: pointer)]
    if pointer == 0:
        with pytest.raises(ValueError, match="ownership changed"):
            order.apply_fill_depths(collection, owned[:1])
    else:
        assert order.apply_fill_depths(collection, owned[:1]) == []
    assert obj.location.z == 0.


@pytest.mark.parametrize("pointer", ["missing", "noncallable", "zero", "throwing"])
def test_same_wrapper_keeps_identity_semantics_when_pointer_api_is_unavailable(monkeypatch, pointer):
    collection, owned = native_faces(monkeypatch)
    obj = owned[0]["object"]
    if pointer == "noncallable":
        obj.as_pointer = 101
    elif pointer == "zero":
        obj.as_pointer = lambda: 0
    elif pointer == "throwing":
        obj.as_pointer = lambda: (_ for _ in ()).throw(ReferenceError("fictional removed RNA"))
    collection.all_objects = [obj]
    assert order.apply_fill_depths(collection, owned[:1]) == []
    assert obj.location.z == 0.


def test_unrelated_stale_wrapper_cannot_introduce_an_eager_build_failure(monkeypatch):
    collection, owned = native_faces(monkeypatch)
    obj = owned[0]["object"]
    obj.as_pointer = lambda: 101
    stale = NS(as_pointer=lambda: (_ for _ in ()).throw(ReferenceError("fictional removed RNA")))
    collection.all_objects = [obj, stale]
    assert order.apply_fill_depths(collection, owned[:1]) == []


def test_names_properties_and_python_equality_never_establish_native_ownership(monkeypatch):
    collection, owned, _states, aliases = pointer_faces(monkeypatch)

    class EqualityTrap:
        name = owned[0]["object"].name

        def as_pointer(self):
            return 999

        def __eq__(self, _other):
            raise AssertionError("Python equality is not native ownership")

        def __hash__(self):
            raise AssertionError("wrapper hashing is not native ownership")

    collection.all_objects = [EqualityTrap(), *aliases]
    assert order.apply_fill_depths(collection, owned)[0]["status"] == "qualified"


@pytest.mark.parametrize("change", ["new-pointer", "stale-bucket", "deleted-wrapper"])
def test_cached_candidates_are_revalidated_and_misses_keep_original_scan(monkeypatch, change):
    collection, owned, states, aliases = pointer_faces(monkeypatch)
    validate = order.validate_source_fill
    checked = []

    def after_inventory(spec, members, graph):
        if spec is owned[0]:
            if change == "new-pointer":
                states[0][0] = 303
            elif change == "stale-bucket":
                aliases[0].as_pointer = lambda: 303
            else:
                aliases[0].as_pointer = lambda: (_ for _ in ()).throw(ReferenceError("fictional removed RNA"))
        result = validate(spec, members, graph)
        checked.append(spec["primitive_id"])
        return result

    monkeypatch.setattr(order, "validate_source_fill", after_inventory)
    if change == "new-pointer":
        with pytest.raises(ValueError, match="ownership changed during validation"):
            order.apply_fill_depths(collection, owned)
        assert checked == [row["primitive_id"] for row in owned], 'miss fallback still validates current candidates'
    elif change == "stale-bucket":
        with pytest.raises(ValueError, match="ownership changed"):
            order.apply_fill_depths(collection, owned)
    else:
        with pytest.raises(ReferenceError, match="removed RNA"):
            order.apply_fill_depths(collection, owned)
    assert all(spec["object"].location.z == 0. for spec in owned)


@pytest.mark.parametrize("change", ["disappeared", "changed"])
def test_earlier_native_owner_change_during_later_validation_rejects_before_movement(monkeypatch, change):
    collection, owned, states, aliases = pointer_faces(monkeypatch)
    validate = order.validate_source_fill
    checked = []

    def after_inventory(spec, members, graph):
        if spec is owned[1]:
            if change == "disappeared":
                aliases[0].as_pointer = lambda: (_ for _ in ()).throw(ReferenceError("fictional removed earlier RNA"))
            else:
                states[0][0] = 303
        result = validate(spec, members, graph)
        checked.append(spec["primitive_id"])
        return result

    monkeypatch.setattr(order, "validate_source_fill", after_inventory)
    with pytest.raises(ValueError, match="ownership changed during validation"):
        order.apply_fill_depths(collection, owned)
    assert checked == [row["primitive_id"] for row in owned]
    assert all(spec["object"].location.z == 0. for spec in owned)


def test_missing_native_owner_and_duplicate_primitive_ids_still_reject_before_movement(monkeypatch):
    collection, owned, _states, aliases = pointer_faces(monkeypatch)
    collection.all_objects = aliases[1:]
    with pytest.raises(ValueError, match="ownership changed"):
        order.apply_fill_depths(collection, owned)
    assert all(spec["object"].location.z == 0. for spec in owned)
    collection.all_objects = aliases
    owned[1]["primitive_id"] = owned[0]["primitive_id"]
    with pytest.raises(ValueError, match="Duplicate"):
        order.apply_fill_depths(collection, owned)
    assert all(spec["object"].location.z == 0. for spec in owned)


@pytest.mark.parametrize("change", ["point", "color", "emission", "loop"])
def test_source_checks_still_read_actual_geometry_and_style_after_inventory(monkeypatch, change):
    collection, owned, _states, _aliases = pointer_faces(monkeypatch)
    validate = order.validate_source_fill

    def after_inventory(spec, members, graph):
        if spec is owned[1]:
            obj = spec["object"]
            if change == "point":
                obj.data.vertices[0].co[0] += .01
            elif change == "color":
                obj.data.materials[0].diffuse_color = (1., 0., 0., 1.)
            elif change == "emission":
                obj.data.materials[0].node_tree.nodes[0].inputs["Strength"].default_value = 2
            else:
                obj.data.polygons[0].vertices = (0, 1)
        return validate(spec, members, graph)

    monkeypatch.setattr(order, "validate_source_fill", after_inventory)
    with pytest.raises(ValueError):
        order.apply_fill_depths(collection, owned)
    assert all(spec["object"].location.z == 0. for spec in owned)


def test_direct_iterable_validator_and_empty_owned_path_are_unchanged(monkeypatch):
    collection, owned = native_faces(monkeypatch)
    world, points, corners = order.validate_source_fill(owned[0], iter(collection.all_objects), None)
    assert world and points and corners
    assert order.apply_fill_depths(None, []) == []
