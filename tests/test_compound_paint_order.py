"""Opaque compound ordering retains exact holes and source paint occurrences."""
from types import SimpleNamespace as NS

import pymupdf as fitz
import pytest

from pdf_vector_importer.compound_paint_order import (
    _svg_covered_clips, plan_compound_fills, apply_compound_fills,
)
from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page


def source(tmp_path, later=b'', before=b'', clip=b'20 20 m 60 20 l 40 60 l h', mutate=None,
           allow_masks=False, mask_text=(31,65), after_text=None):
    path=tmp_path/'clip.pdf'
    with fitz.open() as doc:
        page=doc.new_page(width=100,height=100)
        x=doc.get_new_xref();doc.update_object(x,'<<>>')
        # Gray earlier line crosses the future opaque black triangle.
        stream=b'.5 G 1 w 40 0 m 40 80 l S\n'+before+b'\nq '+clip+b' W* n 0 g 10 10 60 60 re f Q\n'+later
        doc.update_stream(x,stream);page.set_contents(x)
        if allow_masks:
            page.insert_text(mask_text,'A',fontsize=5)
        if after_text:
            page.draw_rect(after_text, color=None, fill=(1,0,0))
        doc.save(path)
    with fitz.open(path) as doc:
        page=doc[0];data=extract_page(page,1,detect_arcs=False)
        if mutate:mutate(data)
        masks=[]
        if allow_masks:
            from pdf_vector_importer.opaque_rectangle_proof import plan_opaque_rectangles
            from pdf_vector_importer.opaque_rectangle_order import bind_rectangle_plans
            masks,_=bind_rectangle_plans(plan_opaque_rectangles(page,'a'*64),data,page.rect)
        return plan_compound_fills(page,data,'a'*64,later_rectangle_plans=masks)


def test_complete_compound_paint_qualifies_without_changing_source_contours(tmp_path):
    plans, unresolved=source(tmp_path)
    assert len(plans)==1 and not unresolved
    assert plans[0]['source_draw_order']==1 and len(plans[0]['contours'])==1
    assert plans[0]['later_unowned_paint_absent'] is True


def test_no_compound_plans_requires_no_native_access(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules,'bpy',None)
    monkeypatch.setitem(sys.modules,'mathutils',None)
    assert apply_compound_fills([],None,{})==[]


@pytest.mark.parametrize('later',[b'1 0 0 rg 30 30 5 5 re f',b'0 G 2 w 25 25 m 50 25 l S'])
def test_unknown_or_nonround_later_paint_blocks_compound(tmp_path,later):
    plans, unresolved=source(tmp_path,later=later)
    assert not plans and unresolved[0]['reason']=='later_source_paint_overlaps_compound'


def test_identical_outline_before_and_after_fill_uses_actual_adjacent_occurrence(tmp_path):
    line=b'0 G 1 J 1 j 1 w 20 20 m 60 20 l S'
    plans, _=source(tmp_path,before=line,later=line)
    assert len(plans)==1
    stroke=plans[0]['later_strokes'][0]
    assert stroke['source_draw_order']==3 and stroke['source_fill_anchor_seqno']==2
    assert stroke['svg_paint_ordinal']==stroke['svg_fill_anchor_ordinal']+1


def test_clipped_own_later_outline_cannot_borrow_earlier_complete_occurrence(tmp_path):
    line=b'0 G 1 J 1 j 1 w 20 20 m 60 20 l S'
    plans, unresolved=source(tmp_path,before=line,later=b'q 20 20 20 10 re W n '+line+b' Q')
    assert not plans and unresolved


def test_separately_qualified_later_white_mask_stays_a_required_later_dependency(tmp_path):
    plans,_=source(tmp_path,later=b'1 g 30 30 10 15 re f',allow_masks=True)
    assert len(plans)==1
    mask=plans[0]['later_rectangles'][0]
    assert mask['fill_rgb']==[1.,1.,1.] and mask['source_proof']['source_draw_order']==2
    from pdf_vector_importer.compound_paint_order import verify_required_later_masks
    config={'_required_compound_later_rectangle_orders':{2}}
    with pytest.raises(ValueError,match='dependency'):
        verify_required_later_masks(config,[{'status':'unqualified','source_draw_order':2}])
    verify_required_later_masks(config,[{'status':'applied','source_proof':mask['source_proof']}])


def test_later_text_stage_is_explicit_before_any_compound_move(monkeypatch):
    collection,spec,plan,config=native(monkeypatch)
    spec['object']['pdf_source_span_id']='future'
    plan['later_rectangles']=[{'later_text':[{'span_ids':['future']}]}]
    with pytest.raises(ValueError,match='precede later mask text'):
        apply_compound_fills([plan],collection,config)
    assert spec['object'].location.z==0


