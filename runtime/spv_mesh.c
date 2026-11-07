// Written for ocarina-of-time-recomp (MIT, Copyright (c) 2026 Jeff Aigner).
//
// Task and mesh programs (NVN2 stages 6 and 7, docs/mesh-plan.md) are lowered and translated as compute programs whose
// ISBE accesses address three shared-memory regions (sm86_lower.h Sm86Isbe). This turns such a GLCompute module into
// a TaskEXT / MeshEXT one (VK_EXT_mesh_shader; the module becomes SPIR-V 1.4, whose entry point lists every global
// variable it uses):
//   - the entry point becomes a new function: prologue, barrier, a call of the old entry, barrier, epilogue;
//   - prologue: the workgroup index into in[0] (both stages); with a task stage, the mesh copies the task's payload
//     (TaskPayloadWorkgroupEXT: the task's whole attr region) into in;
//   - mesh epilogue: SetMeshOutputsEXT(max vertices, min(map word 3, max primitives)); each vertex's attribute lines
//     from attr into the outputs the header's output map names (position lines into gl_MeshVerticesEXT[].gl_Position,
//     generic g into an array of vec4 at Location g); the U8 triangle indices (map words 4..) into
//     PrimitiveTriangleIndicesEXT;
//   - task epilogue: the attr region into the payload, EmitMeshTasksEXT(attr word 1, 1, 1, payload).
// Every copy is unrolled over the workgroup's invocations (thread t handles element t + local * j), so no control flow
// is added. Behaviour reference: docs/mesh-spec.md (Lichii, GPL: behaviour only).
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "spv_mesh.h"

enum {
    OP_EXTENSION = 10, OP_EXT_INST_IMPORT = 11, OP_EXT_INST = 12, OP_ENTRY_POINT = 15, OP_EXECUTION_MODE = 16,
    OP_CAPABILITY = 17, OP_TYPE_VOID = 19, OP_TYPE_INT = 21, OP_TYPE_FLOAT = 22, OP_TYPE_VECTOR = 23,
    OP_TYPE_ARRAY = 28, OP_TYPE_STRUCT = 30, OP_TYPE_POINTER = 32, OP_TYPE_FUNCTION = 33, OP_CONSTANT = 43,
    OP_FUNCTION = 54, OP_FUNCTION_END = 56, OP_FUNCTION_CALL = 57, OP_VARIABLE = 59, OP_LOAD = 61, OP_STORE = 62,
    OP_ACCESS_CHAIN = 65, OP_IN_BOUNDS_ACCESS_CHAIN = 66, OP_PTR_ACCESS_CHAIN = 67, OP_DECORATE = 71, OP_MEMBER_DECORATE = 72, OP_COMPOSITE_CONSTRUCT = 80,
    OP_COMPOSITE_EXTRACT = 81, OP_BITCAST = 124, OP_IADD = 128, OP_IMUL = 132, OP_SHIFT_RIGHT_LOGICAL = 194, OP_BITWISE_OR = 197, OP_BITWISE_AND = 199,
    OP_CONTROL_BARRIER = 224, OP_LABEL = 248, OP_RETURN = 253, OP_EMIT_MESH_TASKS_EXT = 5294,
    OP_SET_MESH_OUTPUTS_EXT = 5295,
    SC_UNIFORM = 2, SC_INPUT = 1, SC_OUTPUT = 3, SC_WORKGROUP = 4, SC_FUNCTION = 7, SC_STORAGE_BUFFER = 12,
    SC_TASK_PAYLOAD_EXT = 5402,
    DEC_BLOCK = 2, DEC_BUFFER_BLOCK = 3, DEC_BUILTIN = 11, DEC_LOCATION = 30,
    BI_POSITION = 0, BI_WORKGROUP_ID = 26, BI_LOCAL_INVOCATION_ID = 27, BI_PRIMITIVE_TRIANGLE_INDICES_EXT = 5296,
    CAP_MESH_SHADING_EXT = 5283, EM_TASK_EXT = 5364, EM_MESH_EXT = 5365,
    MODE_LOCAL_SIZE = 17, MODE_OUTPUT_VERTICES = 26, MODE_OUTPUT_PRIMITIVES_EXT = 5270, MODE_OUTPUT_TRIANGLES_EXT = 5298,
    GLSL_UMIN = 38,
};

typedef struct { uint32_t *w; uint32_t n, cap; } Buf;
static int put(Buf *b, uint32_t v) {
    if (b->n == b->cap) {
        uint32_t c = b->cap ? b->cap * 2 : 256;
        uint32_t *p = realloc(b->w, (size_t)c * 4);
        if (!p) return 0;
        b->w = p, b->cap = c;
    }
    b->w[b->n++] = v;
    return 1;
}
static void ins(Buf *b, uint32_t op, int nargs, const uint32_t *a) {
    put(b, (uint32_t)(nargs + 1) << 16 | op);
    for (int i = 0; i < nargs; i++) put(b, a[i]);
}
#define INS(b, op, ...) do { const uint32_t a_[] = {__VA_ARGS__}; ins(b, op, (int)(sizeof a_ / 4), a_); } while (0)

typedef struct {
    uint32_t bound;
    Buf glob, ann, fn;
    uint32_t t_void, t_uint, t_float, t_v4, t_v3u, t_fn;
    uint32_t glsl;
    struct { uint32_t v, id; } k[96];   // uint constants made here
    int nk;
    uint32_t vars[64];                  // global variables made here (for the entry point's interface)
    int nvars;
} Ctx;

