"""Run inside Blender to check a fictional PDF's green even-odd counter.

This is a bounded geometry/save-open check; desktop UI acceptance is separate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--topology-matrix", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    import bpy
    from pdf_vector_importer.dependency_manager import ensure_pymupdf_runtime
    assert ensure_pymupdf_runtime(auto_install=False)
    import pymupdf
    from pdf_vector_importer.pdfcadcore.primitive_extractor import extract_page
    from pdf_vector_importer import bl_compound_fills, bl_geometry_builder

    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=False)
    if args.topology_matrix:
        results=[]
        cases=[("nested",[[(0,0),(10,0),(10,10),(0,10)],[(3,3),(7,3),(7,7),(3,7)]],84),
               ("overlap",[[(0,0),(10,0),(10,10),(0,10)],[(5,0),(15,0),(15,10),(5,10)]],100),
               ("touching",[[(0,0),(10,0),(10,10),(0,10)],[(10,0),(20,0),(20,10),(10,10)]],200),
               ("touching-reversed",[[(10,0),(20,0),(20,10),(10,10)],[(0,0),(10,0),(10,10),(0,10)]],200),
               ("self-crossing",[[(0,0),(10,10),(0,10),(10,0)],[(20,0),(25,0),(25,5),(20,5)]],75),
               ("lone-self-crossing",[[(0,0),(10,10),(0,10),(10,0)]],50)]
        cases = [(name, contours, expected, "f*", {}) for name, contours, expected in cases]
        outer = [(10,10),(90,10),(90,80),(10,80)]
        inner = [(30,30),(30,50),(60,50),(60,30)]
        cases.extend([
            ("nonzero-opposite-winding", [outer,inner], (80*70-30*20)*(25.4/72)**2, "f", {"pdf_points":True}),
            ("nonzero-same-winding", [outer,inner[::-1]], 80*70*(25.4/72)**2, "f", {"pdf_points":True}),
            ("nonzero-overlap-guard", [[(0,0),(10,0),(10,10),(0,10)],[(5,0),(15,0),(15,10),(5,10)]], None, "f", {}),
            ("topology-budget-guard", [[(0,0),(10,10),(0,10),(10,0)]], None, "f*", {"compound_fill_work_budget":3}),
        ])
        for name,contours,expected,operator,options in cases:
            source=output/(name+'.pdf')
            with pymupdf.open() as doc:
                page=doc.new_page(width=100,height=100)
                xref=doc.get_new_xref();doc.update_object(xref,'<<>>')
                commands=['0 .5 0 rg']
                for contour in contours:
                    points=contour if options.get("pdf_points") else [(x*72/25.4,y*72/25.4) for x,y in contour]
                    commands.append(f'{points[0][0]} {points[0][1]} m')
                    commands.extend(f'{x} {y} l' for x,y in points[1:])
                    commands.append('h')
                commands.append(operator)
                doc.update_stream(xref,'\n'.join(commands).encode());page.set_contents(xref);doc.save(source)
            with pymupdf.open(source) as doc:
                data=extract_page(doc[0],1)
                windings=bl_compound_fills.bind_nonzero_windings(data,doc[0].get_svg_image(text_as_path=False))
            collection=bpy.data.collections.new(name);bpy.context.scene.collection.children.link(collection)
            stats=bl_geometry_builder.build_page(data,collection,{"visual_style":"source",**options,
                                                                  "_source_nonzero_windings":windings})
            bpy.context.view_layer.update()
            area=0;triangles=0
            for obj in collection.objects:
                evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=evaluated.to_mesh()
                try:
                    mesh.calc_loop_triangles()
                    area+=sum(t.area for t in mesh.loop_triangles)*1e6;triangles+=len(mesh.loop_triangles)
                finally:evaluated.to_mesh_clear()
            def snapshot(collection):
                return [(obj.name,obj.data.fill_mode,[[tuple(p.co) for p in s.points] for s in obj.data.splines])
                        for obj in collection.objects if obj.type == "CURVE"]
            before=snapshot(collection)
            model=output/(name+'.blend')
            bpy.ops.wm.save_as_mainfile(filepath=str(model))
            bpy.ops.wm.open_mainfile(filepath=str(model))
            collection=bpy.data.collections[name]
            reopened=before==snapshot(collection)
            if expected is None:
                passed = (area == 0 and len(stats['geometry_delivery_issues']) == 1
                          and stats['geometry_delivery_issues'][0]['source_paths_retained'] is True
                          and all(obj.data.fill_mode == "NONE" for obj in collection.objects))
            else:
                passed=abs(area-expected)<.001 and stats['ordinary_compound_fills']==1
            results.append({'case':name,'source_pdf_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                            'expected_area_mm2':expected,'native_area_mm2':area,'triangles':triangles,
                            'ordinary_compounds':stats['ordinary_compound_fills'],
                            'geometry_delivery_issues':stats['geometry_delivery_issues'],
                            'save_reopen_equal':reopened,
                            'passed':passed and reopened})
        result={'schema':'bcs.blender-evenodd-topology-native/1','host_version':bpy.app.version_string,
                'headless':True,'desktop_ui_acceptance':False,'cases':results,
                'passed':all(r['passed'] for r in results)}
        (output/'NATIVE-RESULT.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(result,indent=2))
        assert result['passed'],'Native even-odd overlap/topology area changed'
        return
    pdf = Path(args.pdf)
    source_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
    with pymupdf.open(pdf) as doc:
        data = extract_page(doc[0], 1)
    green = [p for p in data.primitives if p.fill_color is not None
             and all(abs(a-b) < 1e-6 for a, b in zip(p.fill_color, (.08, .48, .2), strict=True))]
    assert len(green) == 2 and len({p.source_draw_order for p in green}) == 1
    data.primitives = green
    hole_box = min(green, key=lambda p: p.area).bbox
    hole_center = ((hole_box[0]+hole_box[2])/2000, (hole_box[1]+hole_box[3])/2000)
    expected_area = max(p.area for p in green) - min(p.area for p in green)
    expected_points = [[tuple(p) for p in primitive.points] for primitive in green]
    if args.baseline:
        bl_compound_fills.grouped_fill_primitives = lambda page, **kwargs: page.primitives
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    collection = bpy.data.collections.new("Fictional source fill")
    bpy.context.scene.collection.children.link(collection)
    stats = bl_geometry_builder.build_page(data, collection, {"visual_style": "source"})
    bpy.context.view_layer.update()

    def snapshot():
        surfaces, triangles, covered = [], [], False
        for obj in collection.objects:
            evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            mesh = evaluated.to_mesh()
            try:
                mesh.calc_loop_triangles()
                areas = []
                for tri in mesh.loop_triangles:
                    points = [tuple((obj.matrix_world @ mesh.vertices[i].co)[:2]) for i in tri.vertices]
                    cross = lambda a,b,c: (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
                    areas.append(abs(cross(*points))/2*1_000_000)
                    signs = [cross(points[i], points[(i+1)%3], hole_center) for i in range(3)]
                    covered |= all(x >= 0 for x in signs) or all(x <= 0 for x in signs)
                    triangles.append(points)
                surfaces.append({"name":obj.name,"type":obj.type,"area_mm2":sum(areas),
                                 "triangles":len(areas),"splines":len(obj.data.splines) if obj.type=="CURVE" else None})
            finally:
                evaluated.to_mesh_clear()
        return {"surfaces":surfaces,"total_area_mm2":sum(s["area_mm2"] for s in surfaces),
                "hole_center_painted":covered,"triangles":triangles}

    before = snapshot()
    blend = output / "fictional-green-counter.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    collection = bpy.data.collections["Fictional source fill"]
    after = snapshot()
    result = {"schema":"bcs.blender-ordinary-fill-native/1","host_version":bpy.app.version_string,
              "headless":True,"desktop_ui_acceptance":False,"baseline":args.baseline,
              "pdf":str(pdf),"pdf_sha256":source_hash,"expected_area_mm2":expected_area,
              "source_vertices_mm":expected_points,"stats":stats,"before_save":before,
              "after_reopen":after,"save_reopen_equal":before==after,"blend":str(blend),
              "blend_sha256":hashlib.sha256(blend.read_bytes()).hexdigest(),
              "source_pdf_unchanged":hashlib.sha256(pdf.read_bytes()).hexdigest()==source_hash}
    result["passed"] = (before==after and not after["hole_center_painted"]
                        and abs(after["total_area_mm2"]-expected_area) < .001)
    (output / "NATIVE-RESULT.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:result[k] for k in ("host_version","baseline","expected_area_mm2","passed","blend")},indent=2))
    if not args.baseline:
        assert result["passed"], "Native counter area or unpainted region changed"


if __name__ == "__main__":
    main()
