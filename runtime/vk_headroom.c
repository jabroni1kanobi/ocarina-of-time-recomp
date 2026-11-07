// From metroid-prime-4-recomp runtime/vk_headroom.c @ 586e91e1b6f38c9e512e39f2d47a046fdf273cf0 (MIT, Copyright (c) 2026 Jeff Aigner), copied with mp4 renamed to oot.
// From fe3h-pc-recomp runtime/vk_headroom.c @ 47ef99126793dbd13e4a475feb910a13ef6639b8 (MIT, Copyright (c) 2026 Jeff Aigner), copied with fe3h renamed to mp4 only.
// From totk-pc-recomp runtime/vk_headroom.c @ c944f9f94520874f2a669add30dd08d29ee9f7b5 (MIT, Copyright (c) 2026 Valiantsin Dzerakh), copied with totk renamed to fe3h only.
// VRAM headroom: the next VMA block of a memory type is allocated on a background thread, not on the render thread.
//
// VMA places an allocation in an existing block of its memory type and allocates a new block (vkAllocateMemory, 32 to
// 256 MiB) only when none has room. On the render thread that took 8-19 ms per block, several times in the first
// minute of play while VRAM grows (textures from create_tex, mirrors). A new VkDeviceMemory made by any other thread
// (VMA's pfnAllocate callback) wakes this thread for its memory type, which it then checks every 500 ms. A check
// allocates half a default block of that type (the largest size VMA still places in blocks, not in dedicated memory).
// When a block has that much room the allocation lands there and is freed at once. When none has, VMA allocates a new
// block here, and the allocation stays as that block's anchor: VMA deletes an empty block at the next free of its
// type (VmaBlockVector::Free, "we have another empty block"), so a spare block left empty lasted until the next
// texture went. The previous anchor is freed then, which gives its half block to the render thread, so at most half
// a block per memory type is held back.
// Under VRAM pressure no headroom is kept: the spare block counts against the budget that decides whether a pool gets
// its VRAM mirror (vk_memory.c), so while use plus two blocks passes 85% of the budget the anchor goes and nothing new
// is allocated.
// OOT_VRAM_HEADROOM=0 turns it off.
#include <stdlib.h>
#include <windows.h>

#include "cfg.h"
#include "nvn_api.h"
#include "host.h"
#include "vk.h"

#define HR_PERIOD_MS 500
#define HR_SMALL_HEAP (1ull << 30)   // VMA_SMALL_HEAP_MAX_SIZE: smaller heaps use blocks of a heap eighth

static struct {
    int on, stop;
    SRWLOCK lock;             // held around each top-up; vk_headroom_stop takes it before the allocator goes
    HANDLE ev;
    DWORD tid;
    volatile LONG64 types;    // memory types a thread other than this one allocated device memory of
    volatile LONG64 made;     // blocks this thread brought in
    volatile LONG64 other;    // device memory of 32 MiB or more other threads allocated (VMA blocks, large dedicated)
    VmaAllocation anchor[64]; // per memory type: the half block that keeps the newest spare block from being deleted
} H;

static void sp_mark(int on);

static void VKAPI_PTR on_allocate(VmaAllocator a, uint32_t type, VkDeviceMemory m, VkDeviceSize size, void *user) {
    (void)a, (void)m, (void)size, (void)user;
    if (!H.on || type >= 64) return;
    if (GetCurrentThreadId() == H.tid) {
        InterlockedIncrement64(&H.made);
        return;
    }
    if (size >= 32ull << 20) InterlockedIncrement64(&H.other);
    InterlockedOr64(&H.types, (LONG64)(1ull << type));
    SetEvent(H.ev);
}

const VmaDeviceMemoryCallbacks *vk_headroom_callbacks(void) {
    static const VmaDeviceMemoryCallbacks cb = {.pfnAllocate = on_allocate};
    H.on = !cfg_get("OOT_VRAM_HEADROOM") || cfg_bool("OOT_VRAM_HEADROOM");
    sp_mark(H.on ? -1 : 0);   // the spare pools (below) need the thread; sp_init on its first wake
    return H.on ? &cb : NULL;
}

static VkDeviceSize block_size(const VkCtx *c, uint32_t type) {
    VkDeviceSize heap = c->mem_props.memoryHeaps[c->mem_props.memoryTypes[type].heapIndex].size;
    return heap <= HR_SMALL_HEAP ? heap / 8 : 256ull << 20;   // VMA's default preferredLargeHeapBlockSize
}