static uint32_t newid(Ctx *c) { return c->bound++; }
static uint32_t kuint(Ctx *c, uint32_t v) {
    for (int i = 0; i < c->nk; i++) if (c->k[i].v == v) return c->k[i].id;
    uint32_t id = newid(c);
    INS(&c->glob, OP_CONSTANT, c->t_uint, id, v);
    if (c->nk < 96) c->k[c->nk].v = v, c->k[c->nk].id = id, c->nk++;
    return id;
}
static uint32_t kfloat(Ctx *c, float v) {   // a float constant (no dedup: used by the diagnostics only)
    uint32_t id = newid(c), bits;
    memcpy(&bits, &v, 4);
    INS(&c->glob, OP_CONSTANT, c->t_float, id, bits);
    return id;
}
static uint32_t ptr(Ctx *c, uint32_t sc, uint32_t type) {   // pointer types made here (duplicates of the module's are valid)
    uint32_t id = newid(c);
    INS(&c->glob, OP_TYPE_POINTER, id, sc, type);
    return id;
}
static uint32_t arr(Ctx *c, uint32_t elem, uint32_t len) {
    uint32_t id = newid(c);
    INS(&c->glob, OP_TYPE_ARRAY, id, elem, kuint(c, len));
    return id;
}
static uint32_t var(Ctx *c, uint32_t ptype, uint32_t sc) {
    uint32_t id = newid(c);
    INS(&c->glob, OP_VARIABLE, ptype, id, sc);
    if (c->nvars < 64) c->vars[c->nvars++] = id;
    return id;
}

// shared[index id] as uint
static uint32_t sh_load(Ctx *c, uint32_t sh, uint32_t p_wg_uint, uint32_t index) {
    uint32_t p = newid(c), v = newid(c);
    INS(&c->fn, OP_ACCESS_CHAIN, p_wg_uint, p, sh, index);
    INS(&c->fn, OP_LOAD, c->t_uint, v, p);
    return v;
}
static void sh_store(Ctx *c, uint32_t sh, uint32_t p_wg_uint, uint32_t index, uint32_t value) {
    uint32_t p = newid(c);
    INS(&c->fn, OP_ACCESS_CHAIN, p_wg_uint, p, sh, index);
    INS(&c->fn, OP_STORE, p, value);
}
static uint32_t iadd(Ctx *c, uint32_t a, uint32_t b) { uint32_t r = newid(c); INS(&c->fn, OP_IADD, c->t_uint, r, a, b); return r; }

// SPIR-V 1.4 has no BufferBlock: the translator's storage buffers (Uniform + BufferBlock) become Block + StorageBuffer in
// place: the decoration, the variables and their pointer types, and the result type of every access chain that starts
// from one (a StorageBuffer twin of its Uniform pointer type, made in c->glob). Returns a modified copy of w.
static uint32_t *storage_buffers(Ctx *c, const uint32_t *w0, uint64_t n) {
    uint32_t *w = malloc(n * 4);
    if (!w) return NULL;
    memcpy(w, w0, n * 4);
    uint32_t bound = w[3];
    uint8_t *bb = calloc(bound, 1), *sb = calloc(bound, 1);   // bb: BufferBlock structs; sb: storage-buffer pointers
    uint32_t *twin = calloc(bound, 4);                         // Uniform pointer type -> its StorageBuffer twin
    if (!bb || !sb || !twin) { free(bb), free(sb), free(twin), free(w); return NULL; }
    for (uint64_t i = 5; i < n;) {
        uint32_t op = w[i] & 0xffff, len = w[i] >> 16;
        if (op == OP_DECORATE && len >= 3 && w[i + 2] == DEC_BUFFER_BLOCK && w[i + 1] < bound) bb[w[i + 1]] = 1, w[i + 2] = DEC_BLOCK;
        i += len;
    }
    for (uint64_t i = 5; i < n;) {   // pointer types to those structs, and the variables of them
        uint32_t op = w[i] & 0xffff, len = w[i] >> 16;
        if (op == OP_TYPE_POINTER && w[i + 2] == SC_UNIFORM && w[i + 3] < bound && bb[w[i + 3]]) w[i + 2] = SC_STORAGE_BUFFER, sb[w[i + 1]] = 2;
        if (op == OP_VARIABLE && w[i + 3] == SC_UNIFORM && w[i + 1] < bound && sb[w[i + 1]] == 2) w[i + 3] = SC_STORAGE_BUFFER, sb[w[i + 2]] = 1;
        i += len;
    }
    for (uint64_t i = 5; i < n;) {   // access chains from them (in order: a chain's base is defined before it)
        uint32_t op = w[i] & 0xffff, len = w[i] >> 16;
        if ((op == OP_ACCESS_CHAIN || op == OP_IN_BOUNDS_ACCESS_CHAIN || op == OP_PTR_ACCESS_CHAIN) && len >= 4 &&
            w[i + 3] < bound && sb[w[i + 3]] == 1 && w[i + 1] < bound) {
            uint32_t t = w[i + 1];
            if (!twin[t]) {
                uint32_t pointee = 0;
                for (uint64_t k = 5; k < n && !pointee;) {
                    uint32_t o2 = w[k] & 0xffff, l2 = w[k] >> 16;
                    if (o2 == OP_TYPE_POINTER && w[k + 1] == t) pointee = w[k + 3];
                    k += l2;
                }
                twin[t] = newid(c);
                INS(&c->glob, OP_TYPE_POINTER, twin[t], SC_STORAGE_BUFFER, pointee);
            }
            w[i + 1] = twin[t];
            if (w[i + 2] < bound) sb[w[i + 2]] = 1;
        }
        i += len;
    }
    free(bb), free(sb), free(twin);
    return w;
}

