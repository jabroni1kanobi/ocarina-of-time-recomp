// Written for ocarina-of-time-recomp (MIT, Copyright (c) 2026 Jeff Aigner).
//
// NVN2 mesh draws (docs/mesh-plan.md, behaviour reference docs/mesh-spec.md).
//
// Probe (OOT_MESH_PROBE=1): DrawMeshTasksIndirect's argument records live in a GPU-only pool and are written by the
// game's culling dispatches, so the CPU never sees them. The first records met in replay are copied, outside the
// render pass, into a host-visible buffer and logged a few hundred frames later, to settle their layout.
#include <stdio.h>
#include <string.h>

#include "cfg.h"
#include "host.h"
#include "vk.h"
#include "vk_exec.h"

#define PROBE_N 32
#define PROBE_BYTES 32
static struct {
    int on;                              // -1 unknown (set below), 0 off, 1 on
    VkBuffer buf;
    VmaAllocation mem;
    uint8_t *map;
    struct { uint64_t addr; VkBuffer src; VkDeviceSize off; int state; } e[PROBE_N];   // state: 1 noted, 2 copied
    int n;
    uint64_t copied_frame;
    int logged;
} PR = {.on = -1};

static int probe_on(void) {
    if (PR.on < 0) {
        const char *e = cfg_get("OOT_MESH_PROBE");
        PR.on = e && *e == '1';
    }
    return PR.on;
}

void vk_mesh_probe_note(VkExec *ex, uint64_t ind) {
    (void)ex;
    if (!probe_on() || PR.n >= PROBE_N) return;
    VkMemSpan sp;
    if (!vk_mem_resolve(ind, PROBE_BYTES, &sp)) return;
    PR.e[PR.n].addr = ind, PR.e[PR.n].src = sp.buffer, PR.e[PR.n].off = sp.offset, PR.e[PR.n].state = 1;
    PR.n++;
}

// After a render pass ends: the noted records are copied; later frames log them.
void vk_mesh_probe_flush(VkExec *ex) {
    if (PR.on != 1) return;
    VkCtx *c = vk_ctx();
    if (!PR.buf) {
        VkBufferCreateInfo bi = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = PROBE_N * PROBE_BYTES,
                                 .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT};
        VmaAllocationCreateInfo ai = {.usage = VMA_MEMORY_USAGE_AUTO_PREFER_HOST,
                                      .flags = VMA_ALLOCATION_CREATE_HOST_ACCESS_RANDOM_BIT | VMA_ALLOCATION_CREATE_MAPPED_BIT};
        VmaAllocationInfo info;
        if (vmaCreateBuffer(c->vma, &bi, &ai, &PR.buf, &PR.mem, &info) != VK_SUCCESS) { PR.on = 0; return; }
        PR.map = info.pMappedData;
        memset(PR.map, 0xcd, PROBE_N * PROBE_BYTES);
    }
    uint64_t frame = __atomic_load_n(&gfx_counters.frames, __ATOMIC_RELAXED);
    int any = 0;
    for (int i = 0; i < PR.n; i++) {
        if (PR.e[i].state != 1) continue;
        if (!any) {
            VkMemoryBarrier mb = {.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER, .srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT,
                                  .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT};
            vkCmdPipelineBarrier(ex->cb, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 1, &mb, 0, NULL, 0, NULL);
            any = 1;
        }
        VkBufferCopy r = {PR.e[i].off, (VkDeviceSize)i * PROBE_BYTES, PROBE_BYTES};
        vkCmdCopyBuffer(ex->cb, PR.e[i].src, PR.buf, 1, &r);
        PR.e[i].state = 2;
        PR.copied_frame = frame;
    }
    if (any) ex->recorded++;
    if (PR.n == PROBE_N && !PR.logged && PR.copied_frame && frame > PR.copied_frame + 300) {
        PR.logged = 1;
        vmaInvalidateAllocation(c->vma, PR.mem, 0, VK_WHOLE_SIZE);
        for (int i = 0; i < PR.n; i++) {
            const uint32_t *w = (const uint32_t *)(PR.map + i * PROBE_BYTES);
            rt_log("vk_mesh: probe %2d: %#llx: %08x %08x %08x %08x %08x %08x %08x %08x", i, (unsigned long long)PR.e[i].addr,
                   w[0], w[1], w[2], w[3], w[4], w[5], w[6], w[7]);
        }
    }
}
