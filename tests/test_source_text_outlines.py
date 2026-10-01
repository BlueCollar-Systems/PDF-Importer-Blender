"""Source ownership and paint proofs, independent of Blender's object builder."""
from types import SimpleNamespace as NS

import pytest

from pdf_vector_importer.source_text_outlines import (
    OutlineUnavailable, _float32, digest, missing_font_evidence, parse_path,
    qualify_page,
    zero_ink_proof,
)


def svg(*, path='M0 0L1 0L1 1L0 1Z', uses=None, defs='', group=''):
    uses = uses if uses is not None else '<use data-text="A" href="#font_1_7" transform="matrix(1,0,0,1,10,20)"/>'
    return f'<svg viewBox="0 0 100 100"><defs><path id="font_1_7" d="{path}"/>{defs}</defs><g {group}>{uses}</g></svg>'


def item(*, origin=(10., 20.), text='A', synthetic=False):
    bbox = (*origin, origin[0]+1, origin[1]+1)
    char = NS(text=text, source_origin_pdf=origin, source_bbox_pdf=bbox,
              target_origin=(origin[0], 100-origin[1]), glyph_id=7)
    failure = NS(reason='embedded_font_asset_build_failed',
                 proof_category='source_specific_impossibility',
                 detail='embedded font stream is empty', source_xref=4,
                 page_number=1, span_font_name='Example')
    return NS(id=3, page_number=1, text=text, font_name='Example',
              font_asset=None, font_failure=failure, source_char_layout=(char,)), synthetic


class Page:
    rotation_matrix = (1, 0, 0, 1, 0, 0)

    def __init__(self, source, rows):
        self.source = source
        self.rows = rows

    def get_svg_image(self, *, text_as_path):
        assert text_as_path is True
        return self.source

    def get_text(self, kind):
        assert kind == 'rawdict'
        return {'blocks': [{'type': 0, 'lines': [{'spans': [
            {'font': row.font_name, 'chars': [dict(c=c.text, origin=c.source_origin_pdf,
              bbox=c.source_bbox_pdf, synthetic=synthetic) for c in row.source_char_layout]}
            for row, synthetic in self.rows]}]}]}

    def get_texttrace(self):
        return [{'font': row.font_name, 'chars': [(ord(c.text), c.glyph_id,
                 c.source_origin_pdf, c.source_bbox_pdf) for c in row.source_char_layout]}
                for row, synthetic in self.rows if not synthetic]


def qualify(source=None, rows=None):
    rows = rows or [item()]
    return qualify_page(Page(source or svg(), rows), [row for row, _ in rows],
                        page_number=1, width_mm=100, height_mm=100, pdf_sha256='a'*64)


def test_complete_source_record_keeps_controls_identity_and_hash():
    result = qualify()
    record = result['records']['page:1:text:3']
    bound = record['placements'][0]
    assert bound['character_index'] == 0
    assert bound['contours'][0]['start'] == (10., 80.)
    assert bound['contours'][0]['segments'][-1] == ('L', (10., 80.))
    assert bound['binding_method'] == 'exact_float32_source_origin_roundtrip'
    assert record['source_font_absence']['source_xref'] == 4
    assert record['source_outline_sha256'] == digest({k:v for k,v in record.items() if k != 'source_outline_sha256'})


def test_svg_decimal_must_round_to_exact_original_float32():
    rows = [item(origin=(_float32(10.12345), 20.))]
    source = svg(uses='<use data-text="A" href="#font_1_7" transform="matrix(1,0,0,1,10.12345,20)"/>')
    assert qualify(source, rows)['placement_count'] == 1
    with pytest.raises(OutlineUnavailable, match='ownership'):
        qualify(source.replace('10.12345,20', '10.12346,20'), rows)


@pytest.mark.parametrize('alter', [
    lambda value: value.replace('data-text="A"', 'data-text="B"'),
    lambda value: value.replace('href="#font_1_7"', 'href="#font_1_8"'),
    lambda value: value.replace('</g>', '<use data-text="A" href="#font_1_7" transform="matrix(1,0,0,1,10,20)"/></g>'),
    lambda value: value.replace('10,20)', '11,20)'),
])
def test_extra_wrong_or_duplicate_occurrences_fail(alter):
    with pytest.raises(OutlineUnavailable):
        qualify(alter(svg()))


def test_missing_occurrence_and_duplicate_canonical_owner_fail():
    with pytest.raises(OutlineUnavailable, match='absent'):
        qualify(svg(uses=''))
    with pytest.raises(OutlineUnavailable, match='duplicate'):
        row = item()
        qualify_page(Page(svg(), [row]), [row[0], row[0]], page_number=1,
                     width_mm=100, height_mm=100, pdf_sha256='a'*64)