static int room(const VkCtx *c, uint32_t type, VkDeviceSize block) {
    VmaBudget b[VK_MAX_MEMORY_HEAPS];
    vmaGetHeapBudgets(c->vma, b);
    const VmaBudget *h = &b[c->mem_props.memoryTypes[type].heapIndex];
    return h->budget && h->usage + 2 * block <= h->budget / 100 * 85;
}

static void drop_anchor(const VkCtx *c, uint32_t type) {
    if (H.anchor[type]) vmaFreeMemory(c->vma, H.anchor[type]);
    H.anchor[type] = NULL;
}

static void top_up(const VkCtx *c, uint32_t type) {
    VkDeviceSize block = block_size(c, type);
    if (!room(c, type, block)) {
        drop_anchor(c, type);
        return;
    }
    VkMemoryRequirements req = {.size = block / 2, .alignment = 256, .memoryTypeBits = 1u << type};
    VmaAllocationCreateInfo ai = {.usage = VMA_MEMORY_USAGE_UNKNOWN};
    VmaAllocation a;
    LONG64 before = H.made;
    if (vmaAllocateMemory(c->vma, &req, &ai, &a, NULL) != VK_SUCCESS) return;
    if (H.made == before) {   // there was room
        vmaFreeMemory(c->vma, a);
        return;
    }
    drop_anchor(c, type);
    H.anchor[type] = a;
    static volatile LONG logged;
    if (InterlockedIncrement(&logged) <= 32)
        rt_log("vk: headroom: a %.0f MiB block of memory type %u allocated ahead (%lld so far; %lld allocations of 32 MiB "
               "or more on other threads)", (double)block / 1048576.0, type, (long long)H.made, (long long)H.other);
}

// Spare pools. The top-up above still runs vkAllocateMemory inside VMA's lock of the memory type's block vector, and
// every allocation of that type takes the same lock: in Link's house a 256 MiB block took 511 ms in the kernel on this
// thread while the render thread waited for the lock the whole time (profile perf11, 2026-11-07, at 8.4 of 15.4 GB:
// not pressure, the call itself). So this thread also keeps spare memory in custom pools of one block each, made by
// vmaCreatePool under a lock no one else holds yet, and vk_hr_create_image / vk_hr_create_buffer place device-only
// resources there first: a full pool fails at once (it cannot grow) and the next is tried; when none has room the
// resource goes the usual way. Filled to OOT_VRAM_POOL_FREE_MB (default 4096) free across the pools while the budget
// has room (room()), and filled again only once the free part falls under a quarter of that: an allocation in play
// stalled the frame by 0.6-1 s even on this thread (perf14/15/18: the driver or the OS holds the device meanwhile).
// Topping up to 2 GiB all the time made pools during every camera turn of Link's house (perf18: a 998 ms pool, the
// frame with it 1011 ms); the reserve made at load covers the turn, so play rarely needs an allocation.
// OOT_VRAM_POOLS=0 turns them off.
#define SP_MAX 256
#define SP_BLOCK (256ull << 20)
static struct {
    int on;                   // -1 before vk_headroom_callbacks, 0 off
    uint32_t type;            // the device-local memory type that is not host-visible
    VkDeviceSize keep;        // free bytes the pools are filled to
    int filling;              // 1 until the free part reaches keep; again once it falls under keep / 4
    VmaPool pool[SP_MAX];
    volatile LONG n, hint;    // pools published (pool[0..n) never change while running), the last one that fitted
    volatile LONG64 hits, misses;
} SP;

static void sp_mark(int on) { SP.on = on; }

static void sp_init(const VkCtx *c) {
    SP.on = 0;
    if (cfg_get("OOT_VRAM_POOLS") && !cfg_bool("OOT_VRAM_POOLS")) return;
    for (uint32_t t = 0; t < c->mem_props.memoryTypeCount; t++) {
        VkMemoryPropertyFlags f = c->mem_props.memoryTypes[t].propertyFlags;
        if ((f & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT) && !(f & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT) &&
            c->mem_props.memoryHeaps[c->mem_props.memoryTypes[t].heapIndex].size > HR_SMALL_HEAP) {
            const char *k = cfg_get("OOT_VRAM_POOL_FREE_MB");
            SP.keep = (VkDeviceSize)(k && *k ? atoi(k) : 4096) << 20;
            SP.type = t, SP.on = 1, SP.filling = 1;
            rt_log("vk: headroom: spare pools of %llu MiB blocks for device-only resources, memory type %u, %llu MiB kept "
                   "free (OOT_VRAM_POOLS=0 turns them off)", (unsigned long long)(SP_BLOCK >> 20), t,
                   (unsigned long long)(SP.keep >> 20));
            return;
        }
    }
}