def test_finite_paint_union_does_not_sweep_empty_bbox_corner_into_later_dependency(tmp_path):
    later=b'0 G 1 J 1 j 1 w 20 20 m 60 20 l S 1 g 35 5 10 15 re f'
    plans,_=source(tmp_path,later=later,allow_masks=True,mask_text=(36,90),after_text=(22,92,27,94))
    assert len(plans)==1 and len(plans[0]['later_rectangles'])==1
    assert len(plans[0]['dependency_regions_mm'])==3
    # Moving that same unknown paint into the actual later mask must reject.
    plans,_=source(tmp_path,later=later,allow_masks=True,mask_text=(36,90),after_text=(37,91,42,93))
    assert not plans


def test_diagonal_round_stroke_bbox_corner_is_not_painted_source_area(tmp_path):
    later=b'0 G 1 J 1 j 1 w 60 20 m 40 60 l S 1 0 0 rg 50 60.49 3 2 re f'
    plans,_=source(tmp_path,later=later)
    assert len(plans)==1 and len(plans[0]['later_strokes'])==1
    assert any('owned_round_strokes_disjoint' in p for p in plans[0]['later_exclusions'])


@pytest.mark.parametrize('later',[
    b'1 w 60 20 m 40 60 l S 1 0 0 rg 40 60.2 1 2 re f',
    b'1 w 60 20 m 40 60 l S 1 0 0 rg 40.5 60 1 2 re f',
    b'24 w 60 20 m 40 60 l S 1 0 0 rg 50 60.49 3 2 re f',
])
def test_diagonal_stroke_crossing_tangent_or_wider_pen_is_never_excluded(tmp_path,later):
    plans, unresolved=source(tmp_path,later=b'0 G 1 J 1 j '+later)
    assert not plans and unresolved


def test_round_stroke_separation_requires_all_original_uncertainty_fields():
    from pdf_vector_importer.compound_paint_order import _round_stroke_misses_box
    p={'closed':False,'source_points_pdf':[(0.,0.),(10.,10.)],
       'source_point_errors_pdf':[(0.,0.),(0.,0.)],'source_width_pdf':1.}
    assert _round_stroke_misses_box(p,(8.,0.,9.,1.))
    for field in p:
        changed=dict(p);changed.pop(field)
        assert not _round_stroke_misses_box(changed,(8.,0.,9.,1.))
    assert not _round_stroke_misses_box(p|{'source_point_errors_pdf':[(20.,20.),(20.,20.)]},(8.,0.,9.,1.))


def test_compound_border_chain_joins_actual_round_paint_but_not_bbox_contact():
    from pdf_vector_importer.compound_paint_order import _disconnected_strokes
    contour=[(0.,0.),(1.,0.),(.5,1.)]
    first={'points_mm':[(0.,0.),(-1.,1.)],'width_mm':.1,'source_draw_order':1}
    chain={'points_mm':[(-1.,1.),(-2.,1.)],'width_mm':.1,'source_draw_order':2}
    assert _disconnected_strokes([contour],[chain,first])==[]
    separated=chain|{'points_mm':[(-1.,.5),(-2.,.5)]}
    assert _disconnected_strokes([contour],[separated,first])==[2]


def stroke_profile():
    data=NS(dimensions='3D',bevel_mode='ROUND',bevel_object=None,taper_object=None,
            offset=0.,extrude=0.,bevel_factor_start=0.,bevel_factor_end=1.,
            splines=[NS(points=[NS(radius=1.,tilt=0.),NS(radius=1.,tilt=0.)])])
    obj=NS(matrix_world=[[float(i==j) for j in range(4)] for i in range(4)],data=data)
    proof={'points_mm':[(0.,0.),(10.,10.)],'width_mm':1.}
    return obj,proof,[NS(x=0.,y=0.,z=0.),NS(x=.01,y=.01,z=0.)]


def test_native_capsule_profile_retains_translation_but_rejects_width_and_bbox_corner():
    from pdf_vector_importer.compound_paint_order import _native_round_stroke_profile
    obj,proof,paint=stroke_profile()
    obj.matrix_world[2][3]=.003
    _native_round_stroke_profile(obj,proof,paint)
    with pytest.raises(ValueError,match='source capsule'):
        _native_round_stroke_profile(obj,proof,paint+[NS(x=.01,y=0.,z=.003)])


