"""Eight original rectangle direction/start controls in actual headless Blender.

Run with --background --factory-startup --python-exit-code 1. This verifies the
complete import engine and native Save/Open, not desktop interaction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    import bpy
    from pdf_vector_importer.dependency_manager import ensure_pymupdf_runtime
    assert ensure_pymupdf_runtime(auto_install=False)
    import pymupdf
    from pdf_vector_importer.bl_import_engine import import_pdf

    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for reverse in (False, True):
        for start in range(4):
            name = f"nonzero-direction-{int(reverse)}-start-{start}"
            source = output/(name+".pdf")
            points = [(30,30),(30,50),(60,50),(60,30)]
            if reverse:
                points.reverse()
            points = points[start:]+points[:start]
            commands = f"0 .5 0 rg 10 10 80 70 re {points[0][0]} {points[0][1]} m "
            commands += " ".join(f"{x} {y} l" for x,y in points[1:])+" h f"
            with pymupdf.open() as doc:
                page=doc.new_page(width=100,height=100)
                xref=doc.get_new_xref();doc.update_object(xref,"<<>>")
                doc.update_stream(xref,commands.encode());page.set_contents(xref)
                doc.save(source)
                counter_white = page.get_pixmap().pixel(40,60)==(255,255,255)
            digest=hashlib.sha256(source.read_bytes()).hexdigest()
            bpy.ops.wm.read_factory_settings(use_empty=True)
            stats=import_pdf(str(source),config={"mode":"vector","text_mode":"none",
                "visual_style":"source","white_page_background":False,"auto_focus_view":False,
                "import_report_path":str(output/(name+"-report.json"))})
            bpy.context.view_layer.update()

            def native_snapshot():
                objects = [obj for obj in bpy.data.objects if obj.get("bcs_ordinary_pdf_fill")]
                assert len(objects)==1
                obj=objects[0]
                evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
                mesh=evaluated.to_mesh()
                try:
                    mesh.calc_loop_triangles()
                    area=sum(t.area for t in mesh.loop_triangles)*1e6
                    center=(40*25.4/72/1000,40*25.4/72/1000)
                    painted=False
                    for triangle in mesh.loop_triangles:
                        vertices=[tuple((obj.matrix_world@mesh.vertices[i].co)[:2]) for i in triangle.vertices]
                        cross=lambda a,b,c:(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
                        signs=[cross(vertices[i],vertices[(i+1)%3],center) for i in range(3)]
                        painted |= all(v>=0 for v in signs) or all(v<=0 for v in signs)
                    return dict(area_mm2=area,counter_painted=painted,
                        splines=[[tuple(p.co) for p in spline.points] for spline in obj.data.splines],
                        source_rule=obj.get("pdf_source_fill_rule"),
                        winding_proof=obj.get("pdf_source_winding_proof"))
                finally:
                    evaluated.to_mesh_clear()

            before=native_snapshot()
            model=output/(name+".blend")
            bpy.ops.wm.save_as_mainfile(filepath=str(model))
            bpy.ops.wm.open_mainfile(filepath=str(model))
            after=native_snapshot()
            expected=(80*70-(30*20 if counter_white else 0))*(25.4/72)**2
            passed=(before==after and abs(after["area_mm2"]-expected)<.001
                    and after["counter_painted"] is not counter_white
                    and after["winding_proof"]=="unique_directed_source_svg"
                    and not stats.get("geometry_delivery_issues")
                    and hashlib.sha256(source.read_bytes()).hexdigest()==digest)
            results.append(dict(case=name,source_pdf_sha256=digest,source_counter_white=counter_white,
                expected_area_mm2=expected,native=after,save_reopen_equal=before==after,passed=passed))
    result=dict(schema="bcs.blender-nonzero-original-direction-native/1",host_version=bpy.app.version_string,
                headless=True,desktop_ui_acceptance=False,cases=results,passed=all(r["passed"] for r in results))
    (output/"NATIVE-RESULT.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"host_version":bpy.app.version_string,"cases":len(results),"passed":result["passed"]}))
    assert result["passed"],"Native nonzero winding or source counter changed"


if __name__=="__main__":
    main()
