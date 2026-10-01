"""Exercise the real rung controller and post-stack proof boundary."""
from copy import deepcopy
import sys
import json
from types import SimpleNamespace as NS

import pytest

if 'bpy' not in sys.modules:
    sys.modules['bpy'] = NS(types=NS(Collection=object, Object=object, Material=object, VectorFont=object))
sys.modules.setdefault('bmesh', NS())

from pdf_vector_importer import bl_import_engine as engine, bl_text_builder as text
from pdf_vector_importer import bl_source_outline_builder as native
from pdf_vector_importer.source_text_outlines import OutlineUnavailable, digest, missing_font_evidence, verify_zero_ink_collection
from pdf_vector_importer.text_delivery import AttemptOutcome, _impossibility_proof_failures, deliver_item


def item():
    return NS(id=3, page_number=1, font_name='Example', text='A', font_asset=None,
        requires_individual_positioning=True,
        font_failure=NS(reason='embedded_font_asset_build_failed',
          proof_category='source_specific_impossibility', detail='embedded font stream is empty',
          source_xref=4, page_number=1, span_font_name='Example'))


def attempt(row, representation, provider):
    return text._attempt_one_representation(representation, row, object(), effective_page=1,
        requested='3d_text', item_id='page:1:text:3', visual_style='source', z_offset_m=.1,
        terminal_raster_callback=None, source_outline_callback=provider)


def test_positive_absence_uses_source_route_before_font_positioning(monkeypatch):
    calls = []
    monkeypatch.setattr(text, '_attempt_positioned_characters', lambda *_a, **_k: pytest.fail('FONT path'))
    monkeypatch.setattr(native, 'build_source_outlines', lambda record, _col, **kw:
        calls.append((record, kw)) or AttemptOutcome.delivered('curve', entity_ids=['curve']))
    row = item()
    assert attempt(row, 'glyphs', lambda received: {'same_item': received is row}).entity == 'curve'
    assert calls == [({'same_item': True}, dict(representation='glyphs', requested='3d_text', z_offset_m=.1))]


def test_verified_font_and_unproved_absence_never_enter_source_route(monkeypatch):
    sentinel = AttemptOutcome.delivered('font')
    monkeypatch.setattr(text, '_attempt_positioned_characters', lambda *_a, **_k: sentinel)
    for field in ('font_asset', 'font_failure'):
        row = item()
        if field == 'font_asset':
            row.font_asset = object()
        else:
            row.font_failure.detail = 'source program is corrupt'
        assert attempt(row, 'glyphs', lambda _row: pytest.fail('source route')) is sentinel


def test_unsupported_source_retains_original_font_proof_without_native_allocation(monkeypatch):
    monkeypatch.setattr(native, 'build_source_outlines', lambda *_a, **_k: pytest.fail('native allocation'))
    def unsupported(_row):
        raise OutlineUnavailable('ambiguous original occurrence')
    outcome = attempt(item(), 'glyphs', unsupported)
    assert outcome.status == 'impossible'
    assert outcome.reason == 'exact_source_font_unavailable_for_item'
    assert outcome.evidence['source_outline_qualification']['native_entities_created'] == 0
    assert not _impossibility_proof_failures(attempted_representation='glyphs', item_id='page:1:text:3',
        page_number=1, source_span_id=3, outcome=outcome)


def test_requested_3d_records_explicit_glyph_downgrade_and_native_failure_stops(monkeypatch):
    row = item()
    monkeypatch.setattr(text, '_attempt_positioned_characters',
                        lambda *_a, **_k: text._load_exact_font(row, 'page:1:text:3', 1)[1])
    for status in ('delivered', 'failed'):
        result = (AttemptOutcome.delivered('curve', entity_ids=['curve']) if status == 'delivered'
                  else AttemptOutcome.failed('wrong native source control', owned_objects=['owned']))
        monkeypatch.setattr(native, 'build_source_outlines', lambda *_a, _result=result, **_k: _result)
        seen = []
        def run(mode, _seen=seen):
            _seen.append(mode)
            return attempt(row, mode, lambda _row: {})
        obj, record = deliver_item(item_id='page:1:text:3', page_number=1, source_span_id=3,
            requested='3d_text', attempt=run, cleanup=lambda _outcome: {'status': 'complete'})
        assert seen == ['3d_text', 'text', 'glyphs']
        assert record['status'] == status
        assert record['final_representation'] == ('glyphs' if status == 'delivered' else None)
        assert obj == ('curve' if status == 'delivered' else None)


