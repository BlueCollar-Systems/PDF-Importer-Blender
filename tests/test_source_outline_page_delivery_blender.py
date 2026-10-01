"""Page omission evidence must remain bound through delivery and report creation."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from test_source_outline_delivery_blender import engine, text, native
from test_source_outline_page_evidence import orphan_page, Collection
from pdf_vector_importer.source_text_outlines import (
    digest, page_record_provider, persist_page_ledger, qualify_page,
)
from pdf_vector_importer.text_delivery import AttemptOutcome


class Collections(list):
    def get(self, name):
        return next((value for value in self if value.name == name), None)


def setup(monkeypatch, pdf_hash='a'*64):
    page, items = orphan_page()
    result = qualify_page(page, items, page_number=1, width_mm=100, height_mm=100, pdf_sha256=pdf_hash)
    source = result['records']['page:1:text:3']
    collection = Collection('PDF_Page_1')
    persist_page_ledger(collection, result['page_occurrence_ledger'])
    registry = Collections([collection])
    monkeypatch.setattr(engine, 'bpy', NS(data=NS(collections=registry)))
    evidence = {'outline_source': 'source_renderer_svg', 'page_collection': collection.name,
                'source_outline_sha256': source['source_outline_sha256'],
                'source_page_ledger': source['source_page_ledger']}
    record = {'item_id': source['item_id'], 'page': 1, 'source_span_id': 3,
        'status': 'delivered', 'requested_representation': 'glyphs', 'final_representation': 'glyphs',
        'entity_ids': ['outline'], 'attempts': [{'status': 'delivered', 'evidence': evidence}]}
    return collection, registry, source, record, items[0]


def test_provider_persists_ledger_once_and_text_attempt_carries_only_its_reference(monkeypatch):
    collection, _registry, source, _record, row = setup(monkeypatch)
    page, items = orphan_page()
    provider = page_record_provider(page, items, page_number=1, width_mm=100, height_mm=100,
                                    pdf_sha256='a'*64, collection=collection)
    before = dict(collection)
    assert provider(row)['source_outline_sha256'] == source['source_outline_sha256']
    assert dict(collection) == before
    monkeypatch.setattr(native, 'build_source_outlines', lambda *_a, **_k: AttemptOutcome.delivered('curve'))
    outcome = text._attempt_one_representation('glyphs', row, collection, effective_page=1,
        requested='3d_text', item_id=source['item_id'], visual_style='source', z_offset_m=0.,
        terminal_raster_callback=None, source_outline_callback=provider)
    assert outcome.evidence['source_page_ledger'] == source['source_page_ledger']
    assert outcome.evidence['page_collection'] == collection.name
    assert 'omissions' not in outcome.evidence
    collection.clear()
    monkeypatch.setattr(native, 'build_source_outlines', lambda *_a, **_k: pytest.fail('native allocation'))
    failure = text._attempt_one_representation('glyphs', row, collection, effective_page=1,
        requested='3d_text', item_id=source['item_id'], visual_style='source', z_offset_m=0.,
        terminal_raster_callback=None, source_outline_callback=provider)
    assert failure.status == 'failed'
    assert failure.reason == 'source_page_ledger_persistence_failed'


def test_failed_page_storage_is_terminal_not_source_qualification_unavailable(monkeypatch):
    class LostWrite(Collection):
        def __setitem__(self, key, value):
            pass
    page, items = orphan_page()
    collection = LostWrite('Broken_Page')
    provider = page_record_provider(page, items, page_number=1, width_mm=100, height_mm=100,
                                    pdf_sha256='a'*64, collection=collection)
    monkeypatch.setattr(native, 'build_source_outlines', lambda *_a, **_k: pytest.fail('native allocation'))
    with pytest.raises(RuntimeError, match='persistence failed'):
        text._attempt_one_representation('glyphs', items[0], collection, effective_page=1,
            requested='glyphs', item_id='page:1:text:3', visual_style='source', z_offset_m=0.,
            terminal_raster_callback=None, source_outline_callback=provider)


@pytest.mark.parametrize('damage', ['missing', 'live_edit', 'wrong_owner', 'duplicate', 'page_swap', 'reference_removed'])
def test_actual_report_rechecks_live_ledger_and_cannot_keep_ready(monkeypatch, tmp_path, damage):
    pdf = tmp_path/'synthetic.pdf'
    pdf.write_bytes(b'%PDF-1.4\nsource page evidence fixture')
    collection, registry, source, record, _row = setup(monkeypatch, hashlib.sha256(pdf.read_bytes()).hexdigest())
    opts = NS(_text_delivery_records=[record])
    monkeypatch.setattr(engine, '_pymupdf_version', lambda: '')
    stats = dict(pages_imported=1, primitives=0, text_items=1, text_source_spans=1, collections=1, elapsed=.1)
    def report(name):
        path = tmp_path/name
        engine.write_import_report(str(pdf), {'text_mode':'glyphs','import_text':True}, stats,
            import_mode='vector', output_path=str(path), provenance_opts=opts)
        return json.loads(path.read_text())['extra']['text_representation_delivery']
    assert report('before.json')['verified'] is True
    if damage == 'missing':
        collection.clear()
    elif damage == 'live_edit':
        value = json.loads(collection['pdf_source_page_ledger_json'])
        value['ledger']['omissions'].clear()
        collection['pdf_source_page_ledger_json'] = json.dumps(value)
        collection['pdf_source_page_ledger_sha256'] = digest(value)
    elif damage == 'wrong_owner':
        collection.name = 'Other_Page'
    elif damage == 'duplicate':
        registry.append(Collection('Copy', **dict(collection)))
    elif damage == 'page_swap':
        record['attempts'][0]['evidence']['source_page_ledger'] = {**source['source_page_ledger'], 'page_number': 2}
    else:
        del record['attempts'][0]['evidence']['source_page_ledger']
        record['final_state_verification'] = {'entities': [{'source_outline': {
            'source_page_ledger': source['source_page_ledger']}}]}
    after = report('after.json')
    assert after['verified'] is False
    assert after['failed_items'] == 1 and after['delivered_items'] == after['verified_zero_ink_items'] == 0


def test_report_binds_current_pdf_without_changing_source_or_physical_counts(monkeypatch, tmp_path):
    pdf = tmp_path/'synthetic.pdf'
    pdf.write_bytes(b'original')
    _collection, _registry, _source, record, _row = setup(monkeypatch, hashlib.sha256(pdf.read_bytes()).hexdigest())
    opts = NS(_text_delivery_records=[record])
    assert engine._text_delivery_from_provenance(opts, source_pdf_path=pdf)['summary']['delivered_items'] == 1
    pdf.write_bytes(b'changed')
    summary = engine._text_delivery_from_provenance(opts, source_pdf_path=pdf)['summary']
    assert summary['source_items'] == summary['failed_items'] == 1
    assert summary['delivered_items'] == summary['verified_zero_ink_items'] == 0


def test_canonical_zero_ink_keeps_page_orphans_separate_and_rechecks_their_ledger(monkeypatch):
    page, items = orphan_page()
    row = items[0]
    row.text = row.source_char_layout[0].text = ' '
    page.source = page.source.replace('data-text="A"', 'data-text=" "').replace(
        '<path id="font_1_7" d="M0 0L1 0L1 1L0 1Z"/>', '<path id="font_1_7" d="M0 0Z"/>')
    collection = Collection('Zero_Page')
    provider = page_record_provider(page, items, page_number=1, width_mm=100, height_mm=100,
                                    pdf_sha256='a'*64, collection=collection)
    monkeypatch.setattr(text, '_attempt_one_representation', lambda *_a, **_k: pytest.fail('native attempt'))
    opts = NS()
    assert text.build_text(row, collection, page_number=1, text_mode='glyphs',
        provenance_opts=opts, source_outline_callback=provider) is None
    monkeypatch.setattr(engine, 'bpy', NS(data=NS(collections=Collections([collection]))))
    result = engine._text_delivery_from_provenance(opts)
    assert result['summary']['source_items'] == result['summary']['verified_zero_ink_items'] == 1
    assert result['summary']['delivered_items'] == result['summary']['failed_items'] == 0
    assert len(json.loads(collection['pdf_source_page_ledger_json'])['ledger']['omissions']) == 1
    del collection['pdf_source_page_ledger_json']
    result = engine._text_delivery_from_provenance(opts)
    assert result['summary']['failed_items'] == 1 and result['summary']['verified_zero_ink_items'] == 0


def test_final_native_readback_reference_must_match_independent_page_evidence(monkeypatch):
    collection, registry, source, record, _row = setup(monkeypatch)
    obj = NS(name='outline', type='CURVE', location=(0., 0., 0.))
    monkeypatch.setattr(engine, 'bpy', NS(data=NS(collections=registry, objects=NS(get=lambda _: obj)),
        context=NS(view_layer=NS(update=lambda: None))))
    matrix = [[1.,0.,0.,0.],[0.,1.,0.,0.],[0.,0.,1.,0.],[0.,0.,0.,1.]]
    evidence = record['attempts'][0]['evidence']
    evidence['placements'] = [{'entity_id':'outline', 'creation_world_matrix':matrix,
        'source_outline_sha256':source['source_outline_sha256'], 'source_placement_index':0}]
    proof = {'verified':True, 'source_outline_sha256':source['source_outline_sha256'],
             'source_placement_index':0, 'source_page_ledger':source['source_page_ledger']}
    monkeypatch.setattr(native, 'verify_source_outline_entity', lambda *_a, **_k: proof)
    assert engine._reverify_text_delivery_after_stack([record], page_number=1, stack_offset_m=0.) == []
    damaged = deepcopy(record)
    proof = {**proof, 'source_page_ledger': {**source['source_page_ledger'], 'ledger_sha256':'0'*64}}
    assert engine._reverify_text_delivery_after_stack([damaged], page_number=1, stack_offset_m=0.)
    assert damaged['status'] == 'failed'
