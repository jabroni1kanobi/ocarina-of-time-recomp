# Mesh shading: implementation plan (2026-11-06)

Why: the owner's white-out in Link's house is the sky seen through walls the game draws with task/mesh programs
(`nvnCommandBufferDrawMeshTasks*`, stubs). Behaviour reference: `docs/mesh-spec.md` (from Lichii, GPL: behaviour only).

## Measured on this game (build/logs/meshprog, run meshprog)

- Programs: control `+0x56` 7 (mesh) and 6 (task); the game pairs fragment+mesh and fragment+task+mesh.
- Mesh control words: `+0x58` = 0x00800020 (32 threads in the low half), `+0x5c` = 0x03000000, `+0x60` = 0x00400080
  (64 and 128: max vertices / max primitives, which is which not settled; declare 128 / 128).
- Mesh header (code bytes): `+0x35` omap_systemb = 0xf0 (position xyzw), `+0x36..` generic nibbles; lines per vertex
  L = popcount(systemb low 8) + popcount(generic nibbles) (7 and 25 in the samples, matching the code).
- ISBE use in the mesh samples (SM86 opcode 0x923 ISBERD / 0x927 ISBEWR; Ra 24-31, data 32-39, dst 16-23,
  offset 40-63, .32 bit 75, region 76-77 (3 ATTR, 0 map), .SKEW 78, .O 79):
  - `ISBERD` region 3, RZ, offset 0, .SKEW: the mesh workgroup's input index (no task: its own workgroup index).
  - `ISBEWR` region 0, RZ, offset 3, .32: primitive count.
  - `ISBEWR` region 3, Ra + k*128, .32: vertex attribute lines (vertex v, line k at (v/32)*L*128 + k*128 + (v%32)*4).
  - `ISBEWR` region 0, Ra + 4..9, 8-bit: triangle indices, byte 4 + 3*prim + n.
  No per-primitive outputs in these programs.

## Indirect draws (2026-11-06, runs meshind, meshind2, meshind3)

- `DrawMeshTasksIndirect(cmd, ind, a2, a3)`: records 16 bytes apart (`ind` = base + 0x10 k, a3 = base, a2 small 0..2).
- Their contents are zero everywhere we can look: CPU copy (mirror and `OOT_MEMORY=import`) and a GPU readback after
  the draws (`OOT_MESH_PROBE=1`, `vk_mesh.c`). No pass of a frame dump binds those ranges, no copy or clear names
  them, and the only dispatch of the frame with unresolved global stores (cs 2ab4842d08dea85c, pass 0) does not cover
  them. Working assumption: the records come from work that does not run yet (the task/mesh stages, or stores through
  pointers read from memory); re-probe once mesh pipelines run. Translator notes overall: 628 compute "Failed to find
  storage buffer ... Store" (stores dropped), 516 for loads: a buffer-device-address fallback is the general fix.
- New ops: OP_DRAW_MESH (first, count), OP_DRAW_MESH_IND (ind, a2, a3); replay counts and skips them for now.

## Route

1. `sm86_lower.c`: stages 6/7 lower like compute (no header in the output; the SM86 code still starts after its
   0x80-byte header). ISBERD -> LDS, ISBEWR -> STS into shared memory after the program's own shared memory:
   IN region (mesh: input index at 0, payload from 0x10; task: workgroup index), OUT/ATTR region, MAP region (one word
   per byte address, as Lichii). Region bases fixed per stage; shared size grows by them.
2. `gfx_shader.c`: stages 6/7 translate as "cs"; the shader records mesh/task, L, omap, sizes.
3. `spv_mesh.c` (new): turn the compute module into MeshEXT / TaskEXT: execution model and modes, outputs (Position
   array, generic arrays at their Locations, PrimitiveTriangleIndicesEXT), the old entry renamed into a function called
   by a new entry: prologue (input index / payload into shared), barrier, call, barrier, epilogue (SetMeshOutputsEXT,
   copy lines -> outputs, indices; task: payload -> TaskPayloadWorkgroupEXT, EmitMeshTasksEXT(count,1,1)).
4. `vk_device.c` enables VK_EXT_mesh_shader (done). `vk_pipeline.c`: mesh pipelines (monolithic, no vertex input).
5. NVN: bind stages 6/7; `DrawMeshTasks(cmd, first, count)` -> vkCmdDrawMeshTasksEXT; `DrawMeshTasksIndirect` ->
   vkCmdDrawMeshTasksIndirectEXT once the argument layout is read from a live buffer (not in Lichii).
