"""Scene updates may be batched; source, surface, area and rollback proofs may not."""
import copy
import hashlib
import json
import math
import sys
from types import SimpleNamespace as NS

import pytest

from pdf_vector_importer import bl_round_strokes as module
from pdf_vector_importer import bl_source_capsules as capsules
from pdf_vector_importer.import_session import ImportCancelledError


class Points(list):
    def __init__(self):
        super().__init__([NS(co=None)])

    def add(self, count):
        self.extend(NS(co=None) for _ in range(count))


class Splines(list):
    def new(self, kind):
        spline = NS(type=kind, points=Points(), bezier_points=Points())
        self.append(spline)
        return spline


class Matrix(list):
    def __init__(self, location=(0, 0, 0)):
        super().__init__([[1., 0., 0., location[0]], [0., 1., 0., location[1]],
                          [0., 0., 1., location[2]], [0., 0., 0., 1.]])

    def copy(self):
        return copy.deepcopy(self)

    def inverted(self):
        # These neutral fixtures exercise translations and orthonormal
        # rotations only; this is not a replacement for native mathutils.
        assert all(sum(self[k][i]*self[k][j] for k in range(3)) == int(i == j)
                   for i in range(3) for j in range(3))
        result = Matrix()
        for i in range(3):
            for j in range(3):
                result[i][j] = self[j][i]
            result[i][3] = -sum(result[i][j]*self[j][3] for j in range(3))
        return result

    def __matmul__(self, point):
        values = list(point)
        return NS(**{name: sum(self[index][j]*values[j] for j in range(3))+self[index][3]
                     for index, name in enumerate(('x', 'y', 'z'))})


class Curve:
    def __init__(self, host, name, _kind):
        self.host, self.name, self.alive = host, name, True
        self.splines, self.materials = Splines(), []
        self.fill_mode = 'BOTH'
        self._bevel = 0.
        self.refuse_restore = False

    def as_pointer(self):
        if not self.alive:
            raise ReferenceError('Native curve has been removed')
        return id(self)

    @property
    def users(self):
        return sum(obj.data is self for obj in self.host.objects.items.values())

    @property
    def bevel_depth(self):
        return self._bevel

    @bevel_depth.setter
    def bevel_depth(self, value):
        if self.refuse_restore and value == getattr(self, 'original_width', None):
            return
        self._bevel = value


class NativeObject(dict):
    def __init__(self, host, name, data):
        super().__init__()
        self.host, self.name, self.data, self.alive = host, name, data, True
        self.location = (0., 0., 0.)
        self.parent, self.modifiers, self.constraints, self.hide_render = None, [], [], False
        self.matrix_parent_inverse = Matrix()
        self._world, self._basis = None, None
        self.fail_reference = False

    def __setitem__(self, key, value):
        if self.fail_reference and key == 'pdf_source_round_footprint':
            return
        super().__setitem__(key, value)

    def as_pointer(self):
        if not self.alive:
            raise ReferenceError('Native object has been removed')
        return id(self)

    @property
    def matrix_world(self):
        return self._world if self._world is not None else Matrix(self.location)

    @matrix_world.setter
    def matrix_world(self, value):
        self._world = value
        inverse = self.parent.matrix_world.inverted() if self.parent is not None else Matrix()
        self._basis = Matrix()
        for i in range(4):
            for j in range(4):
                self._basis[i][j] = sum(inverse[i][k]*value[k][j] for k in range(4))
        world_origin = tuple(value[i][3] for i in range(3))
        if self.parent is not None:
            local = self.parent.matrix_world.inverted() @ world_origin
            self.location = (local.x, local.y, local.z)
        else:
            self.location = world_origin

    @property
    def matrix_basis(self):
        result = self._basis.copy() if self._basis is not None else Matrix()
        for i in range(3):
            result[i][3] = self.location[i]
        return result

    def evaluated_get(self, graph):
        assert graph is self.host.graph and self.host.updates > 0
        self.host.evaluated.append((self.name, self.host.updates))
        return self

    def to_mesh(self):
        spec = json.loads(self['pdf_source_capsule'])
        index = spec['recipe']['source_paint_order']
        cubic = self.data.splines[0].type == 'BEZIER'
        ratio = self.host.cubic_ratio if cubic else self.host.rational_ratio
        if isinstance(ratio, dict):
            ratio = ratio.get(index, 1.)
        area = spec['recipe']['source_capsule_proof']['capsule']['area']*spec['scale_mm']**2/1e6*ratio
        if index == self.host.no_surface:
            area = 0.
        mesh = NS(polygons=[NS(area=area)], loop_triangles=[])
        mesh.calc_loop_triangles = lambda: setattr(mesh, 'loop_triangles', [NS(area=area)])
        self.host.meshes.append((self.name, self.host.updates, area))
        return mesh

    def to_mesh_clear(self):
        self.host.cleared.append(self.name)


