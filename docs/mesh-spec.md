# NVN2 task/mesh shader behaviour spec (derived from Lichii-nx2)

Purpose: describe what the Lichii Switch 2 emulator does with NVN2 task and mesh programs so a separate MIT-licensed project can implement the same behaviour. This is a behaviour description written in prose and tables. No Lichii code is reproduced. Where a formula is given it is a statement of a data layout, not a transcription of source.

Source root (read-only, GPL): `Lichii-nx2/` (all paths below are relative to it). Citations are `file:line`. `NOTES` = `NX2_NOTES.md`.

Evidence tags: **Observed** = stated in source or comment; **Derived** = computed by me from struct layouts in source (re-check before relying); **Not found** = the source does not settle it.

Contents: 1 ISBERD/ISBEWR encodings, 2 ISBE address layout, 3 program header and mesh registers, 4 mapping to VK_EXT_mesh_shader, 5 GPU/NVN side, 6 pitfalls, 7 open points.

---

## 0. Overview (Observed: NOTES:147, emit_spirv_mesh.cpp:4-10)

NVN2 task and mesh programs do not use ordinary stores for their results. They talk to the hardware through an "internal stage buffer entry" (ISBE) with two instructions, ISBERD (read) and ISBEWR (write).

- A **task** program writes the number of mesh workgroups at byte 4 of its output region and its payload after it. Nothing is read from the hardware except the workgroup index.
- A **mesh** workgroup reads that same region as its input (its own index at byte 0, payload from byte 0x10). It writes its vertices and primitives to an *attribute region* in groups of 32 elements with one 32-word (128-byte) line per output component, and writes the primitive count plus 8-bit vertex indices into a *map region*.
- Lichii models the task's output region as the Vulkan task payload, and the mesh's attribute/map regions as workgroup (shared) memory. A common epilogue reached by every invocation hands them to Vulkan.

Addresses used by the programs are plain linear byte addresses into these regions (NOTES:147).

---

## 1. SM86 (Ampere) ISBERD and ISBEWR encodings

### 1.1 Instruction container

An Ampere instruction is 128 bits held as four 32-bit words (`frontend/ampere/instruction.h:9-19`). "Bit n" below means bit n of the 128-bit value (word = n/32). The opcode is bits 0-11 (`frontend/ampere/lower_to_maxwell.h:155,284`). Bits 12-15 are the guard predicate (3-bit index plus a negate bit at bit 15). The value 15 in that field means "@!PT", an instruction that never executes, and is skipped as compiler padding (`lower_to_maxwell.h:285`, `377-380`).

### 1.2 Opcode and fields (Observed: `lower_to_maxwell.h:3856-3883`)

| Item | Bits | Meaning |
|---|---|---|
| opcode | 0-11 | `0x923` = ISBERD, `0x927` = ISBEWR |
| guard predicate | 12-15 | normal predication; carried to the pseudo instruction (bits 16-19 of it) |
| ISBERD destination | 16-23 | destination register. 255 (RZ) is rejected for a read |
| Ra (address register) | 24-31 | base address register. 255 = RZ (value 0) |
| ISBEWR data register | 32-39 | register whose value is written |
| offset | 40-63 | 24-bit immediate byte offset added to Ra |
| must be zero | 64-74 and 80-95 | any other bit set in the third word is rejected ("native stage buffer access mode") |
| `.32` | 75 | 1 = 32-bit access. 0 = 8-bit (U8) |
| region / `.ATTR` | 76-77 | 2-bit field. 3 = ".ATTR" (the attribute region), 0 = the map region. Values 1 and 2 are rejected |
| `.SKEW` | 78 | decoded and forwarded, see 1.5 |
| `.O` | 79 | 1 = output buffer (this is the write side) |
| bits 96-127 | n/a | scheduling control word, not examined by this path |

Hard rules the lowering enforces (`lower_to_maxwell.h:3873-3875`):
1. `.O` must equal "is ISBEWR". So ISBERD never has `.O` and ISBEWR always does. Forms such as `ISBERD.O` do not occur for task/mesh and are rejected.
2. A read must use region 3 (`.ATTR`).
3. A read must have a real destination register.