def test_valid_diagonal_uses_actual_mesh_vertices_not_empty_aabb_corners():
    from test_image_paint_order_native import Vec
    from pdf_vector_importer.compound_paint_order import _actual_paint_vertices, _native_round_stroke_profile
    obj,proof,_=stroke_profile()
    class Matrix:
        def copy(self):return self
        def __matmul__(self,p):return Vec(p)
    actual=[(0.,0.,0.),(.01,.01,0.),(.0003,-.0003,0.),(.0097,.0103,0.)]
    cleared=[]
    evaluated=NS(matrix_world=Matrix(),
                 to_mesh=lambda:NS(vertices=[NS(co=p) for p in actual]),
                 to_mesh_clear=lambda:cleared.append(True))
    obj.evaluated_get=lambda _:evaluated
    obj.bound_box=[(.01,0.,0.)]  # deliberately outside the actual diagonal paint
    _native_round_stroke_profile(obj,proof,_actual_paint_vertices(obj,None))
    assert cleared==[True]
    evaluated.to_mesh=lambda:(_ for _ in ()).throw(RuntimeError('native conversion'))
    with pytest.raises(RuntimeError,match='native conversion'):
        _actual_paint_vertices(obj,None)
    assert cleared==[True,True]


@pytest.mark.parametrize('change',['transverse_scale','radius','tilt','custom','taper','offset','partial'])
def test_native_capsule_world_width_profile_cannot_be_forged(change):
    from pdf_vector_importer.compound_paint_order import _native_round_stroke_profile
    obj,proof,paint=stroke_profile()
    if change=='transverse_scale':obj.matrix_world[1][1]=2.
    elif change=='radius':obj.data.splines[0].points[0].radius=2.
    elif change=='tilt':obj.data.splines[0].points[0].tilt=1.
    elif change=='custom':obj.data.bevel_mode='OBJECT'
    elif change=='taper':obj.data.taper_object=NS()
    elif change=='offset':obj.data.offset=1.
    else:obj.data.bevel_factor_end=.5
    with pytest.raises(ValueError,match='profile'):
        _native_round_stroke_profile(obj,proof,paint)


def test_complete_hole_contours_required(tmp_path):
    clip=b'20 20 m 60 20 l 60 60 l 20 60 l h 30 30 m 50 30 l 50 50 l 30 50 l h'
    plans,_=source(tmp_path,clip=clip)
    assert len(plans)==1 and len(plans[0]['contours'])==2
    plans,_=source(tmp_path,clip=clip,mutate=lambda data:data.primitives.pop())
    assert not plans


@pytest.mark.parametrize('wrapper',[
    '<g opacity=".5">{}</g>', '<g mask="url(#unknown)">{}</g>',
    '<svg x="30">{}</svg>', '<style>path{{fill:blue}}</style>{}',
    '<clipPath id="c"><path d="M0 0H1V1H0Z"/></clipPath>{}',
])
def test_svg_unknown_style_or_clip_identity_cannot_qualify(wrapper):
    definitions='<defs><clipPath id="c"><path clip-rule="evenodd" d="M2 2H8L5 8Z"/></clipPath></defs>'
    paint='<g clip-path="url(#c)"><path fill="#000000" d="M0 0H10V10H0Z"/></g>'
    assert _svg_covered_clips('<svg>'+definitions+wrapper.format(paint)+'</svg>')==[]


def native(monkeypatch):
    from test_triangle_paint_order import native_case
    collection, arrow, compound, _, config=native_case(monkeypatch)
    arrow['object'].location.z = .002
    plan={'page':1,'source_draw_order':4,'source_sha256':'a'*64,'fill_rgb':(1.,1.,1.),
          'contours':[{'primitive_id':'white','points_mm':compound['contours_mm'][0]}],
          'dependency_bounds_mm':(0.,0.,10.,10.), 'later_strokes':[],
          'later_unowned_paint_absent':True}
    return collection,compound,plan,config


def test_native_complete_compound_moves_without_editing_any_local_point(monkeypatch):
    collection,spec,plan,config=native(monkeypatch)
    before=[tuple(p.co) for p in spec['object'].data.splines[0].points]
    result=apply_compound_fills([plan],collection,config)
    assert result[0]['status']=='applied'
    assert [tuple(p.co) for p in spec['object'].data.splines[0].points]==before
    assert spec['object'].location.z>0


@pytest.mark.parametrize('change',['missing','point','alpha','order','visible'])
def test_native_compound_tampering_never_moves_source(monkeypatch,change):
    collection,spec,plan,config=native(monkeypatch)
    if change=='missing':spec['object'].data.splines=[]
    elif change=='point':spec['object'].data.splines[0].points[0].co[0]+=1
    elif change=='alpha':spec['fill_opacity']=.5
    elif change=='order':spec['source_draw_order']=None
    else:spec['object'].hide_render=True
    with pytest.raises(ValueError,match='Compound'):
        apply_compound_fills([plan],collection,config)
    assert spec['object'].location.z==0
