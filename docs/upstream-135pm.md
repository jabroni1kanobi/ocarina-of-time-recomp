# Upstream 13:35 tree vs 11:39 tree, and what ours lacks (checked 2026-11-06 19:39Z)

Upstream: a parallel working copy of this tree, dated 2026-11-06 13:35 local (not public).
Method: `diff -rq` against the 11:39 tree (read only), then grep of our tree.

## What changed since 11:39 (small: 8 files + 2 new)
Changed: CLAUDE.md, docs/phase3.md, runtime/{gfx_shader.c, sm86_lower.c, vk_dump.c, vk_exec.h, vk_exec_draw.c}, tools/run.py.
New: docs/bindless.md, tools/xlate/patches/0018-maxwell-nan-semantics-for-min-max-clamp-and-f2i.patch.
No change to recomp/*, Program.cs, or any mesh/task, bindless-binding, texture or lighting code. Nothing touches fog/exposure.

## Upstream status headlines (phase3.md, new text only)
1. "GPU timeout on the first level frames: solved." Cause: NVN2 bank-0 resource tables were placed after the pushed
   constants by the length the game pushed; the shader's own layout ends them at the u16 at control +0xba. Textures of
   compute kernels eb67c232 / c82d3cba were unbound. Level then runs 662 frames (before: device lost after 14).
2. "Scene behind the dialogue was black" for two reasons: (a) last-pass kernels 0a9afab0 (1080p->1440p) and 7034cdb8
   did not lower; (b) volumetric fog kernel 1a62084a writes only 0/NaN (still open upstream; they skip it with
   OOT_SKIP_CS). They also say materials still sample a placeholder: true bindless is "the next big piece"
   (docs/bindless.md is a plan only, no code).
Net: upstream is BEHIND us. We fixed the fog NaN (FSEL immediate) and have the bindless pool set; they have neither.

## Changes, and whether ours has them
| Change (file:function) | Fixes | Ours |
|---|---|---|
| vk_exec_draw.c bank-0 table base from control u16 +0xba (instead of 0x10 + pushed length) | compute textures/storage unbound, GPU hang from kernel pair | Already equivalent: sm86_lower.c:63-71 (Sm86Control c0_ssbo/c0_tex/c0_img from +0xba, +0xb2/b3/b6) |
| sm86_lower.c lower_suld (0xb97), lower_redux (0x3c4), HMUL2 | 23 / 27 level kernels refused | Have all (lower_suld 1658, lower_redux 2396, lower_hmul2 1934). HMUL2: ours only 0x232 and 0x832 |
| sm86_lower.c TLD4 per-tap offsets (PTP) + fp16 results | final pass 7034cdb8 | Have (lower_tld4 notes PTP+F16, 12 programs) |
| sm86_lower.c lower_hsetp2 (0x234 reg, 0x434 imm), insn_regs predicate-only for 0x034 | upscaler 0a9afab0 failed to lower | MISSING: no 0x234/0x434 case in the dispatcher (only live-range tables mention 0x034) |
| sm86_lower.c lower_hmul2 immediate/const forms 0x432, 0xa32 | same kernels | MISSING (ours handles 0x232, 0x832 only) |
| sm86_lower.c lower_hmnmx2 negated operands (bits 73, 62) via HADD2 0-x | upscaler max(-x,-y) | MISSING: ours fails "HMNMX2 mode" when bit 62/73 set (and also rejects bits 63/72 as upstream does) |
| Patch 0018 (Instructions.cs): GenerateMaximum/Minimum/Clamp use NMax/NMin/NClamp; F2I saturates to int range and maps NaN to 0 (InstEmitConversion.cs) | SPIR-V FMax/FMin/FClamp/convert are UNDEFINED for NaN; Maxwell defines them. A NaN or inf in lighting math can turn into a huge value, blow a loop count, or win a max() in the bloom/exposure chain | MISSING: tools/xlate/patches stops at 0017, no NMin/NMax/NClamp. Not a proven cause upstream ("not the cause"), but see recs |
| vk_dump.c / vk_exec_draw.c: OOT_DUMP=cs:<id>, vk_dump_note (draw/dispatch skipped for missing shader, with code hash), dump pass cap 400->1200 | diagnostics | Missing (diagnostic only). Useful: our dumps stop at 400 passes, mesh/task or unlowerable draws get dropped silently |
| vk_exec_draw.c OOT_SKIP_CS=all/list/!list; OOT_IND_LOG=1 (GPU-copies indirect dispatch group counts to a host ring, logs vs device limit) | bisecting GPU hangs; indirect group count over limit | Missing (we have OOT_SKIP_SHADERS only). OOT_IND_LOG is the one thing aimed at our "indirect mesh draws not drawn / hang" problem, but only for compute dispatch, not mesh |
| gfx_shader.c KEY_VERSION 13->14, tools/run.py early-retry window 45->25 s | cache bump, test rig | n/a (our key is 10; bump only if we take lowering changes) |

## Not in upstream at all (so no help there)
Anything on mesh/task shaders (NVN2 task-stage hang, DrawMeshTasks/Indirect), bindless texture sampling, depth bias,
GOB order, FSEL fix, exposure/bloom. Upstream docs/bindless.md describes the work we already did.

## Top 3 recommendations to merge (by likely impact)
1. Translator patch 0018 (NaN-defined NMin/NMax/NClamp, F2I saturate + NaN->0). Only upstream change that can alter
   lighting/bloom/exposure numerics. Our white-out + haze could involve an inf or NaN reaching a max()/clamp in the
   luminance/bloom chain (GLSL FMax/FClamp results are undefined with NaN, drivers often pick the NaN side or assume none).
   Cheap, drop-in (patch file applies to Instructions.cs and InstEmitConversion.cs; rebuild via tools/build_xlate.py,
   translator identity changes so every cache key changes). Verify with an A/B dump of the white-out frame via images.txt.
2. Lowerings HSETP2, HMUL2 imm/const forms, HMNMX2 negated operands (lower_hsetp2/lower_hmul2/lower_hmnmx2).
   These are what let the 1080p->1440p upscaler (0a9afab0) and final pass (7034cdb8) lower. If those passes fail in our
   run the last steps of the frame (tonemap/resolve inputs) are skipped or left stale, which could show as
   full-white or stuck frames. First check our dump's "skipped / fail.code" notes for those two hashes; if they
   lower already, drop this item.
3. Diagnostics: vk_dump_note + OOT_DUMP=cs:<id> + MAX_PASSES 1200, and OOT_SKIP_CS (with the !list form) / OOT_IND_LOG.
   Not fixes, but they would show exactly which draws/dispatches vanish in the white-out frames (missing-shader notes
   with code hashes) and let us bisect the task-stage hang by skipping kernels. Independent of the translator, low risk.

Caveat: all comparisons were by grep and reading diffs; nothing was built or run. HSETP2 absence is by grep of the
opcode dispatch (no case 0x234/0x434, no lower_hsetp2).