A real captured example (Observed, `src/tests/video_core/ampere_logged_numeric.cpp:446-451`): the ISBEWR `.32.ATTR.SKEW.O [R197 + 0x780], R240` is the four words `0xc5007927, 0x000780f0, 0x0000f800, 0x000fe200`. That decodes as opcode 0x927, predicate PT, Ra = 0xc5 = R197, data = 0xf0 = R240, offset = 0x780, third word 0xf800 = bits 75..79 all set (`.32`, region 3, `.SKEW`, `.O`). A bare ISBERD example in the geometry test is `0x00017923, 0, 0, 0` = ISBERD R1, [R0] (`src/tests/video_core/ampere_geometry.cpp:34`). That is the geometry/tessellation form, see 1.6.

Registers 216 and above can be shadowed by the uniform register bank. The lowering must read the data register from the shadow copy in that case (test at `ampere_logged_numeric.cpp:444-460`: "R240 is UR22's mirror once the uniform bank is in use").

### 1.3 The internal pseudo instruction (Lichii's own re-encoding)

Lichii lowers Ampere to a Maxwell-format stream and keeps ISBERD/ISBEWR as one pseudo opcode so the frontend can interpret it per stage (`lower_to_maxwell.h:3867-3871`, `frontend/maxwell/maxwell.inc:59`). Pattern: top bits `1110 1111 1100 0`, i.e. `0xefc0...` in the top 12 bits. For an implementer that does not use a Maxwell intermediate, these are only the decoded fields:

| Pseudo bit(s) | Field |
|---|---|
| 0-7 | data register (ISBEWR) or destination register (ISBERD) |
| 8-15 | address register Ra |
| 16-19 | guard predicate |
| 20-43 | 24-bit offset |
| 44 | `.32` |
| 45 | `.ATTR` (region == 3) |
| 46 | `.SKEW` |
| 47 | `.O` |
| 48 | write (1 = ISBEWR) |

(`internal_stage_buffer_entry_read.cpp:131-140`, emission at `lower_to_maxwell.h:3878-3881`). The translator reads only registers, offset, `.32`, `.ATTR` and write; the `.SKEW` and `.O` bits are carried but never consulted (see 1.5).

### 1.4 What each form means (Observed: `internal_stage_buffer_entry_read.cpp:142-161`)

The effective address is always Ra plus the 24-bit offset (a byte address). The instruction is only accepted in the task and mesh stages. Elsewhere it throws "Stage buffer access outside task and mesh programs".

