// Written for ocarina-of-time-recomp (MIT, Copyright (c) 2026 Jeff Aigner).
//
// Guest setjmp / longjmp. The game imports both from the SDK (FreeType's smooth rasterizer, statically linked in main,
// longjmps out of gray_convert_glyph_inner on every Raster_Overflow and retries the band smaller). The SDK's longjmp
// restores the registers and returns to the setjmp call's return address, which in lifted code is the middle of a C
// function: rt_dispatch cannot enter it ("no function at guest pc ... not a function start", 2026-11-06, run togame20,
// main+0x1661690).
//
// recomp/gen.py turns a BL to the setjmp import into RT_SETJMP_CALL (cpu.h): the lifted caller sets a host jmp_buf in
// its own frame, kept here by the guest jmp_buf's address, and calls the guest setjmp as usual (it saves the guest
// registers into the guest jmp_buf). longjmp, replaced here, restores the guest registers from the guest jmp_buf (the
// SDK layout below), sets x0 to the value (1 for 0), and longjmps to that host frame, which then reloads its register
// locals from the Cpu. The host frames in between are dropped without unwinding (no SEH, as ExitThread does); the
// thread's host-call depth goes back to what it was at the setjmp.
//
// SDK setjmp (sdk+0xaec40) layout: x19-x28 at 0x00-0x48, x29 at 0x50, x30 at 0x58 (as lr ^ 1, PAC-signed: no-op here),
// a checksum at 0x60, sp at 0x68, d8-d15 at 0x70-0xa8.
#include <windows.h>
#include <setjmp.h>
#include <stdint.h>

#include "host.h"

#define SLOTS 256

typedef struct {
    uint64_t guest;      // the guest jmp_buf's address (0: free)
    jmp_buf jb;
    int depth;           // the thread's host-call depth at the setjmp
    void *thread;
} Slot;

static Slot slots[SLOTS];
static SRWLOCK lock = SRWLOCK_INIT;
static unsigned next;

jmp_buf *rt_setjmp_slot(Cpu *c) {
    uint64_t g = c->x[0];
    AcquireSRWLockExclusive(&lock);
    Slot *s = NULL;
    for (int i = 0; i < SLOTS && !s; i++)
        if (slots[i].guest == g) s = &slots[i];
    if (!s) s = &slots[next++ % SLOTS];   // the oldest: a jmp_buf is used again soon after its setjmp or not at all
    s->guest = g;
    s->thread = c->thread;
    s->depth = c->thread ? *kern_host_depth(c->thread) : 0;
    ReleaseSRWLockExclusive(&lock);
    return &s->jb;
}

static void ov_longjmp(Cpu *c) {
    uint64_t g = c->x[0];
    int val = (int)c->x[1];
    Slot *s = NULL;
    AcquireSRWLockShared(&lock);
    for (int i = 0; i < SLOTS && !s; i++)
        if (slots[i].guest == g) s = &slots[i];
    ReleaseSRWLockShared(&lock);
    if (!s || s->thread != c->thread)
        rt_fatal("longjmp(%#llx): no setjmp of this thread recorded that jmp_buf (a setjmp call recomp/gen.py does not see?)",
                 (unsigned long long)g);
    for (int i = 0; i < 10; i++) c->x[19 + i] = LD64(g + 8 * i);
    c->x[29] = LD64(g + 0x50);
    c->x[30] = LD64(g + 0x58) ^ 1;
    c->sp = LD64(g + 0x68);
    for (int i = 0; i < 8; i++) c->v[8 + i] = (__uint128_t)LD64(g + 0x70 + 8 * i);   // writing Dn clears the upper half
    c->x[0] = (uint64_t)(uint32_t)(val ? val : 1);
    if (c->thread) *kern_host_depth(c->thread) = s->depth;
    longjmp(s->jb, 1);
}

HOST_OVERRIDE("longjmp", ov_longjmp);   // main imports longjmp (= the SDK's _longjmp, one address)
