from copy import deepcopy
import json

import pytest

from pdf_vector_importer.source_text_outlines import (OutlineUnavailable, qualify_page, svg_placements,
    page_ledger_reference, persist_page_ledger, verify_page_ledger_collection)
from test_source_text_outlines import Page, item, svg, qualify


def orphan_page(*, empty=False, duplicate=False, traced=True):
    original = item()
    extra = item(origin=(50., 60.), text='B')
    extra[0].id = 8
    extra[0].source_char_layout[0].glyph_id = 8
    definition = '<path id="font_1_8" d="M0 0Z"/>' if empty else '<path id="font_1_8" d="M0 0H1V1H0Z"/>'
    definition += '<clipPath id="clip"><path d="M0 0H30V30H0Z"/></clipPath>'
    use = '<use data-text="B" href="#font_1_8" transform="matrix(1,0,0,1,50,60)"/>'
    source = svg(defs=definition, group='clip-path="url(#clip)"', uses=
        '<use data-text="A" href="#font_1_7" transform="matrix(1,0,0,1,10,20)"/>' + use*(2 if duplicate else 1))
    return Page(source, [original, extra] if traced else [original]), [original[0]]


def qualify_orphan(**kw):
    page, rows = orphan_page(**kw)
    return qualify_page(page, rows, page_number=1, width_mm=100, height_mm=100, pdf_sha256='a'*64)


@pytest.mark.parametrize('empty', [False, True])
def test_extra_invisible_use_has_one_page_ledger_and_retains_original_trace(empty):
    result = qualify_orphan(empty=empty)
    ledger = result['page_occurrence_ledger']
    assert ledger['placement_count'] == 2
    assert ledger['canonical_ownership'][0]['index'] == 0
    row = ledger['omissions'][0]
    assert row['original_trace']['trace_id'] == [1, 0]
    assert row['original_trace']['font'] == 'Example'
    assert row['original_trace']['source_origin'] == (50., 60.)
    assert row['reason'] == ('empty_source_definition' if empty else 'strictly_clipped_source_hull')
    assert result['records']['page:1:text:3']['source_page_ledger'] == page_ledger_reference(ledger)
    assert len(result['records']['page:1:text:3']['placements']) == 1
    assert 'omissions' not in result['records']['page:1:text:3']


@pytest.mark.parametrize('kw', [{'duplicate': True}, {'traced': False}])
def test_orphan_trace_cannot_be_missing_or_reused(kw):
    with pytest.raises(OutlineUnavailable, match='ownership'):
        qualify_orphan(**kw)


def test_visible_extra_and_canonical_shared_occurrence_are_not_omitted():
    page, rows = orphan_page()
    page.source = page.source.replace('clip-path="url(#clip)"', '')
    with pytest.raises(OutlineUnavailable, match='ownership'):
        qualify_page(page, rows, page_number=1, width_mm=100, height_mm=100, pdf_sha256='a'*64)
    source = svg(path='M0 0Z', uses=(
        '<use data-text="A" href="#font_1_7" transform="matrix(1,0,0,1,10,20)"/>'*2))
    with pytest.raises(OutlineUnavailable, match='ownership'):
        qualify(source)


def test_nonglyph_multiply_branch_is_separate_but_its_references_are_authenticated():
    base = svg()
    branch = '<g style="mix-blend-mode:multiply"><path fill="yellow" d="M0 0H3V3H0Z"/></g>'
    assert qualify(base.replace('</svg>', branch+'</svg>'))['placement_count'] == 1
    for bad in (
        branch.replace('</g>', '<use href="#font_1_7"/></g>'),
        branch.replace('</g>', '<use href="#missing"/></g>'),
        '<g style="mix-blend-mode:multiply" filter="url(#hidden)"/>',
    ):
        source = base.replace('</defs>', '<filter id="hidden"><feImage href="#font_1_7"/></filter></defs>')
        with pytest.raises(OutlineUnavailable):
            qualify(source.replace('</svg>', bad+'</svg>'))


def test_reference_group_and_cycle_cannot_conceal_glyph_paint():
    for definition, branch in (
        ('<g id="other"><use href="#font_1_7"/></g>', '<use href="#other"/>'),
        ('<g id="one"><use href="#two"/></g><g id="two"><use href="#one"/></g>', '<use href="#one"/>'),
    ):
        with pytest.raises(OutlineUnavailable):
            qualify(svg(defs=definition).replace('</svg>', branch+'</svg>'))


@pytest.mark.parametrize('branch', ['<text>Unproved text</text>',
    '<image href="data:image/svg+xml;base64,PHRleHQ+QQ=="/>'])
def test_nonglyph_pruning_does_not_ignore_alternate_text_or_active_images(branch):
    with pytest.raises(OutlineUnavailable):
        qualify(svg().replace('</svg>', branch+'</svg>'))