| Form | Task stage | Mesh stage |
|---|---|---|
| ISBERD.ATTR (read) | returns the workgroup index (gl_WorkGroupID.x) whatever the address. The input region of a task holds only its index | address == 0: returns the workgroup index (the mesh workgroup's index in the grid the task launched). Any other address: returns the task payload word at (address / 4), clamped to the last payload word |
| ISBEWR.O.ATTR.32 (write) | stores the value into the task payload word at (address / 4), clamped to the payload size. This is the payload and, at byte 4, the mesh-workgroup count | stores the value into the attribute-region word at (address / 4), clamped to the region size |
| ISBEWR.O.ATTR (no .32, U8) | translator throws "U8 stage buffer attribute write" | same |
| ISBEWR.O (region 0), no `.32` (U8) | throws: "Stage buffer map write outside mesh programs" | stores the value masked to 8 bits into map-region element [address], address clamped to the map size. One map byte occupies one 32-bit word of the emulated region. This is how U8 vertex indices are written |
| ISBEWR.O (region 0) with `.32` | throws | only accepted if the address is an immediate constant 3 after folding. The stored value is the primitive count. Any other address throws "32-bit stage buffer map write" |

Typical task program (NOTES:147): `ISBERD.ATTR.SKEW [RZ]` reads the workgroup index. The payload is stored by `ISBEWR.O.ATTR [rank*stride + 0x10]`. The mesh workgroup count is stored by `ISBEWR.O.ATTR [0x4]`. Typical mesh program: read of address 0 is its own index, payload reads at byte 0x10 and up, vertex data writes into the attribute region, `ISBEWR.O.32 [0x3]` writes the primitive count, and U8 `ISBEWR.O` writes indices from byte 4.

### 1.5 `.SKEW`: what Lichii does and does not do

On the task/mesh path `.SKEW` is not used by the translator: no lane offset is added to the address. Whether the compiler already folds the lane or thread offset into Ra is **not found in source**. Lichii's note says all captured programs draw cleanly, so Ra evidently already carries it (inference only). The `.SKEW` bit is only meaningful in the legacy Maxwell ISBERD described next. An implementer must check this against captured programs before assuming it can be ignored.

### 1.6 Other ISBERD forms Lichii handles (not task/mesh)

For completeness, since the same opcode appears in other stages:

- **Ampere ISBERD in geometry or tessellation stages** (`lower_to_maxwell.h:3857-3865`): only opcode 0x923 with zero extra words and a non-RZ Ra is accepted, and it is rewritten to a Maxwell move-style instruction (`0x5c98...`). Otherwise the lowering fails with "native geometry stage buffer read mode" or "native tessellation stage buffer read mode".
- **Legacy Maxwell ISBERD** (opcode pattern `1110 1111 1101 0---`, `maxwell.inc:58`) decoded in `internal_stage_buffer_entry_read.cpp:55-126`:

| Bits | Field |
|---|---|
| 0-7 | destination register |
| 8-15 | source register. 0xFF = use only the immediate |
| 24-31 | 8-bit immediate |
| 31 | `.SKEW` (overlaps the top immediate bit in that struct) |
| 32 | `.O` |
| 33-34 | mode: 0 Default, 1 Patch, 2 Prim, 3 Attr |
| 36-39 | size: 0 U8, 1 U16, 2 U32, 3 F32 |
| 47-48 | index shift: 0 none, 1 shift left 1 (U16), 2 shift left 2 (B32) |

Semantics: index = immediate, or (scaled register + immediate). With `.O`: optionally add lane-id scaled by size when `.SKEW`, then load global memory of the given size. Without `.O` and mode Patch/Prim/Attr, optionally add lane*4 with `.SKEW`, then read patch/attribute/indexed attribute, bit-cast to integer. With mode Default and `.SKEW`: dest = source + lane id. Otherwise dest = source (a plain copy).

---

## 2. ISBE address layout

All addresses are bytes. Lichii stores every region as an array of 32-bit words and converts a byte address to a word by dividing by 4 (map region excepted, one byte per word element).

### 2.1 Task stage

Input region (read by ISBERD): effectively one word, the workgroup index (the draw's "vertex id"). Reads return gl_WorkGroupID.x regardless of address (`emit_spirv_mesh.cpp:171-176`).

Output region = the Vulkan task payload (`TaskPayloadWorkgroupEXT` array of u32, `spirv_emit_context.cpp:1796-1801`).

| Byte offset | Word | Content |
|---|---|---|
| 0x00 | 0 | not written by the task program (a mesh workgroup read of address 0 returns its own index instead, see 2.2) |
| 0x04 | 1 | **number of mesh workgroups** to launch (read back by the epilogue) |
| 0x08, 0x0c | 2, 3 | not found in source |
| 0x10 and up | 4.. | **payload**. Programs store at rank*stride + 0x10 (rank = a per-thread index, stride chosen by the program; NOTES:147) |

Payload size in words = max(lines, 1) * 32, where lines is the larger of two fields of SET_MESH_INIT_SHADER (3.3), each a count of 128-byte lines (`translate_program.cpp:406-408`). All accesses are clamped to the last word (`emit_spirv_mesh.cpp:32-39`).

### 2.2 Mesh stage input region

The same array as the task payload (the Vulkan `TaskPayloadWorkgroupEXT` variable is declared in both stages, `spirv_emit_context.cpp:1796-1801`, size from the same mesh registers, which is why both stages need the same size).

| Byte offset | Content |
|---|---|
| 0x00 | the mesh workgroup's own index (special-cased: reads at address 0 never touch the payload; `emit_spirv_mesh.cpp:177-180`) |
| 0x04 | the count word the task wrote |
| 0x10 and up | task payload |

A mesh pipeline with no task stage gets the same payload variable; what it contains in that case is not found in source.

### 2.3 Mesh output: attribute region

Output elements are organised in **groups of 32** (one lane group). Within a group there is one **line per output component**, each line 32 words = 128 bytes, one word per element in the group, indexed by the element's slot (element number mod 32). Statement of the layout (`emit_spirv_mesh.cpp:58-66`, `spirv_emit_context.cpp:1805-1813`):

- Vertices come first. Let L = number of vertex lines. Vertex v lives in group g = v/32 (integer divide), slot s = v mod 32. Line k of vertex v is the word at g*(L*32) + k*32 + s (word index; multiply by 4 for the byte address).
- After all vertex groups come the per-primitive groups. Let P = number of per-primitive lines. Primitive base word = ceil(max_vertices/32) * L * 32. Line k of primitive p is at base + (p/32)*(P*32) + k*32 + (p mod 32).
- Total region words = max(primitive base + ceil(max_primitives/32)*P*32, 1).

**Line order ("mesh header omap order")**, `translate_program.cpp:310-333`: lines are listed in this order, for the vertex map and (with the other header) for the primitive map separately:

1. The 8 system-value bits of the output map byte `omap_systemb` (header byte 0x35, see 3.2), bit 0 first, one line per set bit. Bit meaning and IR attribute index: bit 0 primitive-array-id (PrimitiveId, attr 24), bit 1 render-target-array-index (Layer, 25), bit 2 viewport-index (ViewportIndex, 26), bit 3 point size (27), bit 4 position X (28), bit 5 position Y (29), bit 6 position Z (30), bit 7 position W (31). (Attribute numbers from `frontend/ir/attribute.h:18-25`.)
2. Then generic attributes 0..31 in ascending order, and for each generic its x, y, z, w components in that order, one line per component enabled in the output map (attr index = 32 + generic*4 + component, `attribute.h:26,153,254`).

Maximum number of lines is 136 (8 + 128), the static capacity of the lists (`shader_info.h:265-276`).

Rejection rule: if the vertex or primitive header has any of clip distances, point-sprite S/T, fog coordinate, tessellation-eval point U/V, instance id or vertex id set in `omap_systemc`, translation throws "Mesh outputs past the generic attributes" (`translate_program.cpp:313-319`).

Two maps are used: the **vertex** lines come from the mesh program's own header output map. The **primitive** lines come from the output map of a separate header-only "pass-through geometry program" bound with the mesh (5.3).

### 2.4 Mesh output: map region

A byte array (Lichii uses one 32-bit word per byte, `spirv_emit_context.cpp:1813,1818`). Size in bytes = 4 + 3*max_primitives.

| Byte | Content |
|---|---|
| 0-2 | not used |
| 3 | **primitive count**, written by ISBEWR.O.32 (immediate address 3 only). The map is "written from its end, so its first word is at byte 3" (`emit_spirv_mesh.cpp:202`) |
| 4 + 3*p + e | U8 vertex index e (0..2) of triangle p, written by ISBEWR.O U8 |

The count is clamped to max_primitives by the epilogue. Each invocation first stores 0 into the count (prologue, `emit_spirv_mesh.cpp:209-217`) so that a workgroup where no thread writes the count produces 0 primitives.

Output topology is triangles only: a topology field other than 4 throws "Mesh output topology" (`translate_program.cpp:414-417`).

---

## 3. Header and register inputs

### 3.1 Where the sizes come from

Important: **max vertices, max primitives, topology, thread counts and payload size are NOT read from the program header.** They come from three GPU method registers latched when the shaders are translated (`translate_program.cpp:399-422`, the registers are captured at `video_core/shader_environment.cpp:828-833`). The program header supplies only the output maps.

### 3.2 Mesh registers (byte offsets within the Maxwell3D register file; `engines/maxwell_3d.h:2544-2551`)

| Method | Name | Fields |
|---|---|---|
| 0x114c | SET_MESH_CONTROL | bit 0 = mesh mode on (`maxwell_3d.h:2553-2555`). Other bits: not found |
| 0x1150 | SET_MESH_SHADER_A | bits 3:0 output topology (4 = triangles, others rejected); bits 12:4 (9 bits) max vertices; bits 22:13 (10 bits) max primitives |
| 0x1154 | SET_MESH_SHADER_B | bits 25:20 (6 bits) mesh workgroup threads (X size) |
| 0x1158 | SET_MESH_INIT_SHADER | bits 5:0 task workgroup threads; bits 20:12 (9 bits) local-buffer lines; bits 30:22 (9 bits) lines passed to the mesh stage. A "line" is 128 bytes |

(`translate_program.cpp:402-411`). The Vulkan workgroup size is (max(threads,1), 1, 1) for both stages. The task uses the INIT_SHADER thread count, the mesh uses SHADER_B's. Payload words = max(1, max(local-buffer lines, mesh-passed lines)) * 32.

Any write to the four registers 0x114c..0x1158 marks the shader state dirty (`video_core/dirty_flags.cpp:67-68`).

### 3.3 Program header (Observed layout in `shader_recompiler/program_header.h`, offsets Derived)

The standard Maxwell SPH is 0x50 bytes (`program_header.h:221`). Native NVN2 programs carry a 128-byte (0x80) header and code starts right after it, 16 bytes per instruction (`video_core/shader_environment.h:112` default `source_header_size = 128`; `shader_environment.cpp:494,500`). Only the first 0x50 bytes are parsed (`shader_environment.cpp:708`); bytes 0x50-0x7f are not interpreted by Lichii.

| Offset | Size | Field |
|---|---|---|
| 0x00-0x13 | 5 words | common words 0-4 (type, version, shader_type at bits 10-13 of word 0, local memory sizes, output topology bits 24-27 of word 3, max output vertices bits 0-11 of word 4). None of these are consulted for mesh sizing |
| 0x14-0x16 | 3 | ImapSystemValuesA (unused here) |
| 0x17 | 1 | imap_systemb |
| 0x18-0x27 | 16 | imap_generic_vector |
| 0x28-0x2B | 4 | imap color (2), imap_systemc (2) |
| 0x2C-0x31 | 6 | fixed-function texture imap (5), reserved (1) |
| 0x32-0x34 | 3 | OmapSystemValuesA (ignored) |
| **0x35** | 1 | **omap_systemb**: bit 0 primitive_array_id, 1 rt_array_index, 2 viewport_index, 3 point_size, 4 position_x, 5 position_y, 6 position_z, 7 position_w |
| **0x36-0x45** | 16 | **omap_generic_vector**: one nibble per generic. Generic g is in byte 0x36 + g/2, low nibble for even g, high nibble for odd g. Nibble bits 0-3 = x, y, z, w enabled |
| 0x46-0x47 | 2 | omap color |
| **0x48-0x49** | 2 | **omap_systemc** (16-bit): bits 0-7 clip distances, 8 point_sprite_s, 9 point_sprite_t, 10 fog coordinate, 12 tessellation_eval_point_u, 13 _v, 14 instance_id, 15 vertex_id. All must be zero for mesh |
| 0x4A-0x4E | 5 | omap fixed-function texture |
| 0x4F | 1 | omap reserved |

Offsets from 0x14 are my computed running sum of the struct member sizes (`program_header.h:70-152`); the total reaches 0x50 which matches the static_assert at line 221. Re-check against a real dump.

NOTES:155 says the task and mesh programs appear in "stage slot 1, header type VS" — the shader_type field is a vertex type for these programs. The field is not used to choose the stage: that is decided by the mesh-mode register and slot (5.1).

### 3.4 The second header (per-primitive output map)

With mesh mode on and the geometry stage slot enabled, Lichii reads 0x50 bytes at the GPU address held in that slot's pipeline record and uses it as the *primitive header* (`shader_environment.cpp:834-840`). The same 0x50 bytes (20 words) are hashed with the mesh registers into the pipeline key (`video_core/shader_cache.cpp:104-114`). Its omap fields at the same offsets as 3.3 name the per-primitive outputs. It contains no code: the geometry slot is given hash 0 and never compiled (`shader_cache.cpp:140-144`).

---

## 4. Translation to VK_EXT_mesh_shader

### 4.1 Declarations (Observed: `backend/spirv/spirv_emit_context.cpp:1788-1895`, `emit_spirv.cpp:291-304`)

- Capability MeshShadingEXT and extension SPV_EXT_mesh_shader for both stages. Execution models TaskEXT and MeshEXT.
- Execution mode LocalSize (threads, 1, 1) on both. Mesh also: OutputVertices = max_vertices, OutputPrimitivesEXT = max_primitives, OutputTrianglesEXT.
- `task_payload`: array of u32 in storage class TaskPayloadWorkgroupEXT, size payload_words, declared in both stages.
- Mesh only, all in Workgroup (shared) storage class:
  - `isbe_attributes`: u32 array of the attribute-region words (2.3);
  - `isbe_map`: u32 array of the map-region size (2.4), one byte per element;
  - `isbe_primitive_count`: one u32.
- Output variables (Mesh):
  - triangle indices: BuiltIn PrimitiveTriangleIndicesEXT, array of uvec3 [max_primitives];
  - Position: BuiltIn Position, array of vec4 [max_vertices] (created on demand, only if a position line exists);
  - PointSize: BuiltIn PointSize, array of float [max_vertices];
  - per vertex generics: array of vec4 [max_vertices] with Location = generic index, one variable per generic;
  - per primitive generics: array of vec4 [max_primitives] with Location = generic index, decorated PerPrimitiveEXT;
  - per primitive built-ins PrimitiveId, Layer, ViewportIndex: arrays of u32 [max_primitives], decorated PerPrimitiveEXT.
- A per-vertex line whose attribute has no vertex output (for example PrimitiveId/Layer/ViewportIndex listed in the vertex map) has no target: it keeps its line in the layout but is not copied. A per-primitive line that is not a generic, PrimitiveId, Layer or ViewportIndex is likewise skipped (`spirv_emit_context.cpp:1840-1888`).
- Components are bit-cast: generic/position/point-size floats from the stored word; built-ins as integers.
- Task and mesh stages use their own output declaration path; the generic DefineOutputs returns immediately for them (`spirv_emit_context.cpp:1899-1900`).
- Task and mesh programs are also marked as reading workgroup id and local invocation id (`ir_opt/collect_shader_info_pass.cpp:1015-1019`).

### 4.2 Control flow: a single epilogue

Every EXIT in the guest program is redirected to one common exit/return so all invocations reach the same epilogue (`frontend/maxwell/structured_control_flow.cpp:399-408`, enabled for these stages at line 1026). The ISBE write instructions are side-effecting so dead-code elimination keeps them (`frontend/ir/microinstruction.cpp:86-88`). The epilogue is invoked in place of the usual stage epilogue (`backend/spirv/emit_spirv_special.cpp:145-148`).

Prologue (mesh only, `emit_spirv_special.cpp:140-142`, `emit_spirv_mesh.cpp:209-217`): every invocation stores 0 to the primitive count, then a workgroup control barrier (workgroup scope, acquire-release, workgroup-memory semantics). The store is unconditional; the comment records that no branch is used in the prologue because the entry block's label is a phi predecessor for later blocks.

### 4.3 Task epilogue (`emit_spirv_mesh.cpp:110-118`)

1. Workgroup barrier.
2. Load payload word 1 (the mesh-workgroup count the program wrote at byte 4).
3. End the task with OpEmitMeshTasksEXT with group counts (count, 1, 1) and the task payload variable.

### 4.4 Mesh epilogue (`emit_spirv_mesh.cpp:120-168`)

1. Workgroup barrier.
2. Primitive count = min(isbe_primitive_count, max_primitives).
3. OpSetMeshOutputsEXT(vertex count = max_vertices as a constant, primitive count). Note the vertex count passed is always the maximum (the header's real count is not used).
4. Vertex copy: invocation i handles vertices i, i+threads, i+2*threads, ... below max_vertices. In the last partial round the copy is guarded by "vertex < max_vertices". For each vertex copy each line's word (from the layout in 2.3) into the corresponding output variable; component index is the line's attribute component. (The loop copies against max_vertices and does not test against the real vertex count.)
5. Depth conversion: if the pipeline's depth mode is converted (`convert_depth_mode`, set from the viewport state's "NDC minus-one-to-one") and the device has no native NDC control and a Position output exists, then after copying set Position.z = (z + w) * 0.5 using the stored z and w lines of that vertex (`emit_spirv_mesh.cpp:85-108,132-133`; `vk_pipeline_cache.cpp:249-251`). If either z or w line is absent nothing is written.
6. Primitive copy: for each primitive p < count (same strided loop over max_primitives): read three map bytes at 4 + 3p, 4 + 3p + 1, 4 + 3p + 2 (zero extended to u32) and store them as the uvec3 in PrimitiveTriangleIndicesEXT[p]. Then copy the per-primitive lines (starting at primitive base word) to the per-primitive output variables.

SetMeshOutputs and EmitMeshTasks have no builder in the SPIR-V library Lichii uses, so the backend emits a recognisable store as a marker and then patches the finished binary: the marker store becomes OpSetMeshOutputsEXT, and the task marker (two stores followed by a return) is replaced with the five-word OpEmitMeshTasksEXT instruction (`emit_spirv.cpp:601-645`). A missing marker is a logic error ("Mesh instruction markers not found").

---

## 5. NVN / GPU side

### 5.1 Selecting stages

- Pipeline records: six 0x40-byte records starting at method address 0x2000 (`maxwell_3d.h:3038,2518-2530`). Word 0: bit 0 enable, bits 4-7 program (shader type). Word 1: offset. The GPU address of a native program is built from the 5th and 6th words (`reserved[0]` holding the high bits in its low 8 bits, `reserved[1]` the low 32 bits), at record offsets 0x14 and 0x18 (`shader_cache.cpp:146-148`, `shader_environment.cpp:836-838`).
- Slot numbers (`maxwell_3d.h:2509-2516`): 0 VertexA, 1 VertexB, 2 TessellationInit, 3 Tessellation, 4 Geometry, 5 Pixel. VertexB is always considered enabled (`maxwell_3d.h:2532-2538`).
- Only on Switch 2 (native) titles, when SET_MESH_CONTROL bit 0 is set:
  - **VertexB slot (1)** holds the **task** program; if the Tessellation slot (3) is NOT enabled there is no task, and the VertexB slot holds the **mesh** program.
  - **Tessellation slot (3)** holds the **mesh** program.
  - **Geometry slot (4)** holds a header-only pass-through program whose output map gives the per-primitive outputs.
  - `shader_environment.cpp:718-722,828-840`, header comment `maxwell_3d.h:2544-2547`.
- The slot of the program determines the driver-constant-buffer base used for the NVN driver buffers: task 0x110 (vertex slot), mesh 0x310 (tessellation slot) (`translate_program.cpp:99-112`, `collect_shader_info_pass.cpp:998-1007`).

### 5.2 Draw to vkCmdDrawMeshTasksEXT

- A pipeline is a mesh pipeline when its first stage info is task or mesh (`vk_graphics_pipeline.h:124-127`). The rasterizer then sets "mesh draw" (`vk_rasterizer.cpp:435-436`).
- **Direct draws**: the draw's vertex count is the number of task workgroups. Lichii calls vkCmdDrawMeshTasksEXT with group counts (vertex count, 1, 1) (`vk_rasterizer.cpp:786-808`). The count is the non-indexed vertex-buffer count (or index-buffer count for an indexed draw). First vertex, base vertex and instance values are ignored for mesh draws. No other adjustment is done for mesh draws.
- **Indirect draws: not supported.** The indirect path logs "Indirect task/mesh draw skipped" once and returns (`vk_rasterizer.cpp:859-867`). The comment says an indirect task/mesh draw would need vkCmdDrawMeshTasksIndirectEXT's own layout. So an indirect argument layout is **not found in source**. (The standard Vulkan layout for that command is three u32 group counts X, Y, Z. That is Vulkan, not something Lichii settles.) The Switch 2 indirect-reset fast path is also disabled while mesh mode is on (`vk_rasterizer.cpp:304`).
- Draws with zero groups are noted only for tracing (`vk_rasterizer.cpp:796-799`).
- Skipped entirely (draw dropped) when the device lacks VK_EXT_mesh_shader (`vk_rasterizer.cpp:447-450`, pipelines are not built: `vk_pipeline_cache.cpp:1252-1258`, `vk_graphics_pipeline.cpp:843-845`).
- Developer switches: environment `EDEN_NX2_NO_MESH` skips all mesh draws; `EDEN_NX2_NO_MESH_FILE=<path>` skips them while that file exists (checked every 1024 draws) (`vk_rasterizer.cpp:437-446`).

### 5.3 Pipeline creation

- Shader stage flag: a program flagged task becomes VK_SHADER_STAGE_TASK_BIT_EXT, flagged mesh becomes VK_SHADER_STAGE_MESH_BIT_EXT, irrespective of the slot (`vk_graphics_pipeline.cpp:62-70`).
- Mesh pipelines have no vertex input state, input assembly state or tessellation state; the matching dynamic states (primitive topology, primitive restart enable, vertex input, vertex binding stride, patch control points) are removed (`vk_graphics_pipeline.cpp:1203-1215,1259-1261`).
- Push constants (the rescaling/render-area block) and the pipeline layout push-constant range add the task and mesh stage bits when a mesh stage is present (`vk_graphics_pipeline.cpp:805-807`, `pipeline_helper.h:252-254,273`). The scheduler tracks a "mesh layout" flag so the push constants are re-sent when switching between mesh and non-mesh pipelines (`vk_scheduler.cpp:467-476`).
- Device: requires the taskShader and meshShader features; multiviewMeshShader, primitiveFragmentShadingRateMeshShader and meshShaderQueries are forced off (`vulkan_common/vulkan_device.cpp:1599-1606`). Only the two stages are used.
- Pipeline key: the hash of the task/mesh programs (slots 1 and 3) is XORed with a hash of (SET_MESH_SHADER_A, SET_MESH_SHADER_B, SET_MESH_INIT_SHADER and the 20 words of the pass-through header), because the same program code translates differently under different register values (`shader_cache.cpp:102-114,218-221`).

### 5.4 Pairing mesh outputs with fragment inputs

- During pipeline construction, stages are processed in slot order, and each program's "previous stage" is the one built before it. The geometry slot has hash 0 under mesh mode so it is skipped; the fragment program's previous stage is therefore the mesh program (`vk_pipeline_cache.cpp:1328-1351`, `shader_cache.cpp:140-144`).
- Fragment inputs are matched by location: for the fragment program, any generic attribute that appears in the mesh program's **per-primitive lines** is recorded in a 32-bit set `per_primitive_generics` (`vk_pipeline_cache.cpp:181-188`, `shader_recompiler/runtime_info.h:89`). In the fragment shader those inputs are decorated PerPrimitiveEXT (and Flat if integer-typed), and the extension and capability are added (`spirv_emit_context.cpp:1750-1757`).
- The mesh program's "stored attributes" are the union of its vertex lines and primitive lines. That is what the fragment stage checks its inputs against (`translate_program.cpp:463-471`).
- Per-vertex outputs go through ordinary interpolated Location outputs; they pair by generic index with fragment input Location of the same index. There is no separate pairing table.

---

## 6. Pitfalls and notes recorded in Lichii

1. **Rejected programs were skipped for a long time.** Task/mesh programs failed lowering ("12 task/mesh programs (ISBERD/ISBEWR)" left over from the shader sweep, NOTES:146). Skipping them caused pop-in and "low-quality meshes" because high detail LODs are drawn this way (NOTES:155), and the attract demo stalled on the world map without them (NOTES:147, NOTES:258 for a frame-rate note of 10-17 fps with mesh geometry missing).
2. **Exception storm.** A rejected native task/mesh shader was rethrown twice every time it was bound, costing 1-2% GPU thread time. The fix was to return false for remembered rejections (NOTES:170, commit 948a391).
3. **Disk cache.** NOTES:147 says the mesh pipelines are "kept out of the disk cache". What I found in the code does not match that exactly: the environment serialiser does write the mesh registers and the 0x50-byte primitive header for these stages (`shader_environment.cpp:342-351`, read back at `1118-1122`), and the loader only skips such pipelines if the device lacks the extension (`vk_pipeline_cache.cpp:908-912`). Whether Lichii deliberately omits mesh pipelines from saved files is **not found in source**; the implementer should not rely on cached mesh pipelines and should key anything cached on the mesh registers and the primitive header.
4. **Barriers and convergence.** The count store and the output copy are only correct if every invocation reaches the same epilogue. That is why every EXIT is routed to one return (`structured_control_flow.cpp:399-404`), and why the prologue zeroes the count before a barrier.
5. **Vulkan has no builder for these two instructions in Lichii's SPIR-V library.** The OpSetMeshOutputsEXT and OpEmitMeshTasksEXT are patched in after assembly via marker stores. The task marker pattern must be followed immediately by a return.
6. **Register clamping.** Every access to the payload, attribute and map arrays is clamped to the array bounds to avoid out-of-range access from guest programs (`emit_spirv_mesh.cpp:32-39,189,197`).
7. **Vertex count fixed at maximum.** SetMeshOutputs always receives max_vertices. The primitive count is the program-written count clamped to max_primitives.
8. **Unsupported forms throw** (and so the pipeline is dropped): topology not 4; omap_systemc bits; U8 attribute-region write; 32-bit map write other than at address 3; map write outside the mesh stage; ISBE in any stage but task/mesh via the Ampere pseudo path; regions 1 and 2.
9. **Uniform-bank shadow registers.** Programs with the high registers shadowed by the uniform bank take the data register from the shadow (test `ampere_logged_numeric.cpp:444-460`).
10. **Performance.** In the attract demo mesh draws cost about 3 ms a frame (NOTES:147).
11. **Not yet verified in race modes.** NOTES:155: "not yet checked in a race".

---

## 7. Open points (not settled by the source)

- Meaning of `.SKEW` on ISBEWR/ISBERD for task/mesh; Lichii ignores it (1.5).
- Task output words 0, 2 and 3 and any content beyond the count at byte 4.
- Indirect mesh draws: argument layout and count handling.
- What a mesh pipeline without a task stage sees in its payload, and how its workgroup count is decided beyond "vertex count".
- Header bytes 0x50-0x7f of native programs.
- SET_MESH_CONTROL bits other than bit 0.
- Whether mesh pipelines are meant to bypass the disk cache (3 in section 6).
- Vertex count actually produced per mesh workgroup (Lichii uses max_vertices).
