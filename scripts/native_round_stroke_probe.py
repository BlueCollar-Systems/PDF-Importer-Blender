"""Native capsule envelope checks for neutral horizontal/vertical/slanted marks."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

sys.dont_write_bytecode = True


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--out",required=True)
    args=parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
    import bpy
    from pdf_vector_importer.dependency_manager import ensure_pymupdf_runtime
    assert ensure_pymupdf_runtime(auto_install=False)
    import pymupdf
    from pdf_vector_importer.bl_import_engine import import_pdf

    output=Path(args.out);output.mkdir(parents=True,exist_ok=False)
    results=[]
    for name,end in (("horizontal",(85,20)),("vertical",(10,85)),("slanted",(75,80)),
                     ("dot",(10,20)),("short",(10.01,20))):
        source=output/(name+'.pdf')
        with pymupdf.open() as doc:
            page=doc.new_page(width=100,height=100);xref=doc.get_new_xref();doc.update_object(xref,'<<>>')
            doc.update_stream(xref,f'.85 .12 .08 RG 1 J 6 w 10 20 m {end[0]} {end[1]} l S'.encode())
            page.set_contents(xref);doc.save(source)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        stats=import_pdf(str(source),config={'mode':'vector','visual_style':'source',
                         'auto_focus_view':False,'import_report_path':str(output/(name+'-report.json'))})
        records=stats.get('source_round_stroke_footprints',())
        assert len(records)==1 and records[0]['status'] in ('verified','approximated')
        obj=bpy.data.objects[records[0]['footprint_object']]
        center=bpy.data.objects[records[0]['centerline_object']]
        def visibility(footprint, parent):
            return {'source_helper_hidden': parent.hide_viewport and parent.hide_render and parent.hide_select,
                    'source_helper_marker': parent.get('pdf_display_replaced_by')==footprint.name,
                    'footprint_visible': footprint.visible_get() and not footprint.hide_render,
                    'parent_hidden': not parent.visible_get(),
                    'parent_preserved': footprint.parent==parent}
        bpy.context.view_layer.update();evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=evaluated.to_mesh()
        imported_visibility=visibility(obj,center)
        try:
            mesh.calc_loop_triangles();area=sum(t.area for t in mesh.loop_triangles)*1e6
            points=[tuple(obj.matrix_world@v.co) for v in mesh.vertices]
            bounds=[min(p[0] for p in points)*1000,min(p[1] for p in points)*1000,
                    max(p[0] for p in points)*1000,max(p[1] for p in points)*1000]
        finally:evaluated.to_mesh_clear()
        factor=25.4/72;radius=3*factor;length=math.hypot(end[0]-10,end[1]-20)*factor
        expected_area=length*6*factor+math.pi*radius**2
        ux,uy=((end[0]-10)*factor/length,(end[1]-20)*factor/length) if length else (1,0)
        # Axis-aligned support of a swept circular pen, not a rectangular overestimate.
        expected_bounds=[min(10,end[0])*factor-radius,min(20,end[1])*factor-radius,
                         max(10,end[0])*factor+radius,max(20,end[1])*factor+radius]
        bpy.ops.wm.save_as_mainfile(filepath=str(output/(name+'.blend')))
        bpy.ops.wm.open_mainfile(filepath=str(output/(name+'.blend')))
        restored=bpy.data.objects[records[0]['footprint_object']]
        restored_center=bpy.data.objects[records[0]['centerline_object']]
        bpy.context.view_layer.update()
        restored_visibility=visibility(restored,restored_center)
        # A hidden parent must still supply its transform to its visible child.
        prior_world=restored.matrix_world.copy()
        restored_center.location.x+=.01
        bpy.context.view_layer.update()
        parent_edit_propagates=all(abs(restored.matrix_world[i][3]-prior_world[i][3]-(.01 if i==0 else 0.))<1e-6
                                   for i in range(3))
        results.append({'case':name,'source_pdf_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                        'expected_area_mm2':expected_area,'native_area_mm2':area,
                        'expected_bounds_mm':expected_bounds,'native_bounds_mm':bounds,
                        'source_direction':[ux,uy],'representation':records[0]['representation'],
                        'reported_radial_error_mm':records[0]['max_radial_error_mm'],
                        'import_visibility': imported_visibility,'restored_visibility': restored_visibility,
                        'hidden_parent_edit_propagates': parent_edit_propagates,
                        'parent_preserved':restored.parent.name==records[0]['centerline_object'],
                        'passed':abs(area-expected_area)<.015 and max(abs(a-b) for a,b in zip(bounds,expected_bounds,strict=True))<.002
                                 and all(imported_visibility.values()) and all(restored_visibility.values())
                                 and parent_edit_propagates})
    result={'schema':'bcs.blender-round-stroke-native/1','host_version':bpy.app.version_string,
            'host_version_tuple':list(bpy.app.version),
            'headless':True,'desktop_ui_acceptance':False,'cases':results,'passed':all(r['passed'] for r in results)}
    (output/'NATIVE-RESULT.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))
    assert result['passed'],'Native cap area, direction or envelope changed'


if __name__=='__main__':main()