static void sp_top_up(const VkCtx *c) {
    LONG n = SP.n;
    VkDeviceSize free = 0;
    for (LONG i = 0; i < n; i++) {
        VmaStatistics s;
        vmaGetPoolStatistics(c->vma, SP.pool[i], &s);
        free += s.blockBytes - s.allocationBytes;
    }
    if (free < SP.keep / 4) SP.filling = 1;
    if (!SP.filling) return;
    if (free >= SP.keep) SP.filling = 0;
    while (free < SP.keep && n < SP_MAX && room(c, SP.type, SP_BLOCK)) {
        VmaPoolCreateInfo pi = {.memoryTypeIndex = SP.type, .blockSize = SP_BLOCK, .minBlockCount = 1, .maxBlockCount = 1};
        VmaPool p;
        uint64_t t0 = kern_now_ns();
        if (vmaCreatePool(c->vma, &pi, &p) != VK_SUCCESS) return;
        double ms = (kern_now_ns() - t0) / 1e6;
        SP.pool[n] = p;
        InterlockedExchange(&SP.n, ++n);   // publishes pool[n - 1]
        free += SP_BLOCK;
        if (free >= SP.keep) SP.filling = 0;
        static volatile LONG logged;
        if (InterlockedIncrement(&logged) <= 32)
            rt_log("vk: headroom: spare pool %ld made in %.1f ms at frame %llu (%.0f MiB free in the pools; %lld resources "
                   "placed in them, %lld found no room)", n, ms,
                   (unsigned long long)__atomic_load_n(&gfx_counters.frames, __ATOMIC_RELAXED), (double)free / 1048576.0,
                   (long long)SP.hits, (long long)SP.misses);
    }
}

// Whether an allocation can go to the spare pools: device-only, no pool or mapping of its own.
static int sp_fits(const VmaAllocationCreateInfo *ai) {
    if (SP.on != 1 || !SP.n || ai->pool || (ai->requiredFlags & ~VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT)) return 0;
    if (ai->flags & (VMA_ALLOCATION_CREATE_MAPPED_BIT | VMA_ALLOCATION_CREATE_HOST_ACCESS_SEQUENTIAL_WRITE_BIT |
                     VMA_ALLOCATION_CREATE_HOST_ACCESS_RANDOM_BIT | VMA_ALLOCATION_CREATE_DEDICATED_MEMORY_BIT))
        return 0;
    return ai->usage == VMA_MEMORY_USAGE_AUTO_PREFER_DEVICE || ai->usage == VMA_MEMORY_USAGE_AUTO ||
           ai->usage == VMA_MEMORY_USAGE_GPU_ONLY;
}

// The pools in turn from the last one that fitted; 1 when *a now holds memory of one of them.
static int sp_alloc(const VkCtx *c, const VkMemoryRequirements *req, VkImage img, VkBuffer buf, VmaAllocation *a,
                    VmaAllocationInfo *info) {
    if (!(req->memoryTypeBits >> SP.type & 1) || req->size > SP_BLOCK / 2) return 0;
    LONG n = SP.n, h = SP.hint;
    VmaAllocationCreateInfo p = {.flags = VMA_ALLOCATION_CREATE_NEVER_ALLOCATE_BIT};
    for (LONG k = 0; k < n; k++) {
        LONG i = (h + k) % n;
        p.pool = SP.pool[i];
        VkResult r = img ? vmaAllocateMemoryForImage(c->vma, img, &p, a, info)
                         : vmaAllocateMemoryForBuffer(c->vma, buf, &p, a, info);
        if (r != VK_SUCCESS) continue;
        if ((img ? vmaBindImageMemory(c->vma, *a, img) : vmaBindBufferMemory(c->vma, *a, buf)) == VK_SUCCESS) {
            if (i != h) InterlockedExchange(&SP.hint, i);
            InterlockedIncrement64(&SP.hits);
            return 1;
        }
        vmaFreeMemory(c->vma, *a);
        return 0;
    }
    InterlockedIncrement64(&SP.misses);
    if (H.ev) SetEvent(H.ev);
    return 0;
}

