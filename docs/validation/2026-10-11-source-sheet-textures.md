# Source Accurate viewport image handoff

The 1.0.109 r9 desktop mixed-page import delivered the original image bytes,
UVs, fonts, geometry and source material colors, but automatic focus selected
Solid/Flat/Material. The image plane appeared gray. Root's actual Blender 5.2.2
manual Solid/Texture selection displayed the checker while retaining the white
sheet and vector/text colors. Material Preview showed the image but changed the
page's apparent colors under the user's scene transform. Those r9 artifacts and
the automatic-view failure remain historical evidence.

The bounded correction passes the requested Source Accurate style through the
existing automatic-focus path. Its ordinary opaque vector/image and raster
handoff selects Solid/Flat/Texture; Workbench falls back to the material display
color on objects without an active texture. Both cached and uncached source image
builders explicitly activate their image texture node after shader construction.
Only source PNGs proven to have no alpha channel or tRNS chunk, with the active
packed image binding, default Alpha 1 and all three canonical shader connections,
take that opaque route. Unknown/alpha graphs and existing transparent/composite
cases retain Material Preview. Blueprint/High Contrast handoffs, disabled Auto Focus,
scene view transform, look, exposure and gamma remain unchanged. The PNG metadata
scan stops at 4096 chunks; exceeding that only retains the existing material view
and does not reject or truncate the imported image. An older API
without the Texture enum retains the flat Material view instead of failing focus.

The official [Workbench color documentation](https://docs.blender.org/manual/en/5.0/render/workbench/color.html)
describes active texture/UV display and material-color fallback. The
[View3DShading API](https://docs.blender.org/api/3.2/bpy.types.View3DShading.html)
documents the Solid texture enum; actual 3.1.2 and 5.2.2 RNA are checked directly.

Focused Python controls exercise real import handoff, default Source Accurate,
optional palettes, disabled automatic focus, alpha/unproven graph protection,
existing sheet framing and older API guards. The opt-in
`tests/native_source_sheet_view_probe.py` runs in fresh background/factory hosts
with fictional RGB/RGBA controls. In actual Blender 3.1.2 and 5.2.2 it verifies
native shading enums, cached/uncached active image nodes, active UVs, packed
bytes, material fallback data and saved/reopened view/resource state while
preserving scene color-management values. It is a direct API/data proof, not
desktop rendering or full PDF import acceptance.

Version remains 1.0.109, which is not yet published. Root must test automatic
desktop import from the new integrated r10 package after normal merge/build.
Alpha/composite visual calibration, other hosts/hardware and broader PDF fidelity
are outside this bounded correction. No r9 package, installed profile, product
identity, old evidence, source PDF or hub metadata was modified.