class Registry:
    def __init__(self, host, factory):
        self.host, self.factory, self.items, self.removed = host, factory, {}, []
        self.failure = None

    def new(self, name, argument):
        unique = name
        while unique in self.items:
            unique += '.001'
        item = self.factory(self.host, unique, argument)
        self.items[unique] = item
        return item

    def get(self, name):
        return self.items.get(name)

    def remove(self, item, **_kwargs):
        if self.failure == 'silent':
            return
        if self.failure == 'throws':
            raise RuntimeError('Neutral native removal refused')
        self.items.pop(item.name)
        item.alive = False
        self.removed.append(item)
        if isinstance(item, NativeObject):
            self.host.linked.pop(item.name, None)


class Host:
    def __init__(self, monkeypatch):
        self.graph, self.updates = object(), 0
        self.evaluated, self.meshes, self.cleared, self.linked = [], [], [], {}
        self.rational_ratio, self.cubic_ratio, self.no_surface = 1., 1., None
        self.on_update = None
        self.curves, self.objects = Registry(self, Curve), Registry(self, NativeObject)
        self.collection = NS(objects=NS(link=lambda obj: self.linked.__setitem__(obj.name, obj),
                                        get=self.linked.get))
        monkeypatch.setitem(sys.modules, 'bpy', NS(
            data=NS(curves=self.curves, objects=self.objects),
            context=NS(view_layer=NS(update=self.update), evaluated_depsgraph_get=lambda: self.graph)))
        monkeypatch.setitem(sys.modules, 'mathutils', NS(Vector=lambda point: point))
        monkeypatch.setitem(sys.modules, 'bmesh', NS())

    def update(self):
        self.updates += 1
        if self.on_update:
            self.on_update(self.updates)

    def fixture(self, tmp_path, count):
        from pdf_vector_importer.bl_geometry_builder import _line_bevel_depth

        path = tmp_path / 'fictional-source.bin'
        path.write_bytes(b'Generated fictional source identity; no user PDF')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        specs, centers = [], []
        for index in range(count):
            capsule = dict(start=(10.*index, 0.), end=(10.*index+1., 0.), width=.02,
                           length=1., area=.02+math.pi*.01**2)
            spec = dict(primitive_id=index, points_mm=[(10.*index,100.),(10.*index+1.,100.)],
                        scale_mm=1., flip_y=True, page_height=100.,
                        recipe=dict(source_paint_order=index, source_sha256=digest, page=1,
                                    source_capsule_proof=dict(capsule=capsule)))
            curve = self.curves.new('CenterData'+str(index), 'CURVE')
            curve.bevel_depth = _line_bevel_depth(.02)
            curve.original_width = curve.bevel_depth
            curve.materials.append(object())
            spline = curve.splines.new('POLY')
            spline.use_cyclic_u = False
            spline.points.add(1)
            for point, (x,y) in zip(spline.points, spec['points_mm'], strict=True):
                point.co = (x/1000, y/1000, 0., 1.)
            center = self.objects.new('Center'+str(index), curve)
            specs.append(spec)
            centers.append(center)
        return path, specs, centers, {'_image_order_stroke_objects': {i:[obj] for i,obj in enumerate(centers)}}