def test_live_glyph_direct_blend_attribute_and_conflicting_href_are_not_ignored():
    with pytest.raises(OutlineUnavailable):
        qualify(svg(group='mix-blend-mode="multiply"'))
    source = svg(defs='<path id="font_1_8" d="M0 0Z"/>').replace(
        '<svg ', '<svg xmlns:xlink="http://www.w3.org/1999/xlink" ').replace(
        'href="#font_1_7"', 'href="#font_1_7" xlink:href="#font_1_8"')
    with pytest.raises(OutlineUnavailable):
        qualify(source)


def clip_source(path, *, glyph='M0 0H1V1H0Z', origin='2,2', rule='evenodd', transform=''):
    return svg(path=glyph, defs=f'<clipPath id="clip"><path clip-rule="{rule}" d="{path}" {transform}/></clipPath>',
        uses=f'<use data-text="A" href="#font_1_7" transform="matrix(1,0,0,1,{origin})"/>',
        group='clip-path="url(#clip)"')


def test_evenodd_hole_accepts_only_complete_hull_inside_fill_or_strictly_excluded():
    path = 'M0 0H20V20H0Z M5 5H10V10H5Z M12 12H15V15H12Z'
    assert svg_placements(clip_source(path))[1][0]['fully_clipped'] == []
    proof = svg_placements(clip_source(path, origin='6,6'))[1][0]['fully_clipped']
    assert proof[0]['proof'] == 'exact_source_control_hull_strictly_inside_evenodd_hole'
    for origin in ('4.5,6', '4,6', '5,6'):
        with pytest.raises(OutlineUnavailable, match='partially'):
            svg_placements(clip_source(path, origin=origin))
    # All four controls lie outside the hole, but their hull crosses it.
    with pytest.raises(OutlineUnavailable, match='partially'):
        svg_placements(clip_source(path, origin='0,0', glyph='M4 4C4 11 11 11 11 4Z'))


@pytest.mark.parametrize('path', [
    'M0 0H20V20H0Z M0 5H10V10H0Z',
    'M0 0H20V20H0Z M5 5H10V10H5Z M9 9H12V12H9Z',
    'M0 0H20V20H0Z M5 5H10V10H5Z M10 5H12V8H10Z',
    'M0 0H20V20H0Z M5 5H10V10H5Z M6 6H8V8H6Z',
    'M0 0H20V20H0Z M5 5H10V10H5',
    'M0 0H20V20H0Z M5 5C10 5 10 10 5 10Z',
])
def test_unproved_holes_remain_unavailable(path):
    with pytest.raises(ValueError):
        svg_placements(clip_source(path))


def test_holes_need_evenodd_and_survive_exact_shear_and_reflection():
    path = 'M0 0H20V20H0Z M5 5H10V10H5Z'
    with pytest.raises(OutlineUnavailable):
        svg_placements(clip_source(path, rule='nonzero'))
    source = clip_source(path, origin='6,6')
    source = source.replace('<g clip-path', '<g transform="matrix(-1,0,0.25,1,30,0)" clip-path')
    assert svg_placements(source)[1][0]['fully_clipped'][0]['proof'].endswith('evenodd_hole')


class Collection(dict):
    def __init__(self, name='Page_1', **kw):
        super().__init__(**kw)
        self.name = name


@pytest.mark.parametrize('damage', ['missing', 'tamper', 'owner', 'copy', 'swapped_page'])
def test_page_ledger_persistence_rejects_missing_changed_and_duplicate_owners(damage):
    result = qualify_orphan()
    ledger, collection = result['page_occurrence_ledger'], Collection()
    ref = page_ledger_reference(ledger)
    persist_page_ledger(collection, ledger)
    assert verify_page_ledger_collection(collection, ref)['orphan_omissions'] == 1
    reopened = Collection(**json.loads(json.dumps(collection)))
    assert verify_page_ledger_collection(reopened, ref)['verified']
    registry = [reopened]
    if damage == 'missing':
        reopened.clear()
    elif damage == 'tamper':
        reopened['pdf_source_page_ledger_json'] += ' '
        data = json.loads(reopened['pdf_source_page_ledger_json'])
        data['ledger']['omissions'][0]['original_trace']['font'] = 'Changed'
        reopened['pdf_source_page_ledger_json'] = json.dumps(data)
    elif damage == 'owner':
        reopened.name = 'Other_Page'
    elif damage == 'copy':
        registry.append(Collection('Duplicate', **dict(reopened)))
    else:
        ref = {**ref, 'page_number': 2}
    with pytest.raises(OutlineUnavailable):
        verify_page_ledger_collection(reopened, ref, collections=registry)


def test_forged_page_census_rejects_duplicate_trace_and_missing_occurrence():
    original = qualify_orphan()['page_occurrence_ledger']
    for kind in ('duplicate', 'missing', 'visible', 'origin'):
        ledger = deepcopy(original)
        row = ledger['omissions'][0]
        if kind == 'duplicate':
            row['original_trace']['trace_id'] = ledger['canonical_ownership'][0]['trace_id']
        elif kind == 'missing':
            ledger['placement_count'] = 3
        elif kind == 'visible':
            row['placement']['fully_clipped'] = []
        else:
            row['original_trace']['source_origin'] = [52., 60.]
        with pytest.raises(OutlineUnavailable):
            page_ledger_reference(ledger)