def test_empty_source_path_is_occurrence_bound_without_native_contours():
    record = qualify(svg(path='M0 0Z'))['records']['page:1:text:3']
    assert record['placements'] == []
    assert record['empty_placements'][0]['glyph_id'] == 7


def test_only_original_synthetic_space_without_trace_can_be_excluded():
    row, _ = item(text=' ', synthetic=True)
    record = qualify(svg(uses=''), [(row, True)])['records']['page:1:text:3']
    assert len(record['synthetic_extraction_spaces']) == 1
    with pytest.raises(OutlineUnavailable, match='absent'):
        qualify(svg(uses=''), [(row, False)])
    row, _ = item(text='X', synthetic=True)
    with pytest.raises(OutlineUnavailable, match='synthetic'):
        qualify(svg(uses=''), [(row, True)])


@pytest.mark.parametrize('effect', ['filter="url(#flood)"', 'mask="url(#mask)"',
                                  'opacity="0.5"', 'stroke="red"', 'fill="url(#paint)"'])
def test_effects_cannot_turn_empty_outline_into_false_zero_ink(effect):
    with pytest.raises(OutlineUnavailable):
        qualify(svg(path='M0 0Z', group=effect))


def test_clip_uses_ancestor_frame_and_exact_lexical_bounds():
    defs = '<clipPath id="clip"><path d="M0 0H2V2H0Z"/></clipPath>'
    use = '<use data-text="A" href="#font_1_7"/>'
    source = svg(defs=defs, uses=use, group='transform="matrix(1,0,0,1,10,20)" clip-path="url(#clip)"')
    assert qualify(source)['records']['page:1:text:3']['placements']
    # This gap is much smaller than one float ULP, but source clipping is exact.
    source = source.replace('H2V2', 'H0.999999999999999999999999V2')
    with pytest.raises(OutlineUnavailable, match='partially'):
        qualify(source)


def test_strictly_outside_clip_retains_bound_occurrence_proof():
    defs = '<clipPath id="clip"><path d="M0 0H2V2H0Z"/></clipPath>'
    record = qualify(svg(defs=defs, group='clip-path="url(#clip)"'))['records']['page:1:text:3']
    assert not record['placements']
    assert record['clipped_placements'][0]['fully_clipped'][0]['proof'].startswith('exact_source')


@pytest.mark.parametrize('path', ['M0 0L1 0', 'M0 0H1V1H0Z M1.000000000000000001 0H2V1H1.000000000000000001Z',
                                  'M0 0H2V2H0Z M1 0H3V2H1Z', 'M0 0L1 1L0 1L1 0Z'])
def test_unproved_clip_region_fails(path):
    defs = f'<clipPath id="clip"><path d="{path}"/></clipPath>'
    with pytest.raises(ValueError):
        qualify(svg(defs=defs, group='clip-path="url(#clip)"'))


def test_paths_preserve_cubic_support_and_opposite_counter_winding():
    contours = parse_path('M0 0C1 0 2 1 2 2L0 2Z M.5 .5V1.5H1.5V.5Z')
    assert len(contours) == 2
    assert contours[0]['segments'][0] == ('C', (1., 0.), (2., 1.), (2., 2.))
    assert contours[1]['segments'][0] == ('L', (.5, 1.5))


@pytest.mark.parametrize('path', ['M0', 'L1 1', 'M0 0A1 1 0 0 0 2 2', 'M0 0Lnan 1', 'M0 0L1e1000 1', 'M0 0L1e-10000 1'])
def test_malformed_or_unbounded_paths_are_unavailable(path):
    with pytest.raises(ValueError):
        parse_path(path, exact=True)


def test_absence_gate_rejects_ambiguous_corrupt_wrong_page_and_real_program():
    row, _ = item()
    assert missing_font_evidence(row, 1)
    assert missing_font_evidence(row, 2) is None
    row.font_failure.detail = 'font program is corrupt'
    assert missing_font_evidence(row, 1) is None
    row.font_failure.detail = 'embedded font stream is empty'
    row.font_asset = object()
    assert missing_font_evidence(row, 1) is None


def test_zero_ink_requires_complete_original_occurrence_evidence():
    row, _ = item(text=' ')
    source = svg(path='M0 0Z', uses='<use data-text=" " href="#font_1_7" transform="matrix(1,0,0,1,10,20)"/>')
    record = qualify(source, [(row, False)])['records']['page:1:text:3']
    assert zero_ink_proof(record)['native_entities_created'] == 0
    record['empty_placements'] = []
    record['source_outline_sha256'] = digest({key:value for key,value in record.items() if key != 'source_outline_sha256'})
    with pytest.raises(OutlineUnavailable, match='coverage'):
        zero_ink_proof(record)