// Loop watchdog: every structured loop (OpLoopMerge %merge %continue) gets a Function counter, and the back edge of
// its continue block leaves to the merge block once the counter reaches limit. A task or mesh workgroup that would
// spin (a loop exit the translation gets wrong) then ends instead of losing the device for every program. Back edges
// of the forms OpBranch %header and OpBranchConditional %c %header %merge are guarded; others are left alone.
// Returns a new module or NULL (nothing to guard or an unknown shape).
static uint32_t *watchdog(const uint32_t *w, uint64_t n, uint32_t limit, uint64_t *out_n) {
    enum { OP_TYPE_BOOL = 20, OP_LOOP_MERGE = 246, OP_BRANCH = 249, OP_BRANCH_COND = 250, OP_ULT = 176,
           OP_LOGICAL_AND = 167, OP_FUNCTION_PARAMETER = 55 };
    uint32_t bound = w[3], t_uint = 0, t_bool = 0;
    uint64_t fn_at = 0;
    for (uint64_t i = 5; i < n;) {
        uint32_t op = w[i] & 0xffff, len = w[i] >> 16;
        if (!len || i + len > n) return NULL;
        if (op == OP_TYPE_INT && w[i + 2] == 32 && w[i + 3] == 0 && !t_uint) t_uint = w[i + 1];
        if (op == OP_TYPE_BOOL && !t_bool) t_bool = w[i + 1];
        if (op == OP_FUNCTION && !fn_at) fn_at = i;
        i += len;
    }
    if (!t_uint || !fn_at) return NULL;
    // loops: header label, merge, continue, the function they are in (index of its first block's OpLabel)
    struct { uint32_t header, merge, cont, var; uint64_t first; } lp[256];
    int nl = 0;
    uint32_t cur = 0;
    uint64_t first = 0;
    for (uint64_t i = fn_at; i < n;) {
        uint32_t op = w[i] & 0xffff, len = w[i] >> 16;
        if (op == OP_FUNCTION) first = 0;
        if (op == OP_LABEL) { cur = w[i + 1]; if (!first) first = i; }
        if (op == OP_LOOP_MERGE && nl < 256) lp[nl].header = cur, lp[nl].merge = w[i + 1], lp[nl].cont = w[i + 2], lp[nl].first = first, nl++;
        i += len;
    }
    if (!nl) return NULL;
    Buf glob = {0}, o = {0};
    uint32_t id = bound;
    if (!t_bool) { t_bool = id++; INS(&glob, OP_TYPE_BOOL, t_bool); }
    uint32_t p_fn = id++, k0 = id++, k1 = id++, klim = id++;
    INS(&glob, OP_TYPE_POINTER, p_fn, SC_FUNCTION, t_uint);
    INS(&glob, OP_CONSTANT, t_uint, k0, 0);
    INS(&glob, OP_CONSTANT, t_uint, k1, 1);
    INS(&glob, OP_CONSTANT, t_uint, klim, limit);
    for (int k = 0; k < nl; k++) lp[k].var = id++;
    for (int i = 0; i < 5; i++) put(&o, w[i]);
    int guarded = 0;
    cur = 0;
    for (uint64_t i = 5; i < n;) {
        uint32_t op = w[i] & 0xffff, len = w[i] >> 16;
        if (i == fn_at) for (uint32_t k = 0; k < glob.n; k++) put(&o, glob.w[k]);
        if (op == OP_LABEL) cur = w[i + 1];
        int loop = -1;
        for (int k = 0; k < nl && loop < 0 && cur; k++) if (lp[k].cont == cur) loop = k;
        if (loop >= 0 && (op == OP_BRANCH || op == OP_BRANCH_COND)) {
            uint32_t h = lp[loop].header, m = lp[loop].merge, v = lp[loop].var;
            int fit = op == OP_BRANCH ? w[i + 1] == h : (len == 4 && w[i + 2] == h && w[i + 3] == m);
            if (fit) {
                uint32_t c0 = id++, c1 = id++, lt = id++;
                INS(&o, OP_LOAD, t_uint, c0, v);
                INS(&o, OP_IADD, t_uint, c1, c0, k1);
                INS(&o, OP_STORE, v, c1);
                INS(&o, OP_ULT, t_bool, lt, c1, klim);
                uint32_t cond = lt;
                if (op == OP_BRANCH_COND) { cond = id++; INS(&o, OP_LOGICAL_AND, t_bool, cond, w[i + 1], lt); }
                INS(&o, OP_BRANCH_COND, cond, h, m);
                guarded++;
                i += len;
                continue;
            }
        }
        for (uint32_t k = 0; k < len; k++) put(&o, w[i + k]);
        if (op == OP_LABEL)   // a function's first block: its loops' counters (OpVariable first in the block)
            for (int k = 0; k < nl; k++)
                if (lp[k].first == i) INS(&o, OP_VARIABLE, p_fn, lp[k].var, SC_FUNCTION, k0);
        i += len;
    }
    free(glob.w);
    if (!guarded) { free(o.w); return NULL; }
    o.w[3] = id;
    *out_n = o.n;
    return o.w;
}

