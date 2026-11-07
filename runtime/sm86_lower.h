// Written for ocarina-of-time-recomp (MIT, Copyright (c) 2026 Jeff Aigner).
// SM86 (Ampere, the Switch 2's T239) shader programs lowered to Maxwell (SM5x) programs the vendored Ryujinx
// translator decodes. See sm86_lower.c for the layout of both and the provenance of the encodings.
#pragma once
#include <stdint.h>

typedef struct {
    uint8_t *code;             // Maxwell program: graphics: the 0x50-byte program header, then instructions in
                               // groups of three behind a (zero) scheduling word; compute: the groups alone
    uint32_t code_size;
    uint8_t *c1;               // the jump-table constants (constant bank 1) with their targets moved to the
                               // lowered program; the translator reads BRX targets from here
    uint32_t c1_len;
    uint32_t error_offset;     // byte offset of the SM86 instruction that could not be lowered, from the start
                               // of the code block (header included)
    char error[96];            // empty when the lowering succeeded
} Sm86Lowered;

// code and control are the NVN2 program as the game hands them to nvnProgramSetShaders (and as the archives hold
// them): control is the NVN2 control section (magic 0x98761234; its fields are read here), code the code block
// the control describes (graphics: 0x80-byte header, instructions, padding, the jump tables). Returns 1 and fills
// out on success; 0 with out->error set otherwise. Free with sm86_lower_free either way.
int sm86_lower(const uint8_t *code, uint32_t code_size, const uint8_t *control, uint32_t control_size, Sm86Lowered *out);
void sm86_lower_free(Sm86Lowered *out);

// The control section's fields the host needs (offsets measured over the game's archives, tools/shader_census.py).
typedef struct {
    uint32_t size;             // the control section's own size (+0xd4; the archives repeat it at +0x30)
    uint32_t code_end;         // end of the instructions in the code block (+0x38), header included
    uint32_t c1_off, c1_len;   // the jump-table constants in the code block (+0x40, +0x3c)
    uint32_t code_size;        // the whole code block (+0x44)
    uint32_t stage;            // NVN's enum at byte +0x56: 0 vertex, 1 fragment, 2 geometry, 3 tess control,
                               // 4 tess eval, 5 compute, 6 task, 7 mesh
    uint32_t local[3];         // compute: the workgroup size (+0x58)
    uint32_t shared;           // compute: the shared memory the program uses, in bytes (+0x64; 0x500 to 0x5460 in the
                               // upscaler's programs), capped at 48 KiB
    // compute: the program's driver constant bank c0 (c0_valid when the fields add up). NVN2 packs it per program:
    // 0x0-0xf the dispatch (workgroup counts at 0x4, 0x8, 0xc), the push constants at 0x10, then the storage-buffer
    // table ({u64 address, u32 size} every 16 bytes, +0xb2 entries), the texture handles (8 bytes, +0xb3 of them) and
    // the image handles (8 bytes, +0xb6), c0_end bytes in all (+0xba, u16). Checked on all 270 compute programs of
    // the game: every c0 access of 269 lies in its region (one reads 0x22c, past its bank).
    int c0_valid;
    uint32_t c0_push, c0_ssbo, c0_tex, c0_img, c0_end;   // push size; the byte offsets of the tables; the bank size
    uint32_t c0_nssbo, c0_ntex, c0_nimg;
    uint32_t mesh_max_vertices, mesh_max_primitives;      // mesh: control +0x60 (u16 each)
} Sm86Control;
// 0 when the magic or the bounds are wrong.
int sm86_control_parse(const uint8_t *control, uint32_t control_size, Sm86Control *c);
// Task (6) and mesh (7) stages (docs/mesh-plan.md): their ISBE accesses become shared-memory accesses after the
// program's own shared memory, in three regions the host's SPIR-V conversion (spv_mesh.c) reads and fills:
//   in    task: the workgroup index at 0; mesh: its input index at 0, the task payload from 0x10
//   attr  task: the payload it writes (mesh workgroup count at 4, payload from 0x10); mesh: the vertex attribute
//         lines, vertex v line k at (v / 32) * lines * 128 + k * 128 + (v % 32) * 4
//   map   mesh: one word per byte address: the primitive count at word 3, U8 triangle indices from word 4
typedef struct {
    uint32_t shared;                 // the program's own shared memory (16-byte aligned)
    uint32_t in, attr, map, total;   // region byte offsets in shared memory; total = the shared memory to declare
    uint32_t max_vertices, max_primitives, lines;   // mesh: from control +0x60 (lo, hi) and the header's output map
    uint8_t omap[17];                // mesh: header bytes 0x35 (omap_systemb) and 0x36-0x45 (omap_generic nibbles)
    uint32_t spill;                  // uniform registers the allocator could not keep in a register: word n for URn,
                                     // the last SM86_UR_SPILL_BYTES of total
} Sm86Isbe;
#define SM86_UR_SPILL_BYTES 0x100u
#define SM86_ISBE_IN_BYTES 0x1000u
#define SM86_ISBE_PAYLOAD_BYTES 0x1000u
int sm86_isbe_layout(const uint8_t *code, const Sm86Control *c, Sm86Isbe *o);   // 0 when not a task/mesh stage
#define SM86_CONTROL_MAGIC 0x98761234u
#define SM86_CONTROL_MIN 0xe0u    // the fixed part; the jump-table records follow it
#define SM86_HEADER 0x80u         // the program header before a graphics stage's first instruction