def assert_restored(host, centers):
    assert not host.linked
    assert len(host.objects.items) == len(centers)
    assert len(host.curves.items) == len(centers)
    assert all(center.data.bevel_depth == center.data.original_width and 'pdf_source_round_footprint' not in center for center in centers)


def test_128_rational_capsules_have_one_update_and_every_surface_area_source_proof(monkeypatch, tmp_path):
    host = Host(monkeypatch)
    path, specs, centers, config = host.fixture(tmp_path, 128)
    before = copy.deepcopy(specs)
    records = module.apply_round_strokes(specs, host.collection, config, source_path=path)
    assert host.updates == 1
    assert len(records) == 128 and specs == before
    assert len(host.meshes) == len(host.cleared) == 256
    assert {generation for _,generation in host.evaluated} == {1}
    assert all(record['status'] == 'verified' for record in records)
    assert all(center.data.bevel_depth == .00001 for center in centers)
    assert all(host.linked[record['footprint_object']].parent is center
               for record,center in zip(records,centers,strict=True))


def test_cubic_fallback_batch_updates_once_and_revalidates_surviving_rationals(monkeypatch, tmp_path):
    host = Host(monkeypatch)
    path, specs, _, config = host.fixture(tmp_path, 80)
    host.rational_ratio = {i:.75 for i in range(0,80,2)}
    records = module.apply_round_strokes(specs, host.collection, config, source_path=path)
    assert host.updates == 2 and len(records) == 80
    assert sum(record['status'] == 'approximated' for record in records) == 40
    assert len(host.meshes) == len(host.cleared) == 320
    assert len(host.curves.removed) == len(host.objects.removed) == 40
    assert all(sum(name == record['footprint_object'] and generation == 2 for name,generation in host.evaluated) == 2 for record in records)


@pytest.mark.parametrize('failure', ['no_surface', 'cubic_area', 'source_control', 'material', 'width',
                                   'source_file', 'native_reference', 'world', 'unlinked', 'source_record'])
def test_rejected_batch_restores_original_lines_and_deletes_all_private_data(monkeypatch, tmp_path, failure):
    host = Host(monkeypatch)
    path, specs, centers, config = host.fixture(tmp_path, 4)
    if failure == 'no_surface':
        host.no_surface = 3
    elif failure == 'cubic_area':
        host.rational_ratio = host.cubic_ratio = .75
    elif failure == 'native_reference':
        centers[-1].fail_reference = True
    else:
        def mutate(_generation):
            footprint = next(iter(host.linked.values()))
            if failure == 'source_control':
                footprint.data.splines[0].points[0].co = (9,9,9,1)
            elif failure == 'material':
                footprint.data.materials[0] = object()
            elif failure == 'width':
                centers[0].data.bevel_depth = 7.
            elif failure == 'world':
                matrix = footprint.matrix_world.copy()
                matrix[0][0] = -1.
                footprint.matrix_world = matrix
            elif failure == 'unlinked':
                host.linked.pop(footprint.name)
            elif failure == 'source_record':
                footprint['pdf_source_capsule'] = '{}'
            else:
                path.write_bytes(b'Changed fictional source')
        host.on_update = mutate
    with pytest.raises(ValueError):
        module.apply_round_strokes(specs, host.collection, config, source_path=path)
    assert_restored(host, centers)
    assert len(host.meshes) == len(host.cleared)