def topology_proof():
    return AttemptOutcome.impossible('source_outline_topology_unavailable_for_item', evidence={
        'importer_id': 'bc_pdf_vector_importer.blender', 'item_id': 'page:1:text:3',
        'page_number': 1, 'source_span_id': 3, 'proof_category': 'source_outline_topology_unsupported',
        'native_entities_created': 0, 'pdf_sha256': 'a'*64, 'svg_sha256': 'b'*64,
        'source_outline_sha256': 'c'*64, 'source_font_absence': missing_font_evidence(item(), 1),
        'detail': 'intersecting source contours'})


@pytest.mark.parametrize('mutation', [
    lambda value: value.evidence.update(native_entities_created=1),
    lambda value: value.evidence.update(native_entities_created=False),
    lambda value: value.evidence.update(pdf_sha256='missing'),
    lambda value: value.evidence.update(source_font_absence={}),
    lambda value: value.evidence['source_font_absence'].update(detail='corrupt font'),
    lambda value: setattr(value, 'owned_objects', ('owned',)),
    lambda value: value.evidence.update(page_number=2),
])
def test_topology_unavailability_requires_complete_source_bound_preallocation_proof(mutation):
    positive = topology_proof()
    check = lambda value: _impossibility_proof_failures(attempted_representation='glyphs',
        item_id='page:1:text:3', page_number=1, source_span_id=3, outcome=value)
    assert check(positive) == []
    mutation(positive)
    assert check(positive)


def test_final_outline_verify_uses_creation_matrix_plus_independent_stack(monkeypatch):
    obj = NS(name='outline', type='CURVE', location=(0., -.5, .1))
    monkeypatch.setattr(engine, 'bpy', NS(data=NS(objects=NS(get=lambda _name: obj)),
        context=NS(view_layer=NS(update=lambda: None))))
    creation = [[1.,0.,0.,0.],[0.,1.,0.,0.],[0.,0.,1.,.1],[0.,0.,0.,1.]]
    record = {'page': 1, 'status': 'delivered', 'final_representation': 'glyphs',
        'entity_ids': ['outline'], 'attempts': [{'status': 'delivered', 'evidence': {
            'outline_source': 'source_renderer_svg', 'placements': [{'entity_id': 'outline',
                'creation_world_matrix': creation, 'actual_location_m': [0.,0.,.1],
                'source_outline_sha256': 'a'*64, 'source_placement_index': 7}]}}]}
    received = []
    def verify(actual, **kwargs):
        received.append(kwargs)
        assert actual is obj
        assert kwargs['expected_world_matrix'][1][3] == -.5
        assert kwargs['page_clip_verified'] is False
        return {'verified': True, 'source_outline_sha256': 'a'*64, 'source_placement_index': 7}
    monkeypatch.setattr(native, 'verify_source_outline_entity', verify)
    assert engine._reverify_text_delivery_after_stack([record], page_number=1, stack_offset_m=-.5) == []
    assert creation[1][3] == 0 and len(received) == 1
    swapped = deepcopy(record)
    monkeypatch.setattr(native, 'verify_source_outline_entity', lambda *_a, **_k:
        {'verified': True, 'source_outline_sha256': 'a'*64, 'source_placement_index': 8})
    assert engine._reverify_text_delivery_after_stack([swapped], page_number=1, stack_offset_m=-.5)
    assert swapped['status'] == 'failed'
    damaged = deepcopy(record)
    def reject(*_a, **_k):
        raise ValueError('native cubic handle changed')
    monkeypatch.setattr(native, 'verify_source_outline_entity', reject)
    failures = engine._reverify_text_delivery_after_stack([damaged], page_number=1, stack_offset_m=-.5)
    assert failures and damaged['status'] == 'failed'


class Collection(dict):
    name = 'PDF_Page_1'


