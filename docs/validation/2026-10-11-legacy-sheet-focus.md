# Blender 3.1 automatic sheet view compatibility

The unpublished 1.0.109 r10 source assumed that every supported Blender context
provided `temp_override`. Blender 3.1.2 does not. The surrounding exception
handler therefore skipped direct sheet framing and Source Accurate shading.
A bounded, instrumented 3.1 background control confirmed the missing method and
unchanged viewport with otherwise correctly imported geometry and packed assets.
That control is diagnostic evidence, not a desktop import pass. Its separate
5.2 instrumented-background crash is retained as a test-harness diagnostic and
is not treated as a product desktop failure.

The correction retains the current `temp_override` scope and its six original
context members. When that method is absent, it copies the real context and
pairs the selected window, screen, area and WINDOW region with that area's
active space and region data. Only Local View, View Selected and View All
operators receive the older positional dictionary. Direct RNA framing, Source
Accurate shading, collection visibility, clipping and selection continue through
the existing code. The actual background guard, disabled Auto Focus behavior,
scene color-management values and other visual styles remain unchanged.

This dictionary convention is documented by the primary
[Blender 3.0 operator API](https://docs.blender.org/api/3.0/bpy.ops.html).
The [3.4 operator API](https://docs.blender.org/api/3.4/bpy.ops.html) records its
deprecation after the newer context method became available. A failure within
the modern method is not retried through the legacy interface.

Focused controls verify both context formats, real numeric page framing without
operators, exact per-area dictionary pairing, Local View handling, operator
fallback, selected-object behavior and unchanged scene values. The opt-in
`tests/native_legacy_sheet_context_probe.py` checks only dictionary construction
against real Blender 3.1 context objects in a normal factory/background process.
It never changes the background flag or calls view operators. It proves API
availability and context pairing, not native viewport pixels or a full import.

Version remains 1.0.109. All r9/r10 packages and evidence are historical and
unchanged. Desktop automatic import, visible Source Accurate image delivery,
Save/Open and final r11 package acceptance require the separate real-program
checks after normal source merge and build.