uint32_t *spv_mesh_convert(const uint32_t *w0, uint64_t n, const SpvMeshInfo *mi, int with_task, uint64_t *out_n) {
    if (!w0 || n < 6 || w0[0] != 0x07230203u || (mi->kind != 6 && mi->kind != 7)) return NULL;
    int mesh = mi->kind == 7;
    uint32_t local = mi->local ? mi->local : 32;
    if (local != 32) return NULL;   // the attribute line addressing below takes one 32-vertex group per j
    Ctx c = {0};
    uint64_t gn = 0;
    uint32_t *guard = watchdog(w0, n, SPV_MESH_LOOP_LIMIT, &gn);   // NULL: no loops (or none it can guard)
    if (guard) w0 = guard, n = gn;
    c.bound = w0[3];
    uint32_t *wm = storage_buffers(&c, w0, n);
    free(guard);
    if (!wm) return NULL;
    const uint32_t *w = wm;
    uint32_t entry = 0, sh = 0, v_lid = 0, v_wg = 0;
    uint64_t ep_at = 0, caps_end = 0, ann_end = 0, fn_at = 0;
    uint32_t gvars[1024];   // the module's global variables
    int ngv = 0;
    for (uint64_t i = 5; i < n;) {
        uint32_t op = w[i] & 0xffff, len = w[i] >> 16;
        if (!len || i + len > n) { free(wm); return NULL; }
        const uint32_t *a = w + i + 1;
        if (op == OP_CAPABILITY || op == OP_EXTENSION) caps_end = i + len;
        if (op == OP_EXT_INST_IMPORT && !strcmp((const char *)(a + 1), "GLSL.std.450")) c.glsl = a[0];
        if (op == OP_ENTRY_POINT && !ep_at) ep_at = i, entry = a[1];
        if (op >= OP_DECORATE && op <= 75) ann_end = i + len;
        if (op == OP_DECORATE && len >= 4 && a[1] == DEC_BUILTIN && a[2] == BI_LOCAL_INVOCATION_ID) v_lid = a[0];
        if (op == OP_DECORATE && len >= 4 && a[1] == DEC_BUILTIN && a[2] == BI_WORKGROUP_ID) v_wg = a[0];
        if (op == OP_TYPE_VOID) c.t_void = a[0];
        if (op == OP_TYPE_INT && a[1] == 32 && a[2] == 0) c.t_uint = a[0];
        if (op == OP_TYPE_FLOAT && a[1] == 32) c.t_float = a[0];
        if (op == OP_TYPE_FUNCTION && len == 3 && a[1] == c.t_void && !c.t_fn) c.t_fn = a[0];
        if (op == OP_FUNCTION && !fn_at) fn_at = i;
        if (op == OP_VARIABLE && !fn_at && a[2] != SC_FUNCTION) {
            if (ngv == 1024) { free(wm); return NULL; }
            gvars[ngv++] = a[1];
            if (a[2] == SC_WORKGROUP && !sh) sh = a[1];
        }
        i += len;
    }
    if (!entry || !c.t_void || !c.t_uint || !c.t_float || !c.t_fn || !c.glsl || !sh || !caps_end || !ann_end || !fn_at) {
        free(wm), free(c.glob.w);
        return NULL;
    }
    for (uint64_t i = 5; i < fn_at;) {   // vector types, found by shape (they must not be declared twice)
        uint32_t op = w[i] & 0xffff, len = w[i] >> 16;
        const uint32_t *a = w + i + 1;
        if (op == OP_TYPE_VECTOR && a[1] == c.t_float && a[2] == 4) c.t_v4 = a[0];
        if (op == OP_TYPE_VECTOR && a[1] == c.t_uint && a[2] == 3) c.t_v3u = a[0];
        i += len;
    }
    uint32_t t_bool = 0;
    for (uint64_t i = 5; i < fn_at;) {
        if ((w[i] & 0xffff) == 20) t_bool = w[i + 1];   // OpTypeBool
        i += w[i] >> 16;
    }
    if (!c.t_v4) { c.t_v4 = newid(&c); INS(&c.glob, OP_TYPE_VECTOR, c.t_v4, c.t_float, 4); }
    if (!c.t_v3u) { c.t_v3u = newid(&c); INS(&c.glob, OP_TYPE_VECTOR, c.t_v3u, c.t_uint, 3); }
    if (!v_lid) { v_lid = var(&c, ptr(&c, SC_INPUT, c.t_v3u), SC_INPUT); INS(&c.ann, OP_DECORATE, v_lid, DEC_BUILTIN, BI_LOCAL_INVOCATION_ID); }
    if (!v_wg) { v_wg = var(&c, ptr(&c, SC_INPUT, c.t_v3u), SC_INPUT); INS(&c.ann, OP_DECORATE, v_wg, DEC_BUILTIN, BI_WORKGROUP_ID); }
    uint32_t p_wg_uint = ptr(&c, SC_WORKGROUP, c.t_uint);

    // The payload: the task's attr region (task: written, mesh with a task: read)
    uint32_t payload = 0, pay_words = SPV_MESH_PAYLOAD_BYTES / 4, p_pay_uint = 0;
    if (!mesh || with_task) {
        payload = var(&c, ptr(&c, SC_TASK_PAYLOAD_EXT, arr(&c, c.t_uint, pay_words)), SC_TASK_PAYLOAD_EXT);
        p_pay_uint = ptr(&c, SC_TASK_PAYLOAD_EXT, c.t_uint);
    }

    // Mesh outputs. Line k of a vertex -> generic 0..31, 32 for a position component, -1 for a line not output here.
    uint32_t v_pos = 0, p_out_float = 0, v_gen[32] = {0}, v_idx = 0, p_out_v3u = 0;
    uint32_t maxv = mi->maxv ? mi->maxv : 128, maxp = mi->maxp ? mi->maxp : 128;
    // The output arrays and the OutputVertices / OutputPrimitivesEXT modes are declared rounded up to whole rounds of the
    // workgroup: the epilogue's lane l writes slots l, l + local, ..., and the game's 126-primitive meshes (4 rounds of
    // 32) had lanes 30 and 31 store the unused slots 126 and 127 past a 126-entry index array. On the owner's AMD GPU
    // that overwrote a vertex position in the driver's output storage with the zero triangle, so every triangle using
    // that vertex was drawn as a wedge to the screen centre (the white-out, docs/phase3.md 2026-11-07). SetMeshOutputsEXT
    // still receives the real counts: nothing past them is drawn.
    uint32_t local0 = mi->local ? mi->local : 32;
    uint32_t maxv_out = (maxv + local0 - 1) / local0 * local0, maxp_out = (maxp + local0 - 1) / local0 * local0;
    int line_g[160], line_c[160], nlines = 0;
    for (int b = 0; b < 8; b++)
        if (mi->omap[0] >> b & 1) line_g[nlines] = b >= 4 ? 32 : -1, line_c[nlines] = b >= 4 ? b - 4 : 0, nlines++;
    for (int g = 0; g < 32 && nlines < 156; g++) {
        uint32_t nib = mi->omap[1 + g / 2] >> (4 * (g & 1)) & 0xf;
        for (int k = 0; k < 4; k++) if (nib >> k & 1) line_g[nlines] = g, line_c[nlines] = k, nlines++;
    }
    if (mesh) {
        p_out_float = ptr(&c, SC_OUTPUT, c.t_float);
        int any_pos = 0;
        for (int i = 0; i < nlines; i++) any_pos |= line_g[i] == 32;
        if (any_pos) {   // out gl_MeshPerVertexEXT { vec4 gl_Position; } gl_MeshVerticesEXT[maxv]
            uint32_t st = newid(&c);
            INS(&c.glob, OP_TYPE_STRUCT, st, c.t_v4);
            INS(&c.ann, OP_MEMBER_DECORATE, st, 0, DEC_BUILTIN, BI_POSITION);
            INS(&c.ann, OP_DECORATE, st, DEC_BLOCK);
            v_pos = var(&c, ptr(&c, SC_OUTPUT, arr(&c, st, maxv_out)), SC_OUTPUT);
        }
        for (int i = 0; i < nlines; i++) {
            int g = line_g[i];
            if (g < 0 || g >= 32 || v_gen[g]) continue;
            v_gen[g] = var(&c, ptr(&c, SC_OUTPUT, arr(&c, c.t_v4, maxv_out)), SC_OUTPUT);
            INS(&c.ann, OP_DECORATE, v_gen[g], DEC_LOCATION, (uint32_t)g);
        }
        p_out_v3u = ptr(&c, SC_OUTPUT, c.t_v3u);
        v_idx = var(&c, ptr(&c, SC_OUTPUT, arr(&c, c.t_v3u, maxp_out)), SC_OUTPUT);
        INS(&c.ann, OP_DECORATE, v_idx, DEC_BUILTIN, BI_PRIMITIVE_TRIANGLE_INDICES_EXT);
    }

    // OOT_MESH_DEBUG (spv_mesh.h): the record buffer, uint[] at set 2 binding 8 (the pool set's feedback buffer)
    int dbg = mesh && with_task && mi->debug;
    uint32_t v_dbg = 0, p_sb_uint = 0;
    if (dbg) {
        enum { OP_TYPE_RUNTIME_ARRAY = 29, DEC_ARRAY_STRIDE = 6, DEC_BINDING = 33, DEC_DESCRIPTOR_SET = 34, DEC_OFFSET = 35 };
        if (!t_bool) { t_bool = newid(&c); INS(&c.glob, 20, t_bool); }
        uint32_t rta = newid(&c), st = newid(&c);
        INS(&c.glob, OP_TYPE_RUNTIME_ARRAY, rta, c.t_uint);
        INS(&c.glob, OP_TYPE_STRUCT, st, rta);
        INS(&c.ann, OP_DECORATE, rta, DEC_ARRAY_STRIDE, 4);
        INS(&c.ann, OP_MEMBER_DECORATE, st, 0, DEC_OFFSET, 0);
        INS(&c.ann, OP_DECORATE, st, DEC_BLOCK);
        v_dbg = var(&c, ptr(&c, SC_STORAGE_BUFFER, st), SC_STORAGE_BUFFER);
        INS(&c.ann, OP_DECORATE, v_dbg, DEC_DESCRIPTOR_SET, 2);
        INS(&c.ann, OP_DECORATE, v_dbg, DEC_BINDING, 8);
        p_sb_uint = ptr(&c, SC_STORAGE_BUFFER, c.t_uint);
    }
#define DBG_STORE(index, value) do { uint32_t p_ = newid(&c); \
        INS(&c.fn, OP_ACCESS_CHAIN, p_sb_uint, p_, v_dbg, kuint(&c, 0), (index)); INS(&c.fn, OP_STORE, p_, (value)); } while (0)

    // The new entry
    uint32_t nentry = newid(&c), lbl = newid(&c);
    INS(&c.fn, OP_FUNCTION, c.t_void, nentry, 0, c.t_fn);
    INS(&c.fn, OP_LABEL, lbl);
    uint32_t l3 = newid(&c), lid = newid(&c), g3 = newid(&c), wg = newid(&c);
    INS(&c.fn, OP_LOAD, c.t_v3u, l3, v_lid);
    INS(&c.fn, OP_COMPOSITE_EXTRACT, c.t_uint, lid, l3, 0);
    INS(&c.fn, OP_LOAD, c.t_v3u, g3, v_wg);
    INS(&c.fn, OP_COMPOSITE_EXTRACT, c.t_uint, wg, g3, 0);
    uint32_t in0 = mi->in / 4, attr0 = mi->attr / 4, map0 = mi->map / 4;
    uint32_t k_wg = kuint(&c, 2), k_sem = kuint(&c, 0x108);   // Workgroup scope; AcquireRelease | WorkgroupMemory
    // prologue
    if (mesh && with_task)
        for (uint32_t j = 0; j < pay_words / local; j++) {
            uint32_t e = iadd(&c, lid, kuint(&c, j * local)), pp = newid(&c), v = newid(&c);
            INS(&c.fn, OP_ACCESS_CHAIN, p_pay_uint, pp, payload, e);
            INS(&c.fn, OP_LOAD, c.t_uint, v, pp);
            sh_store(&c, sh, p_wg_uint, iadd(&c, e, kuint(&c, in0)), v);
        }
    INS(&c.fn, OP_CONTROL_BARRIER, k_wg, k_wg, k_sem);
    sh_store(&c, sh, p_wg_uint, kuint(&c, in0), wg);             // input index / workgroup index (all lanes alike)
    if (mesh) {
        sh_store(&c, sh, p_wg_uint, kuint(&c, map0 + 3), kuint(&c, 0));   // no count written: no primitives
        // Shared memory keeps the previous workgroup's bytes: a triangle the game did not write this time is (0, 0, 0)
        // (no area), and a vertex it did not write has a NaN position, so a triangle that names one is dropped by
        // the rasterizer instead of being drawn as a spike to the screen centre.
        for (uint32_t j = 0; j < (3 * maxp + local - 1) / local; j++) {
            uint32_t e = newid(&c);
            INS(&c.fn, OP_EXT_INST, c.t_uint, e, c.glsl, GLSL_UMIN, iadd(&c, lid, kuint(&c, j * local)), kuint(&c, 3 * maxp - 1));
            sh_store(&c, sh, p_wg_uint, iadd(&c, e, kuint(&c, map0 + 4)), kuint(&c, 0));
        }
        for (uint32_t j = 0; j < (maxv + local - 1) / local; j++)
            for (int k = 0; k < nlines; k++)
                if (line_g[k] == 32)
                    sh_store(&c, sh, p_wg_uint, iadd(&c, lid, kuint(&c, attr0 + j * mi->lines * 32 + (uint32_t)k * 32)),
                             kuint(&c, 0x7fc00000u));
    } else {
        sh_store(&c, sh, p_wg_uint, kuint(&c, attr0 + 1), kuint(&c, 0));       // no count written: no mesh tasks
    }
    INS(&c.fn, OP_CONTROL_BARRIER, k_wg, k_wg, k_sem);
    uint32_t callr = newid(&c);
    if (!mi->no_call) INS(&c.fn, OP_FUNCTION_CALL, c.t_void, callr, entry);
    INS(&c.fn, OP_CONTROL_BARRIER, k_wg, k_wg, k_sem);
    // epilogue
    if (mesh) {
        // Every shared word the outputs need is read first, into registers, then the workgroup synchronises once more,
        // and only then are the outputs declared and written: the driver stages gl_MeshVerticesEXT and the primitive
        // indices in the same LDS as the shader's shared memory, and on the owner's AMD GPU the output stores of one
        // lane overwrote lines another lane had not read yet (the last lanes' second vertex came back as the
        // prologue's NaN and single lines held other vertices' values: docs/phase3.md, 2026-11-07).
        uint32_t cnt = sh_load(&c, sh, p_wg_uint, kuint(&c, map0 + 3)), cl = newid(&c);
        INS(&c.fn, OP_EXT_INST, c.t_uint, cl, c.glsl, GLSL_UMIN, cnt, kuint(&c, maxp));
        uint32_t nj = (maxv + local - 1) / local, np = (maxp + local - 1) / local;
        uint32_t *lv = calloc((size_t)nj * (size_t)nlines, sizeof *lv);          // line values per vertex round
        uint32_t *iv = calloc((size_t)np * 3, sizeof *iv);                        // index bytes per primitive round
        if (!lv || !iv) { free(lv), free(iv); return NULL; }
        for (uint32_t j = 0; j < nj; j++)
            for (int k = 0; k < nlines; k++) {
                if (line_g[k] < 0) continue;
                // vertex v = lid + 32 j: line k at word (v / 32) * lines * 32 + k * 32 + v % 32
                uint32_t word = attr0 + j * mi->lines * 32 + (uint32_t)k * 32;
                lv[j * (uint32_t)nlines + (uint32_t)k] = sh_load(&c, sh, p_wg_uint, iadd(&c, lid, kuint(&c, word)));
            }
        int kw = -1;   // the line of position w
        for (int k = 0; k < nlines; k++) if (line_g[k] == 32 && line_c[k] == 3) kw = k;
        uint32_t *wv = calloc((size_t)np * 3, sizeof *wv);                        // w of each index's vertex (cullw)
        if (!wv) { free(lv), free(iv); return NULL; }
        for (uint32_t j = 0; j < np; j++) {
            uint32_t p0 = iadd(&c, lid, kuint(&c, j * local)), p = newid(&c), p3 = newid(&c);
            INS(&c.fn, OP_EXT_INST, c.t_uint, p, c.glsl, GLSL_UMIN, p0, kuint(&c, maxp - 1));   // a slot past maxp reads the last
            INS(&c.fn, OP_IMUL, c.t_uint, p3, p, kuint(&c, 3));
            for (uint32_t e = 0; e < 3; e++) {
                iv[j * 3 + e] = sh_load(&c, sh, p_wg_uint, iadd(&c, p3, kuint(&c, map0 + 4 + e)));
                if (mi->cullw && kw >= 0) {   // vertex v = index & 0xff: its w line word (v / 32) * lines * 32 + kw * 32 + v % 32
                    uint32_t b8 = newid(&c), g = newid(&c), sl = newid(&c), gw = newid(&c), word = newid(&c);
                    INS(&c.fn, OP_BITWISE_AND, c.t_uint, b8, iv[j * 3 + e], kuint(&c, 0xff));
                    INS(&c.fn, OP_SHIFT_RIGHT_LOGICAL, c.t_uint, g, b8, kuint(&c, 5));
                    INS(&c.fn, OP_BITWISE_AND, c.t_uint, sl, b8, kuint(&c, 31));
                    INS(&c.fn, OP_IMUL, c.t_uint, gw, g, kuint(&c, mi->lines * 32));
                    INS(&c.fn, OP_IADD, c.t_uint, word, gw, sl);
                    wv[j * 3 + e] = sh_load(&c, sh, p_wg_uint, iadd(&c, word, kuint(&c, attr0 + (uint32_t)kw * 32)));
                }
            }
        }
        uint32_t pay_ml = 0, pay_w[8] = {0};
        if (dbg) {
            pay_ml = sh_load(&c, sh, p_wg_uint, iadd(&c, wg, kuint(&c, in0 + 4)));
            for (uint32_t pw = 0; pw < 8; pw++) pay_w[pw] = sh_load(&c, sh, p_wg_uint, kuint(&c, in0 + pw));
        }
        uint32_t w0 = 0;
        if (dbg && mi->debug == 2 && kw >= 0) w0 = sh_load(&c, sh, p_wg_uint, kuint(&c, attr0 + (uint32_t)kw * 32));
        INS(&c.fn, OP_CONTROL_BARRIER, k_wg, k_wg, k_sem);   // all reads done before any lane writes an output
        INS(&c.fn, OP_SET_MESH_OUTPUTS_EXT, kuint(&c, maxv), cl);
        uint32_t dbase = 0;
        if (dbg) {   // lane 0 of a workgroup with primitives claims a record; the rest (and later ones) use the spare
            enum { OP_LOGICAL_OR = 166, OP_LOGICAL_AND = 167, OP_SELECT = 169, OP_IEQUAL = 170, OP_UGT = 172, OP_ULT = 176, OP_ATOMIC_IADD = 234,
                   OP_BROADCAST_FIRST = 338 };
            uint32_t is0 = newid(&c), has = newid(&c), both = newid(&c), inc = newid(&c), cp = newid(&c), old = newid(&c),
                     rec = newid(&c), ok = newid(&c), okh = newid(&c), slot = newid(&c), off = newid(&c);
            INS(&c.fn, OP_IEQUAL, t_bool, is0, lid, kuint(&c, 0));
            INS(&c.fn, OP_UGT, t_bool, has, cl, kuint(&c, 0));
            uint32_t want = has;
            if (mi->debug == 2 && kw >= 0) {   // only workgroups whose vertex 0 has w != 1 (not an orthographic pass)
                uint32_t ne = newid(&c);
                want = newid(&c);
                INS(&c.fn, 171, t_bool, ne, w0, kuint(&c, 0x3f800000u));   // OpINotEqual
                INS(&c.fn, OP_LOGICAL_AND, t_bool, want, has, ne);
            }
            {   // the gate word (SPV_MESH_DEBUG_BASE + 1), set by the host: records are claimed only while it is nonzero
                // (vk_exec_draw.c mesh_capture opens it at the captured draw, so the records are that draw's)
                uint32_t gp = newid(&c), gv = newid(&c), gopen = newid(&c), want2 = newid(&c);
                INS(&c.fn, OP_ACCESS_CHAIN, p_sb_uint, gp, v_dbg, kuint(&c, 0), kuint(&c, SPV_MESH_DEBUG_BASE + 1));
                INS(&c.fn, OP_LOAD, c.t_uint, gv, gp);
                INS(&c.fn, 171, t_bool, gopen, gv, kuint(&c, 0));   // OpINotEqual
                INS(&c.fn, OP_LOGICAL_AND, t_bool, want2, want, gopen);
                want = want2;
            }
            INS(&c.fn, OP_LOGICAL_AND, t_bool, both, is0, want);
            INS(&c.fn, OP_SELECT, c.t_uint, inc, both, kuint(&c, 1), kuint(&c, 0));
            INS(&c.fn, OP_ACCESS_CHAIN, p_sb_uint, cp, v_dbg, kuint(&c, 0), kuint(&c, SPV_MESH_DEBUG_BASE));
            INS(&c.fn, OP_ATOMIC_IADD, c.t_uint, old, cp, kuint(&c, 1), kuint(&c, 0), inc);   // Device scope, relaxed
            INS(&c.fn, OP_BROADCAST_FIRST, c.t_uint, rec, kuint(&c, 3), old);                 // Subgroup = the workgroup
            INS(&c.fn, OP_ULT, t_bool, ok, rec, kuint(&c, SPV_MESH_DEBUG_RECORDS));
            INS(&c.fn, OP_LOGICAL_OR, t_bool, okh, ok, ok);
            INS(&c.fn, OP_SELECT, c.t_uint, slot, okh, rec, kuint(&c, SPV_MESH_DEBUG_RECORDS));
            INS(&c.fn, OP_IMUL, c.t_uint, off, slot, kuint(&c, SPV_MESH_DEBUG_STRIDE));
            dbase = iadd(&c, off, kuint(&c, SPV_MESH_DEBUG_BASE + 16));
            DBG_STORE(dbase, kuint(&c, maxv << 16 | maxp));
            DBG_STORE(iadd(&c, dbase, kuint(&c, 1)), wg);
            DBG_STORE(iadd(&c, dbase, kuint(&c, 2)), cl);
            DBG_STORE(iadd(&c, dbase, kuint(&c, 3)), kuint(&c, SPV_MESH_DEBUG_MAGIC));
            // word 4: the payload word this workgroup reads at byte 0x10 + 4 * index (the meshlet id in the game's programs);
            // words 5-12: payload words 0-7 (the task's header: the count at 1 and what it wrote at 0x10, 0x14)
            DBG_STORE(iadd(&c, dbase, kuint(&c, 4)), pay_ml);
            for (uint32_t pw = 0; pw < 8; pw++) DBG_STORE(iadd(&c, dbase, kuint(&c, 5 + pw)), pay_w[pw]);
        }
        for (uint32_t j = 0; j < nj; j++) {
            uint32_t v = iadd(&c, lid, kuint(&c, j * local));
            for (int k = 0; k < nlines; k++) {
                if (line_g[k] < 0) continue;
                uint32_t val = lv[j * (uint32_t)nlines + (uint32_t)k], f = newid(&c), pp = newid(&c);
                INS(&c.fn, OP_BITCAST, c.t_float, f, val);
                if (line_g[k] == 32) INS(&c.fn, OP_ACCESS_CHAIN, p_out_float, pp, v_pos, v, kuint(&c, 0), kuint(&c, (uint32_t)line_c[k]));
                else INS(&c.fn, OP_ACCESS_CHAIN, p_out_float, pp, v_gen[line_g[k]], v, kuint(&c, (uint32_t)line_c[k]));
                INS(&c.fn, OP_STORE, pp, f);
                if (dbg && line_g[k] == 32 && maxv <= 128) {
                    uint32_t v4 = newid(&c);
                    INS(&c.fn, OP_IMUL, c.t_uint, v4, v, kuint(&c, 4));
                    DBG_STORE(iadd(&c, dbase, iadd(&c, v4, kuint(&c, 400 + (uint32_t)line_c[k]))), val);
                }
                if (dbg && maxv <= 64 && nlines <= 40) {   // every line: word 700 + v * lines + k
                    uint32_t vl = newid(&c);
                    INS(&c.fn, OP_IMUL, c.t_uint, vl, v, kuint(&c, (uint32_t)nlines));
                    DBG_STORE(iadd(&c, dbase, iadd(&c, vl, kuint(&c, 700 + (uint32_t)k))), val);
                }
            }
        }
        for (uint32_t j = 0; j < np; j++) {
            uint32_t p = iadd(&c, lid, kuint(&c, j * local)), p3 = newid(&c), e3[3];
            INS(&c.fn, OP_IMUL, c.t_uint, p3, p, kuint(&c, 3));
            for (uint32_t e = 0; e < 3; e++) {
                uint32_t b8 = newid(&c);
                INS(&c.fn, OP_BITWISE_AND, c.t_uint, b8, iv[j * 3 + e], kuint(&c, 0xff));
                e3[e] = newid(&c);   // an index past the vertices (undefined in Vulkan, a hang on some GPUs) is clamped
                INS(&c.fn, OP_EXT_INST, c.t_uint, e3[e], c.glsl, GLSL_UMIN, b8, kuint(&c, maxv - 1));
                if (dbg && maxp <= 128) DBG_STORE(iadd(&c, dbase, iadd(&c, p3, kuint(&c, 16 + e))), e3[e]);
            }
            uint32_t tri = newid(&c), pp = newid(&c);
            INS(&c.fn, OP_COMPOSITE_CONSTRUCT, c.t_v3u, tri, e3[0], e3[1], e3[2]);
            if (mi->cullw && kw >= 0) {   // any vertex at w <= 0 (as float bits: sign set, or zero): the triangle is (0, 0, 0)
                uint32_t bad = kuint(&c, 0);
                for (uint32_t e = 0; e < 3; e++) {
                    uint32_t f = newid(&c), le = newid(&c), sel = newid(&c), acc = newid(&c);
                    INS(&c.fn, OP_BITCAST, c.t_float, f, wv[j * 3 + e]);
                    INS(&c.fn, 188, t_bool, le, f, kfloat(&c, 0.0f));   // OpFOrdLessThanEqual
                    INS(&c.fn, 169, c.t_uint, sel, le, kuint(&c, 1), kuint(&c, 0));   // OpSelect
                    INS(&c.fn, OP_BITWISE_OR, c.t_uint, acc, bad, sel);
                    bad = acc;
                }
                uint32_t isbad = newid(&c), tri2 = newid(&c), zero3 = newid(&c);
                INS(&c.fn, 171, t_bool, isbad, bad, kuint(&c, 0));   // OpINotEqual
                INS(&c.fn, OP_COMPOSITE_CONSTRUCT, c.t_v3u, zero3, kuint(&c, 0), kuint(&c, 0), kuint(&c, 0));
                INS(&c.fn, 169, c.t_v3u, tri2, isbad, zero3, tri);
                tri = tri2;
            }
            INS(&c.fn, OP_ACCESS_CHAIN, p_out_v3u, pp, v_idx, p);
            INS(&c.fn, OP_STORE, pp, tri);
        }
        free(lv), free(iv), free(wv);
        put(&c.fn, 1u << 16 | OP_RETURN);
    } else {
        for (uint32_t j = 0; j < pay_words / local; j++) {
            uint32_t e = iadd(&c, lid, kuint(&c, j * local));
            uint32_t v = sh_load(&c, sh, p_wg_uint, iadd(&c, e, kuint(&c, attr0))), pp = newid(&c);
            INS(&c.fn, OP_ACCESS_CHAIN, p_pay_uint, pp, payload, e);
            INS(&c.fn, OP_STORE, pp, v);
        }
        uint32_t cnt = sh_load(&c, sh, p_wg_uint, kuint(&c, attr0 + 1)), cl = newid(&c);
        INS(&c.fn, OP_EXT_INST, c.t_uint, cl, c.glsl, GLSL_UMIN, cnt, kuint(&c, SPV_MESH_MAX_TASK_GROUPS));
        INS(&c.fn, OP_EMIT_MESH_TASKS_EXT, cl, kuint(&c, 1), kuint(&c, 1), payload);   // terminates the block
    }
    put(&c.fn, 1u << 16 | OP_FUNCTION_END);

    // Assemble: the header (SPIR-V 1.4 at least), capabilities (+ MeshShadingEXT, the extension), the entry point with
    // every global variable, new annotations after the last one, new globals before the first function, the new entry
    // at the end.
    Buf o = {0};
    for (int i = 0; i < 5; i++) put(&o, w[i]);
    if (o.w[1] < 0x00010400u) o.w[1] = 0x00010400u;
    for (uint64_t i = 5; i < n;) {
        uint32_t op = w[i] & 0xffff, len = w[i] >> 16;
        const uint32_t *a = w + i + 1;
        if (i == fn_at) for (uint32_t k = 0; k < c.glob.n; k++) put(&o, c.glob.w[k]);
        if (op == OP_ENTRY_POINT && i == ep_at) {
            uint32_t nw = 0;   // the name: up to the first word with a zero byte
            while (2 + nw < len - 1) {
                uint32_t x = a[2 + nw++];
                if (!(x & 0xff000000u) || !(x & 0xff0000u) || !(x & 0xff00u) || !(x & 0xffu)) break;
            }
            Buf e = {0};
            put(&e, mesh ? EM_MESH_EXT : EM_TASK_EXT);
            put(&e, nentry);
            for (uint32_t k = 0; k < nw; k++) put(&e, a[2 + k]);
            for (int k = 0; k < ngv; k++) put(&e, gvars[k]);
            for (int k = 0; k < c.nvars; k++) put(&e, c.vars[k]);
            put(&o, (e.n + 1) << 16 | OP_ENTRY_POINT);
            for (uint32_t k = 0; k < e.n; k++) put(&o, e.w[k]);
            free(e.w);
            i += len;
            continue;
        }
        if (op == OP_EXECUTION_MODE && a[0] == entry) {
            put(&o, w[i]), put(&o, nentry);
            for (uint32_t k = 2; k < len; k++) put(&o, a[k - 1]);
            if (a[1] == MODE_LOCAL_SIZE && mesh) {
                INS(&o, OP_EXECUTION_MODE, nentry, MODE_OUTPUT_VERTICES, maxv_out);
                INS(&o, OP_EXECUTION_MODE, nentry, MODE_OUTPUT_PRIMITIVES_EXT, maxp_out);
                INS(&o, OP_EXECUTION_MODE, nentry, MODE_OUTPUT_TRIANGLES_EXT);
            }
            i += len;
            continue;
        }
        for (uint32_t k = 0; k < len; k++) put(&o, w[i + k]);
        if (i + len == caps_end) {
            INS(&o, OP_CAPABILITY, CAP_MESH_SHADING_EXT);
            if (dbg) {   // GroupNonUniform(Ballot): BroadcastFirst
                INS(&o, OP_CAPABILITY, 61);
                INS(&o, OP_CAPABILITY, 64);
            }
            static const char ext[] = "SPV_EXT_mesh_shader";   // 19 chars + NUL: 5 words
            uint32_t ew[5] = {0};
            memcpy(ew, ext, sizeof ext);
            ins(&o, OP_EXTENSION, 5, ew);
        }
        if (i + len == ann_end) for (uint32_t k = 0; k < c.ann.n; k++) put(&o, c.ann.w[k]);
        i += len;
    }
    for (uint32_t k = 0; k < c.fn.n; k++) put(&o, c.fn.w[k]);
    o.w[3] = c.bound;
    free(c.glob.w), free(c.ann.w), free(c.fn.w), free(wm);
    *out_n = o.n;
    return o.w;
}