def test_complete_301_item_ledger_and_successful_progress_are_retained(monkeypatch, tmp_path):
    host = Host(monkeypatch)
    path, specs, _, config = host.fixture(tmp_path, 301)
    events = []
    records = module.apply_round_strokes(specs, host.collection, config, source_path=path,
                                        progress_callback=lambda fraction,label: events.append((fraction,label)))
    assert len(records) == 301 and host.updates == 1
    assert {record['primitive_id'] for record in records} == set(range(301))
    assert all(record['source_area_pdf2'] == .02+math.pi*.01**2 for record in records)
    assert events[0][0] == 0 and events[-1][0] == 1.
    assert all(a[0] <= b[0] for a,b in zip(events,events[1:],strict=False))


def test_surviving_rational_area_is_checked_after_cubic_scene_update(monkeypatch, tmp_path):
    host = Host(monkeypatch)
    path, specs, centers, config = host.fixture(tmp_path, 2)
    host.rational_ratio = {1:.75}
    def change_after_fallback(generation):
        if generation == 2:
            host.rational_ratio[0] = .75
    host.on_update = change_after_fallback
    with pytest.raises(ValueError, match='filled footprint could not be verified'):
        module.apply_round_strokes(specs, host.collection, config, source_path=path)
    assert_restored(host, centers)
    assert host.updates == 2


def test_existing_center_footprint_reference_is_restored_after_late_failure(monkeypatch, tmp_path):
    host = Host(monkeypatch)
    path, specs, centers, config = host.fixture(tmp_path, 2)
    centers[0]['pdf_source_round_footprint'] = 'EarlierUserFootprint'
    centers[-1].fail_reference = True
    with pytest.raises(ValueError, match='reference was not retained'):
        module.apply_round_strokes(specs, host.collection, config, source_path=path)
    assert centers[0]['pdf_source_round_footprint'] == 'EarlierUserFootprint'
    assert not host.linked and len(host.objects.items) == 2
    assert all(center.data.bevel_depth == center.data.original_width for center in centers)


def test_partial_rational_construction_refusal_removes_curve_and_prior_footprints(monkeypatch, tmp_path):
    host = Host(monkeypatch)
    path, specs, centers, config = host.fixture(tmp_path, 3)
    new_curve = host.curves.new
    class RefusedPoint:
        @property
        def co(self):
            return (0.,0.,0.,1.)
        @co.setter
        def co(self, value):
            pass  # Neutral native storage refuses a requested control.
    class RefusedPoints(list):
        def __init__(self):
            super().__init__([RefusedPoint()])
        def add(self, count):
            self.extend(RefusedPoint() for _ in range(count))
    def allocate(name, kind):
        curve = new_curve(name, kind)
        if len(host.linked) == 1:
            new_spline = curve.splines.new
            def spline(kind):
                result = new_spline(kind)
                result.points = RefusedPoints()
                return result
            curve.splines.new = spline
        return curve
    monkeypatch.setattr(host.curves, 'new', allocate)
    with pytest.raises(ValueError, match='rational boundary construction changed'):
        module.apply_round_strokes(specs, host.collection, config, source_path=path)
    assert_restored(host, centers)
    assert host.updates == 0


@pytest.mark.parametrize('phase', ['Constructing round strokes', 'Evaluating round strokes', 'Verifying round strokes',
                                 'Checking original round strokes', 'Checking completed round strokes'])
def test_cancellation_is_bounded_and_restores_original_source_lines(monkeypatch, tmp_path, phase):
    host = Host(monkeypatch)
    path, specs, centers, config = host.fixture(tmp_path, 301)
    events = []
    def progress(fraction, label):
        events.append((fraction,label,len(host.linked)))
        return not (label == phase and (phase != 'Constructing round strokes' or len(host.linked) >= 50))
    with pytest.raises(ImportCancelledError):
        module.apply_round_strokes(specs, host.collection, config, source_path=path, progress_callback=progress)
    assert_restored(host, centers)
    assert all(a[0] <= b[0] for a,b in zip(events,events[1:],strict=False))
    if phase == 'Constructing round strokes':
        assert 50 <= events[-1][2] < 75