def zero_source(row, pdf_hash='a'*64):
    record = dict(item_id='page:1:text:3', page_number=1, source_text=' ',
        pdf_sha256=pdf_hash, svg_sha256='b'*64, placements=[], empty_placements=[], clipped_placements=[],
        source_font_absence=missing_font_evidence(row, 1), synthetic_extraction_spaces=[{
            'character_index': 0, 'font': 'Example', 'unicode': ' ', 'source_origin': [10.,20.],
            'source_bbox': [10.,20.,11.,21.],
            'proof': 'original_rawdict_synthetic_space_without_trace_occurrence'}])
    record['source_outline_sha256'] = digest(record)
    return record


def zero_delivery(monkeypatch, pdf_hash='a'*64):
    row = item(); row.text = ' '
    collection, opts = Collection(), NS()
    monkeypatch.setattr(text, '_attempt_one_representation', lambda *_a, **_k: pytest.fail('native attempt'))
    assert text.build_text(row, collection, page_number=1, text_mode='glyphs',
        provenance_opts=opts, source_outline_callback=lambda _row: zero_source(row, pdf_hash)) is None
    monkeypatch.setattr(engine, 'bpy', NS(data=NS(collections=NS(get=lambda name:
        collection if name == collection.name else None))))
    return collection, opts


def test_whitespace_proof_persists_without_object_or_false_delivery(monkeypatch):
    collection, opts = zero_delivery(monkeypatch)
    record = opts._text_delivery_records[0]
    assert record['status'] == 'verified_zero_ink'
    assert record['final_representation'] is None and record['entity_ids'] == []
    assert engine._terminal_import_failures({'text_mode':'glyphs'}, {'text_source_spans':1}, opts) == []
    summary = engine._text_delivery_from_provenance(opts)['summary']
    assert summary['source_items'] == summary['verified_zero_ink_items'] == 1
    assert summary['delivered_items'] == summary['failed_items'] == 0
    reopened = Collection(json.loads(json.dumps(collection)))
    assert verify_zero_ink_collection(reopened, opts._text_delivery_records)['verified']


@pytest.mark.parametrize('damage', ['missing_ledger', 'stale_proof', 'wrong_owner', 'visible_text', 'not_absent', 'duplicate'])
def test_zero_ink_never_counts_ready_after_evidence_or_persisted_roster_damage(monkeypatch, damage):
    collection, opts = zero_delivery(monkeypatch)
    record = opts._text_delivery_records[0]
    if damage == 'missing_ledger':
        collection.clear()
    elif damage == 'stale_proof':
        record['zero_ink_proof_sha256'] = '0'*64
    elif damage == 'wrong_owner':
        record['page_collection'] = 'Other_Page'
    elif damage == 'duplicate':
        opts._text_delivery_records.append(deepcopy(record))
    else:
        source = record['zero_ink_proof']['source_record']
        if damage == 'visible_text':
            source['source_text'] = 'A'
        else:
            source['source_font_absence'] = None
    assert engine._terminal_import_failures({'text_mode':'glyphs'}, {'text_source_spans':1}, opts)


def test_actual_report_exempts_only_fully_authenticated_zero_ink_source(monkeypatch, tmp_path):
    from hashlib import sha256

    source = tmp_path/'synthetic.pdf'
    source.write_bytes(b'%PDF-1.4\nsynthetic zero-ink report binding fixture')
    collection, opts = zero_delivery(monkeypatch, sha256(source.read_bytes()).hexdigest())
    monkeypatch.setattr(engine, '_pymupdf_version', lambda: '')
    stats = dict(pages_imported=1, primitives=0, text_items=0, text_source_spans=1, collections=1, elapsed=.1)
    for damaged in (False, True):
        if damaged:
            collection.clear()
        out = tmp_path/f'report-{damaged}.json'
        engine.write_import_report(str(source), {'text_mode':'glyphs','import_text':True}, stats,
            import_mode='vector', output_path=str(out), provenance_opts=opts)
        report = json.loads(out.read_text())
        extra = report['extra']
        assert extra['text_representation_delivery']['verified'] is (not damaged)
        assert extra['text_representation_delivery']['verified_zero_ink_items'] == (0 if damaged else 1)
        assert ('source_text_seen_but_no_text_entities_created' in extra['diagnostics']['signals']) is damaged