VkResult vk_hr_create_image(const VkImageCreateInfo *ci, const VmaAllocationCreateInfo *ai, VkImage *img,
                            VmaAllocation *a, VmaAllocationInfo *info) {
    const VkCtx *c = vk_ctx();
    if (sp_fits(ai)) {
        VkImage im;
        if (vkCreateImage(c->device, ci, NULL, &im) == VK_SUCCESS) {
            VkMemoryRequirements req;
            vkGetImageMemoryRequirements(c->device, im, &req);
            if (sp_alloc(c, &req, im, VK_NULL_HANDLE, a, info)) {
                *img = im;
                return VK_SUCCESS;
            }
            vkDestroyImage(c->device, im, NULL);
        }
    }
    return vmaCreateImage(c->vma, ci, ai, img, a, info);
}

VkResult vk_hr_create_buffer(const VkBufferCreateInfo *bi, const VmaAllocationCreateInfo *ai, VkBuffer *buf,
                             VmaAllocation *a, VmaAllocationInfo *info) {
    const VkCtx *c = vk_ctx();
    if (sp_fits(ai)) {
        VkBuffer b;
        if (vkCreateBuffer(c->device, bi, NULL, &b) == VK_SUCCESS) {
            VkMemoryRequirements req;
            vkGetBufferMemoryRequirements(c->device, b, &req);
            if (sp_alloc(c, &req, VK_NULL_HANDLE, b, a, info)) {
                *buf = b;
                return VK_SUCCESS;
            }
            vkDestroyBuffer(c->device, b, NULL);
        }
    }
    return vmaCreateBuffer(c->vma, bi, ai, buf, a, info);
}

static void sp_release(const VkCtx *c) {   // H.lock held; the pools that still hold allocations stay (VMA would assert)
    LONG n = SP.n, kept = 0;
    InterlockedExchange(&SP.n, 0);
    for (LONG i = 0; i < n; i++) {
        VmaStatistics s;
        vmaGetPoolStatistics(c->vma, SP.pool[i], &s);
        if (s.allocationCount) kept++;
        else vmaDestroyPool(c->vma, SP.pool[i]);
    }
    if (kept) rt_log("vk: headroom: %ld spare pools still hold allocations at shutdown; left to the allocator", kept);
}

static DWORD WINAPI headroom_main(void *arg) {
    (void)arg;
    uint64_t watch = 0;   // memory types seen so far
    for (;;) {
        DWORD w = WaitForSingleObject(H.ev, watch ? HR_PERIOD_MS : INFINITE);
        if (w == WAIT_FAILED) return 0;
        watch |= (uint64_t)InterlockedExchange64(&H.types, 0);
        AcquireSRWLockExclusive(&H.lock);
        const VkCtx *c = H.stop ? NULL : vk_ctx();
        if (c && SP.on < 0) sp_init(c);
        if (c && SP.on == 1) sp_top_up(c);
        for (uint32_t t = 0; c && t < c->mem_props.memoryTypeCount && t < 64; t++)
            if (watch >> t & 1) top_up(c, t);
        int stop = H.stop;
        if (stop && (c = vk_ctx()))
            for (uint32_t t = 0; t < 64; t++) drop_anchor(c, t);
        ReleaseSRWLockExclusive(&H.lock);
        if (stop) return 0;
    }
}

// Before the allocator is destroyed: waits for a running top-up, frees the anchors, and the thread allocates no more.
void vk_headroom_stop(void) {
    if (!H.ev) return;
    AcquireSRWLockExclusive(&H.lock);
    H.stop = 1;
    const VkCtx *c = vk_ctx();
    for (uint32_t t = 0; c && t < 64; t++) drop_anchor(c, t);
    if (c) sp_release(c);
    ReleaseSRWLockExclusive(&H.lock);
    SetEvent(H.ev);
}

void vk_headroom_start(void) {
    if (!H.on || H.ev) return;
    InitializeSRWLock(&H.lock);
    H.ev = CreateEventW(NULL, FALSE, FALSE, NULL);
    HANDLE t = H.ev ? CreateThread(NULL, 0, headroom_main, NULL, 0, &H.tid) : NULL;
    if (!t) {
        H.on = 0;
        rt_log("vk: headroom: no thread, new VMA blocks stay on the allocating thread");
        return;
    }
    SetThreadPriority(t, THREAD_PRIORITY_BELOW_NORMAL);
    CloseHandle(t);
    rt_log("vk: headroom: the next VMA block of each memory type in use is allocated on a background thread "
           "(OOT_VRAM_HEADROOM=0 turns it off)");
}