def test_publication_cancel_after_stored_references_restores_every_original(monkeypatch, tmp_path):
    host = Host(monkeypatch)
    path, specs, centers, config = host.fixture(tmp_path, 301)
    centers[0]['pdf_source_round_footprint'] = 'EarlierUserFootprint'
    events = []
    def progress(fraction, label):
        published = sum('pdf_source_round_footprint' in center for center in centers) - 1
        events.append((fraction, label, published))
        return not (label == 'Linking round stroke footprints' and published >= 50)
    with pytest.raises(ImportCancelledError):
        module.apply_round_strokes(specs, host.collection, config, source_path=path, progress_callback=progress)
    assert 50 <= events[-1][2] < 75 and events[-1][0] < 1.
    assert centers[0]['pdf_source_round_footprint'] == 'EarlierUserFootprint'
    assert all('pdf_source_round_footprint' not in center for center in centers[1:])
    assert all(center.data.bevel_depth == center.data.original_width for center in centers)
    assert not host.linked and len(host.objects.items) == len(host.curves.items) == 301


@pytest.mark.parametrize('mutation', ['controls', 'material', 'parent', 'reference', 'world', 'source_file', 'stale_local'])
def test_final_callback_cannot_launder_an_earlier_published_footprint(monkeypatch, tmp_path, mutation):
    host = Host(monkeypatch)
    path, specs, centers, config = host.fixture(tmp_path, 4)
    def progress(_fraction, label):
        if label == 'Checking completed round strokes':
            footprint = next(iter(host.linked.values()))
            if mutation == 'controls':
                footprint.data.splines[0].points[0].co = (9.,9.,9.,1.)
            elif mutation == 'material':
                footprint.data.materials[0] = object()
            elif mutation == 'parent':
                footprint.parent = centers[-1]
            elif mutation == 'reference':
                centers[0]['pdf_source_round_footprint'] = 'OtherNativeObject'
            elif mutation == 'world':
                world = footprint.matrix_world.copy()
                world[0][3] += 1.
                footprint.matrix_world = world
            elif mutation == 'stale_local':
                footprint.location = (9.,9.,9.)  # Cached world still agrees until a graph update.
            else:
                path.write_bytes(b'Changed fictional source after publication')
        return True
    with pytest.raises(ValueError):
        module.apply_round_strokes(specs, host.collection, config, source_path=path, progress_callback=progress)
    assert_restored(host, centers)


def test_translated_center_preserves_world_capsule_and_proves_parent_local_origin(monkeypatch, tmp_path):
    host = Host(monkeypatch)
    path, specs, centers, config = host.fixture(tmp_path, 2)
    shift = (.3, -.2, 0.)
    for center in centers:
        center.matrix_world = Matrix(shift)
        for point in center.data.splines[0].points:
            point.co = tuple(point.co[i]-shift[i] for i in range(3))+(1.,)
    records = module.apply_round_strokes(specs, host.collection, config, source_path=path)
    assert len(records) == 2 and host.updates == 1
    for record, center, spec in zip(records, centers, specs, strict=True):
        footprint = host.linked[record['footprint_object']]
        origin, _ = capsules.capsule_controls(spec['recipe']['source_capsule_proof']['capsule'])
        x, y = capsules.model_point(origin, spec['page_height'], spec['scale_mm'], spec['flip_y'])
        world = (x/1000,y/1000,0.)
        assert footprint.location == pytest.approx(tuple(value-delta for value,delta in zip(world,shift,strict=True)))
        assert tuple(footprint.matrix_world[i][3] for i in range(3)) == pytest.approx(world)
        assert footprint.parent is center


