"""Bounded native import/save-open check for the fictional vector fixture.

Run with Blender --background --factory-startup --python-exit-code 1.
Desktop clicks and other PDFs remain separate acceptance scopes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--bad-model")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    import bpy
    from pdf_vector_importer.dependency_manager import ensure_pymupdf_runtime
    assert ensure_pymupdf_runtime(auto_install=False)
    import pymupdf
    from pdf_vector_importer import bl_import_engine
    from pdf_vector_importer.build_identity import runtime_package_identity
    from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page

    output=Path(args.out)
    output.mkdir(parents=True,exist_ok=False)
    pdf=Path(args.pdf)
    sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
    identity=runtime_package_identity(root/'pdf_vector_importer')
    with pymupdf.open(pdf) as doc:
        data=extract_page(doc[0],1)
    green=[p for p in data.primitives if p.fill_color is not None
           and all(abs(a-b)<1e-6 for a,b in zip(p.fill_color,(.08,.48,.2),strict=True))]
    assert len(green)==2
    hole=min(green,key=lambda p:p.area).bbox
    hole_center=((hole[0]+hole[2])/2000,(hole[1]+hole[3])/2000)
    expected_area=max(p.area for p in green)-min(p.area for p in green)
    red=[p for p in data.primitives if p.stroke_color is not None
         and all(abs(a-b)<1e-6 for a,b in zip(p.stroke_color,(.85,.12,.08),strict=True))]
    assert len(red)==1
    red=red[0]
    radius=red.line_width/2
    length=math.dist(*red.points)
    expected_capsule_area=red.line_width*length+math.pi*radius**2
    expected_bounds=(min(p[0] for p in red.points)-radius,min(p[1] for p in red.points)-radius,
                     max(p[0] for p in red.points)+radius,max(p[1] for p in red.points)+radius)

    if args.bad_model:
        bad=Path(args.bad_model)
        bad_sha=hashlib.sha256(bad.read_bytes()).hexdigest()
        bpy.ops.wm.open_mainfile(filepath=str(bad))
        stats={}
    else:
        stats=bl_import_engine.import_pdf(str(pdf),config={"mode":"auto","text_mode":"3d_text",
            "visual_style":"source","white_page_background":True,"group_by_color":True,
            "auto_focus_view":False,"import_report_path":str(output/'import-report.json')})
    bpy.context.view_layer.update()

    def snapshot():
        green_objects=[];round_objects=[];all_native=[]
        for obj in bpy.context.scene.objects:
            if obj.type not in ("CURVE","MESH"):
                continue
            is_green=any(m and m.name.startswith('PDF_source_0.08_0.48_0.20') for m in obj.data.materials)
            is_round=bool(obj.get('pdf_normal_round_stroke'))
            if not(is_green or is_round):
                continue
            evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            mesh=evaluated.to_mesh()
            try:
                mesh.calc_loop_triangles()
                triangle_points=[];areas=[];covered=False
                for tri in mesh.loop_triangles:
                    points=[tuple((obj.matrix_world@mesh.vertices[i].co)[:2]) for i in tri.vertices]
                    cross=lambda a,b,c:(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
                    areas.append(abs(cross(*points))/2*1e6)
                    signs=[cross(points[i],points[(i+1)%3],hole_center) for i in range(3)]
                    covered |= all(v>=0 for v in signs) or all(v<=0 for v in signs)
                    triangle_points.extend(points)
                assert triangle_points
                bounds=[min(p[0] for p in triangle_points)*1000,min(p[1] for p in triangle_points)*1000,
                        max(p[0] for p in triangle_points)*1000,max(p[1] for p in triangle_points)*1000]
                item={"name":obj.name,"type":obj.type,"area_mm2":sum(areas),
                      "triangles":len(areas),"hole_center_painted":covered,"bounds_mm":bounds,
                      "parent":obj.parent.name if obj.parent else None,
                      "matrix_world":[list(row) for row in obj.matrix_world],
                      "location":list(obj.location),"materials":[{"name":m.name,"rgba":list(m.diffuse_color)} for m in obj.data.materials],
                      "splines":[{"type":s.type,"cyclic":s.use_cyclic_u,"order":s.order_u,
                                  "endpoint":s.use_endpoint_u,"bezier":s.use_bezier_u,
                                  "points":[list(p.co) for p in s.points],
                                  "bezier_points":[{"co":list(p.co),"left":list(p.handle_left),
                                                    "right":list(p.handle_right),"types":[p.handle_left_type,p.handle_right_type]}
                                                   for p in s.bezier_points]} for s in obj.data.splines] if obj.type=='CURVE' else []}
                all_native.append(item)
                if is_green:green_objects.append(item)
                if is_round:round_objects.append(item)
            finally:
                evaluated.to_mesh_clear()
        return {"green_objects":green_objects,"round_objects":round_objects,"native":all_native,
                "green_area_mm2":sum(o['area_mm2'] for o in green_objects),
                "hole_center_painted":any(o['hole_center_painted'] for o in green_objects)}

    before=snapshot()
    if args.bad_model:
        after=before
        blend=bad
    else:
        blend=output/'native-vector-fidelity.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        bpy.ops.wm.open_mainfile(filepath=str(blend))
        after=snapshot()
    caps=after['round_objects']
    result={"schema":"bcs.blender-vector-fidelity-native/1","host_version":bpy.app.version_string,
            "headless":True,"desktop_ui_acceptance":False,"read_only_historical_model":bool(args.bad_model),
            "source_identity":identity,"pdf":str(pdf),"pdf_sha256":sha,
            "expected_green_area_mm2":expected_area,"expected_round_area_mm2":expected_capsule_area,
            "expected_round_bounds_mm":expected_bounds,"before_save":before,"after_reopen":after,
            "save_reopen_equal":before==after,"blend":str(blend),
            "blend_sha256":hashlib.sha256(blend.read_bytes()).hexdigest(),
            "round_records":stats.get('source_round_stroke_footprints',()),
            "source_unchanged":hashlib.sha256(pdf.read_bytes()).hexdigest()==sha,
            "source_identity_unchanged":runtime_package_identity(root/'pdf_vector_importer')==identity}
    result['passed']=(before==after and len(after['green_objects'])==1
                      and not after['hole_center_painted'] and abs(after['green_area_mm2']-expected_area)<.001
                      and len(caps)==1 and abs(caps[0]['area_mm2']-expected_capsule_area)<.05
                      and max(abs(a-b) for a,b in zip(caps[0]['bounds_mm'],expected_bounds,strict=True))<.001)
    if args.bad_model:
        result['historical_model_unchanged']=hashlib.sha256(bad.read_bytes()).hexdigest()==bad_sha
    (output/'NATIVE-RESULT.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('host_version','passed','blend','expected_green_area_mm2','expected_round_area_mm2')},indent=2))
    if not args.bad_model:
        assert result['passed'], 'Filled counter, round-end envelope, or saved native geometry changed'


if __name__=='__main__':
    main()
