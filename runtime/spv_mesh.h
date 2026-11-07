// Written for ocarina-of-time-recomp (MIT, Copyright (c) 2026 Jeff Aigner).
// Task / mesh programs translated as compute, turned into TaskEXT / MeshEXT modules (spv_mesh.c, docs/mesh-plan.md).
#pragma once
#include <stdint.h>

#define SPV_MESH_PAYLOAD_BYTES 0x1000u   // the task payload: the task's whole attr region (sm86_lower.h SM86_ISBE_PAYLOAD_BYTES)
#define SPV_MESH_MAX_TASK_GROUPS 4096u   // a task's mesh workgroup count is clamped to this (a stray count would hang)
#define SPV_MESH_LOOP_LIMIT 65536u       // every loop of a task / mesh module leaves after this many rounds (watchdog)

typedef struct {
    uint32_t kind;                     // 6 task, 7 mesh
    uint32_t in, attr, map, total;     // the ISBE regions in shared memory, bytes (sm86_lower.h Sm86Isbe)
    uint32_t maxv, maxp, lines, local; // mesh output sizes, attribute lines per vertex, workgroup size
    uint8_t omap[17];                  // header bytes 0x35 (omap_systemb) and 0x36-0x45 (generic nibbles)
    uint32_t no_call;                  // diagnostic (OOT_MESH_NOCALL=1): the game's code is not called (no output)
    uint32_t debug;                    // diagnostic (OOT_MESH_DEBUG=1): mesh workgroups with a task stage record their
                                       // output in the pool feedback buffer (SPV_MESH_DEBUG_*)
    uint32_t cullw;                    // diagnostic (OOT_MESH_CULLW=1): a triangle with a vertex at w <= 0 is not emitted
} SpvMeshInfo;

// OOT_MESH_DEBUG records, in the pool set's feedback buffer (set 2, binding 8) after its texture marks: word BASE counts
// the records claimed; record r (r < RECORDS) at BASE + 16 + r * STRIDE: [0] maxv << 16 | maxp, [1] workgroup,
// [2] primitive count, [3] magic, [16 + 3p + e] index e of triangle p, [400 + 4v + c] position component c of vertex v.
#define SPV_MESH_DEBUG_BASE 65536u
#define SPV_MESH_DEBUG_RECORDS 1500u
#define SPV_MESH_DEBUG_STRIDE 4096u   // [700 + v * lines + k]: line k of vertex v (every attribute line)
#define SPV_MESH_DEBUG_WORDS (16u + (SPV_MESH_DEBUG_RECORDS + 1u) * SPV_MESH_DEBUG_STRIDE)
#define SPV_MESH_DEBUG_MAGIC 0x0d15ea5eu

// A new module (malloc'd, *out_n words) or NULL when w is not a translated task / mesh compute module this handles.
// with_task: a mesh module that reads a task payload (pipelines with a task stage).
uint32_t *spv_mesh_convert(const uint32_t *w, uint64_t n, const SpvMeshInfo *mi, int with_task, uint64_t *out_n);
