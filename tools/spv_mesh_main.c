// Written for ocarina-of-time-recomp (MIT, Copyright (c) 2026 Jeff Aigner).
// Offline check of runtime/spv_mesh.c: converts a translated task / mesh compute module (tools/dev/mesh_check.py).
//   spv_mesh <in.spv> <out.spv> <kind> <in> <attr> <map> <total> <maxv> <maxp> <lines> <local> <omap hex> <with_task>
// Build: clang -O1 -o build/spv_mesh_main.exe tools/spv_mesh_main.c runtime/spv_mesh.c -Iruntime
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "spv_mesh.h"

int main(int argc, char **argv) {
    if (argc != 14) { fprintf(stderr, "usage: spv_mesh in out kind in attr map total maxv maxp lines local omap with_task\n"); return 2; }
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 1;
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint32_t *w = malloc((size_t)size);
    if (!w || fread(w, 1, (size_t)size, f) != (size_t)size) return 1;
    fclose(f);
    SpvMeshInfo mi = {0};
    uint32_t *v[] = {&mi.kind, &mi.in, &mi.attr, &mi.map, &mi.total, &mi.maxv, &mi.maxp, &mi.lines, &mi.local};
    for (int i = 0; i < 9; i++) *v[i] = (uint32_t)strtoul(argv[3 + i], NULL, 0);
    for (int i = 0; i < 17 && argv[12][2 * i]; i++) { unsigned b; sscanf(argv[12] + 2 * i, "%2x", &b); mi.omap[i] = (uint8_t)b; }
    mi.debug = getenv("OOT_MESH_DEBUG") && *getenv("OOT_MESH_DEBUG") == '1';
    uint64_t n = 0;
    uint32_t *o = spv_mesh_convert(w, (uint64_t)size / 4, &mi, atoi(argv[13]), &n);
    if (!o) { fprintf(stderr, "spv_mesh_convert refused the module\n"); return 1; }
    f = fopen(argv[2], "wb");
    if (!f) return 1;
    fwrite(o, 4, n, f);
    fclose(f);
    printf("%llu words\n", (unsigned long long)n);
    return 0;
}