def test_late_parent_axis_rotation_cannot_launder_stale_child_world_matrix(monkeypatch, tmp_path):
    host = Host(monkeypatch)
    path, specs, centers, config = host.fixture(tmp_path, 2)
    def progress(_fraction, label):
        if label == 'Checking completed round strokes':
            # Rotate about the original horizontal stroke itself. Its two
            # source endpoints and child's origin do not change, but the
            # child's stored local basis would now tip the capsule out of XY.
            matrix = Matrix((0., .1, -.1))
            matrix[1][1], matrix[1][2] = 0., -1.
            matrix[2][1], matrix[2][2] = 1., 0.
            centers[0].matrix_world = matrix
        return True
    with pytest.raises(ValueError, match='local transform'):
        module.apply_round_strokes(specs, host.collection, config, source_path=path, progress_callback=progress)
    assert_restored(host, centers)


@pytest.mark.parametrize('failure', ['silent', 'throws', 'curve_silent', 'restore_silent'])
def test_uncertain_cleanup_escalates_and_never_returns_certified_records(monkeypatch, tmp_path, failure):
    host = Host(monkeypatch)
    path, specs, centers, config = host.fixture(tmp_path, 2)
    host.no_surface = 1
    if failure == 'curve_silent':
        host.curves.failure = 'silent'
    elif failure == 'restore_silent':
        centers[0].data.refuse_restore = True
    else:
        host.objects.failure = failure
    with pytest.raises(module.RoundStrokeRollbackFailure, match='rollback could not be verified'):
        module.apply_round_strokes(specs, host.collection, config, source_path=path)


def test_wire_only_request_has_no_new_footprints_or_scene_update(monkeypatch, tmp_path):
    host = Host(monkeypatch)
    path, specs, centers, config = host.fixture(tmp_path, 2)
    for center in centers:
        center.data.fill_mode = 'NONE'
        center.data.bevel_depth = 0.
    assert module.apply_round_strokes(specs, host.collection, config, source_path=path) == []
    assert not host.linked and host.updates == 0


def test_direct_native_capsule_still_immediately_updates_and_rejects_unfilled_surface(monkeypatch, tmp_path):
    host = Host(monkeypatch)
    _, specs, centers, _ = host.fixture(tmp_path, 1)
    host.no_surface = 0
    with pytest.raises(ValueError, match='no filled surface'):
        capsules._native_capsule(specs[0], host.collection, centers[0].data.materials[0])
    assert host.updates == 1 and len(host.meshes) == len(host.cleared) == 1
    assert_restored(host, centers)


def test_real_engine_round_stage_cancel_is_reported_and_active_page_discarded(monkeypatch, tmp_path):
    import test_clip_fill_degrade_blender as engine_fixture
    host = engine_fixture._FakeHost(monkeypatch, tmp_path)
    def prepared(_page, page_data, **_kwargs):
        primitive = next(p for p in page_data.primitives if p.type == 'line')
        return [dict(primitive_id=primitive.id, points_mm=list(primitive.points))], []
    monkeypatch.setattr(module, 'prepare_round_strokes', prepared)
    monkeypatch.setattr(host.engine, 'build_page', lambda *_args, **_kwargs: {'curves': 0})
    reached = []
    def apply(_specs, _collection, _config, *, source_path, progress_callback=None):
        assert progress_callback is not None, 'Default engine must supply its real cancel probe'
        reached.append(str(source_path))
        if progress_callback(.3, 'Constructing round strokes') is False:
            raise ImportCancelledError('neutral round-stage cancel')
        raise AssertionError('Round-stage cancel was ignored')
    monkeypatch.setattr(module, 'apply_round_strokes', apply)
    discarded = []
    real_discard = host.engine._discard_page_collection
    def discard(collection):
        discarded.append(collection.name)
        return real_discard(collection)
    monkeypatch.setattr(host.engine, '_discard_page_collection', discard)
    stats = host.run(progress_callback=lambda _pct,label: not label.startswith('Constructing round strokes'))
    assert reached and discarded
    assert stats['cancelled'] is True and stats['pages_imported'] == 0
    assert stats['resume']['remaining_pages'] == [1]
    assert not stats.get('source_round_stroke_footprints')
