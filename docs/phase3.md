# Phase 3: SM86 shaders

Hand-off written 2026-11-05 at commit `b46d663`, for a fresh session. Read `CLAUDE.md` and `AGENTS.md` first; the
history is in `docs/plan.md` (Phases 0 to 2 and the NVN2 survey). This file says where the port stands, exactly what
blocks it, what is already measured, and the order to do the work in.

## Status (2026-11-07 11:05Z): published

Repository: https://github.com/jabroni1kanobi/ocarina-of-time-recomp (public), `main` at `18def11` before this section,
tag and release `v0.1.0` (source only: the installer is still MP4's and does not read a `.nup`, so no installer is
attached). The content guard ran in CI on the push: success.

Before the first commit: `tools/check_content.py` over the tree (pass), a search of every file for the owner's name,
e-mail, machine paths, OneDrive or Desktop paths, the dump's file name and 32-hex-digit key strings (the hits rewritten
to placeholders); the agent briefs `docs/handoff-*.md` (one machine's paths and its owner's rules) stay local
(`.gitignore`); commits carry the account's GitHub no-reply address. The tracked files hold no `build/`, `generated/`,
`.nup`, `.xci` or `.nsp` (checked with `git ls-files`). The `tools/dev/lichii_*` scripts are ours; the GPL header they
drive stays outside the tree; no GPL text is tracked.

Fresh-clone check (`docs/building.md`, followed step by step in an empty folder): venv, ZBIC decoder, ExeFS extraction
with `--verify`, lift (77 s), translator (15 s), game (441 s), `--probe-astc` pass, a warm-up run and the sweep gate:
**PASS** (81/81, 0.0 %, sheet looked at). Deviations: the RomFS image and overlay folders were linked from the main
tree (the 2.8 GB unpack would have crossed the disk floor; the step is the main tree's own) and Python 3.14 came from uv.
What it found, fixed and pushed: `docs/building.md` was missing (written); an MSYS2 git hung on the zstd fetch (Git for
Windows named); the pre-commit hook found no `python` (falls back to `py`); `--selftest` failed its "beyond the pool"
check since the Switch 2 pool holds MP4's 4 GiB request (now a page past the pool: pass); a checkout 80 characters deep
could not open the RomFS overlay's long names (`fs_wide` now prefixes `\\?\` from 248 characters).

What a player needs: their own `.nup` (no keys), Windows 10/11, a Vulkan 1.3 GPU with `VK_EXT_mesh_shader` and 8 GB or
more, MSYS2 UCRT64 with Clang, Python 3.12 and 3.14, the .NET 10 SDK with the C++ build tools, Git for Windows, about
8 GB free on the dump's drive. Open: the items of the section below (the Link's house door dip, first-visit pipeline
compiles, coverage beyond three scenes), an installer for the `.nup`, the movie network on tensor cores.

## Status (2026-11-07 10:16Z): Link's house back to 60 fps, the 0.5-1 s freezes gone; five runtime fixes

Measured on the default build (no environment switches) with `tools/dev/perf_summary.py <log> --from 3300` (new:
per-window frame times from the `OOT_TRACE=perf` lines, the windows with a frame of 20 ms or more, the slow frames):

| Scene (script) | run | frames | mean | worst frame | note |
|---|---|---|---|---|---|
| Link's house, two full camera turns (`continue_sweep2x.txt`) | perf16 | 4,620 | 59.5 fps | 41.9 ms | was 42 fps with 0.7-1.1 s freezes (perf1-perf8) |
| Goron City shop, camera turn (`friend_slot1.txt`) | goron3 | 3,540 | 60.0 fps | 52.7 ms | |
| Dodongo's Cavern, blue warp room (`friend_slot5.txt`, with shots) | slot5 | 2,400 | 59.9 fps | 74.8 ms | first run there |

Gates on the final build: sweep41 **PASS** (81/81, 0.0 %, sheet looked at: walls, door, lamps right at every angle);
goron2 **PASS** (81/81, 0.0 %) one fix earlier. 10 consecutive scripted starts (`start1`..`start10`, `-Tries 1`): no
crash. The only crash of the day was the profiler's own (fix 6).

What was wrong, in the order found (each found in a profile or a log line, each confirmed by a run that changed when it
alone changed):
1. **`feedback_scan` walked every descriptor of a bindless array on every draw** (`vk_exec_draw.c`): 42 % of the render
   thread (profile perf1, `OOT_PROFILE_RENDER=1 OOT_PROFILE_FRAMES=4300-5500`; the hot loop disassembled), since the
   whole-pool bindings gave the material shaders arrays of thousands of slots. Only slots 0..127 hold handles: the loop
   now visits those. 33 -> 60 fps outside the freezes (perf2). It was not the VRAM mirror (the earlier guess).
2. **Released render targets piled up** (`vk_texture.c sweep_aged`): the game renders into a transient heap at
   0x1500000000 (about 36 targets released and taken back every frame); while the camera turns it places some at new
   offsets and sizes, and the old entries, never asked for again, stayed: 2.6 GB (perf6, new perf line "texture memory
   MiB live/released"). A released entry not taken back within 600 frames now goes (`OOT_TEX_RELEASED_AGE`, frames;
   0 = off). A byte cap on released entries was tried first and rejected: the droppable ones came straight back
   (300 uploads a second) and the 2.6 GB were not droppable (perf5).
3. **ASTC textures are encoded to BC7 by default** (`runtime/shaders/bc7_encode.comp`, `vk_astc.c`, `vk_formats.c`):
   the RX 9070 has no native ASTC, so decoded ASTC sat as RGBA8: 4.8 GB in Link's house, which with the render targets
   held VRAM at 83 % of the OS budget (the budget itself fell from 15.4 to 13.6 GB as we grew). BC7 mode 6 (principal
   axis, all four p-bit pairs, exhaustive indices, one least-squares refit), a quarter of the memory: 1.3 GB. BC3 (the
   older encoder) removed the freezes too (perf9bc) but bands colour gradients. `OOT_ASTC_BC`: 1 BC7 (default), 3 BC3,
   0 RGBA8; the settings toggle is now "Compress ASTC textures (BC7)". `--probe-astc` checks it: every block mode 6, a
   flat block within 1, the PSNR floor; BC7 lowest 20.0 dB on the random-block noise against BC3's 19.3; the ASTC decoder
   still bit-exact (1.74 M texels). Full-resolution crops of q0 (RGBA8) and q1 (BC7) side by side: no visible blocks
   or banding (not the same camera position: runs are not frame-identical).
4. **New VMA blocks stalled the render thread** (`vk_headroom.c` spare pools, `vk_hr_create_image`/`_buffer`): a
   256 MiB `vkAllocateMemory` sometimes takes 0.5-3 s in the kernel (seen at 8.4 of 15.4 GB: not only pressure), and
   VMA makes that call inside the block vector's lock that every allocation of the memory type takes, so the
   background headroom thread did not help (profile perf11: the render thread waited 513 ms on that lock). The
   headroom thread now also keeps device-only memory in custom pools of one block each (made under their own lock) and
   textures, ASTC scratch and mirrors go there first; a full pool fails at once. It keeps 2 GiB free in them
   (`OOT_VRAM_POOL_FREE_MB`), most of it made while the game loads, within the 85 % budget rule. Each pool creation is
   logged with its time and frame: 5-9 ms in play, the slow ones (0.9 and 3.1 s) during the scene load (perf16).
   `OOT_VRAM_POOLS=0` turns them off.
5. **Mesh pipelines were built on the render thread** (`vk_pipeline.c pipe_miss`): task/mesh pipelines have no
   pipeline libraries, so every new one was built whole inside the draw: 267-688 ms freezes while the camera turned in
   the Goron City shop on a cold driver cache (slot1, "blocked pipeline" in the slow-frame lines). They now go to a
   worker like the others (fast link marked tried) and the draw is skipped meanwhile; one-shot and full-screen draws
   keep their bounded wait. `OOT_MESH_ASYNC=0` builds them inline. Not yet seen on a cold cache (the driver's cache,
   outside the tree, already holds every pipeline of these scenes): the first new scene will show it.
6. Dev tool: the sampling profiler's slow-frame mode crashed reading a thread's stack (`prof_win.c`, memcpy on a page
   that changed after VirtualQuery); it reads with ReadProcessMemory now.

Also: `tools/build_shaders.py` finds glslc in the Vulkan SDK when MSYS2 has none; new perf lines (texture memory by kind,
released render targets taken back, VMA blocks against allocations); `tools/dev/perf_summary.py`.

The parallel tree's copy of 2026-11-07 02:31 local (not public) was compared file by file: it is behind this tree on gameplay; its translator patches are ours under other numbers except
0016 (HMMA on cooperative matrices, measured on an RTX 4070's 16x8x16 shape). Not taken: this tree bypasses the movie
upscaler network by default (`OOT_MOVIE_NET=0`, the cinematics play at 60 fps unfiltered), and the RX 9070's
cooperative-matrix shapes are not known to include 16x8x16; port it with its `--probe-coopmat` when the network is
wanted. Its full PRMT (all modes; ours: index mode) is a candidate translator patch once a census shows the game uses
the other modes.

Open (performance):
- The door side of Link's house runs 51-57 fps for about 3 s in some runs (replay 14-17 ms against 5.5 elsewhere; perf14
  both turns, perf17 none). Render-thread profile perf17: no lock waits; per-draw shader lookups hash the code
  (`req_init` -> `rt_sha256`, ~0.5 ms a frame) and one render target is deswizzled and uploaded again every frame
  (`resolve_target` -> `tex_bl_copy`, ~1 ms a frame, the "re-uploaded" count). Both are the next items.
- Single frames of 25-50 ms in about one window in five (shader and texture first use); the definition of done's "max
  under 20 ms in every window" is not met: steady windows show 19-22 ms maxima (present pacing jitter; not looked into).
- A brand-new scene on a cold driver cache still compiles pipelines (now in the background: brief pop-in, no freeze).

Open (coverage): only Link's house, Goron City (shop) and Dodongo's Cavern (blue warp room) are measured. The scripted
walk out of Link's house did not leave the room (house_exit.txt, exit1/exit2: Link only walks once the scene has faded
in, after about frame 4300; exit2 then started in a different camera with no response to input, not understood). Kokiri
Forest, the Deku Tree, Hyrule Field by day and night, the pause menu, saving, a cutscene in the engine and a fight need
either the owner's saves there or recorded play (the input recorder writes a played session as a script).

### Owner's note (2026-11-07)
- Run: `tools\dev\run_oot.ps1 -Seconds 115 -Name x -Tries 1 -Extra @('--profile',"$r\build\profile-user-copy") -Env
  @{OOT_INPUT_SCRIPT="$r\build\logs\continue_sweep2x.txt"}` and read it with `python tools\dev\perf_summary.py
  build\logs\x.log --from 3300`; the gate as in the gate below (Status 2026-11-07 07:40Z and earlier).
- Expect: the title and cinematics at 60 fps (cinematics unfiltered: the upscaler network is skipped); Link's house,
  Goron City and Dodongo's Cavern at 60 fps with no freeze; textures are BC7 now (Settings: "Compress ASTC textures").
- Known: a dip to about 52-57 fps for a few seconds facing the door in Link's house in some runs; the first visit of a
  new area may show objects popping in for a moment while their shaders compile (once per PC); only the scenes above
  were tested.

Gate on the default build: sweep39 **PASS** (81 shots, worst 0.0 % blown; sheet `build/logs/sweep39_sheet.png`: the full
camera turn in Link's house, walls, door and lamps right at every angle). Goron City (`--profile build/profile-friend`,
`continue_load.txt`): goron1 **PASS** (79 shots, 0.0 %), 60 fps, GPU 3.6 ms. No dumps left (34 GB free).

What was wrong, in the order found (each Confirmed by a gate run that changed when it alone changed):
1. **Conditional renders evaluated on the CPU** (section 06:25Z below): the room's chunks were skipped, the sky showed.
   `vk_exec_xfer.c`: a condition on samples-passed counters renders. sweep27force 4/81 -> sweep28.
2. **The lit wall material follows GPU pointers held in its storage block** (phase3 "Next" item 1): with the walls back
   they drew as yellow striped planks (the translator's global-load fallback reads 0 outside the bound ranges). Fix
   without a translator change, `vk_exec_draw.c`: storage buffers are bound to the end of their backing pool and the
   c0 storage tables carry that extent (`mesh_whole`, `whole_extent`, `c0_table(.., extend)`): the fallback's range
   check then finds the pointer targets (72 KB above the slot, same pool). Write tracking keeps the bound size; a
   whole-extent binding never goes through the scratch copy. All stages (compute kernels follow pointers too).
   `OOT_SSBO_WHOLE=0` restores the bound sizes. sweep29/30: walls right.
3. **The gate's all-white frames were the gate's own hitches.** Every screenshot was a long frame (read-back + filter
   + PNG on the game thread, every 15th present) and the game answers a long frame with white frames (a fresh bindless
   descriptor on those frames, sweep33pool; shots every 2nd frame gave runs of white, sweep36step2; reading every present
   gave none, sweep31ring). `boot.c series_shot` now reads back and filters every present in the range and saves the
   step-th: steady frames, sweep39 PASS. Not a game bug a player at a steady frame rate sees; a real hitch may still
   flash white (the game's own behaviour, to compare on hardware). Ruled out on the way: the fence write-back limit
   (`OOT_WB_FENCE_LIMIT=262144`, sweep38wb), eager pool residency of new slots (kept, `OOT_POOL_EAGER=0` disables;
   it did not change the count), a device finish before the read (sweep35finish).
4. (kept from 06:25Z) diagnostics: `OOT_OQ_LOG`, `OOT_OQ_FORCE`, `OOT_OQ_COND`, `OOT_POOL_LOG=1` (first uses of
   pool ids per frame), `OOT_DUMP_WHEN=white:<count>` (from the ring or the series shots; arms the next frame),
   clear colours in `passes.txt`.
Open: Link's house runs at 42 fps without shots (60 before item 2; replay 15 -> 22.6 ms; `OOT_MEMORY=import` gives
60 fps, so the cost is in the mirror path of the whole-extent bindings, not the GPU); Goron City is at 60. The tonemap
dispatch's "inf/NaN" in images.txt is the dump decoding its RGB9E5 words as r11g11b10f, not a bug.

## Status (2026-11-07 06:25Z): the camera-angle white-out was the conditional-render evaluation; fixed; two bugs behind it

Gate on the default build: sweep28 **31/81** over 10 % (sweep26 before: 50/81). What changed in the picture: no shot is
blown by the sky and its bloom any more (the door half of the turn is dark room, not white glow); every failing shot is a
whole all-white frame (98.2 %, the periodic flicker), which went from one in 4-8 shots to 31 of 81. FAIL, not fixed.

Root cause of the white-out (Observed in dumps + logs, Confirmed by the OOT_OQ_FORCE=2 run sweep27force: 4/81, all four
the all-white frames at the start):
- The door-side frame (dump20, frame 4531, `OOT_DUMP=when` with `continue_sweep_dump.txt`) draws the whole room in the
  depth prepass (pass 0150: 419 draws, 295 index buffers, colour mask 0) but its lit pass (0189/0190, depth EQUAL against
  a copy of the prepass depth) has only 92 draws, 74 index buffers: 221 chunks the prepass drew never get colour. The
  sky/fog full-screen draw (0172) filled those pixels first, so the HDR target is sky (57 nits) where the walls belong and
  the fixed indoor exposure blows the frame out. The bed-side frame (dump21bed) keeps 109 of 119 prepass chunks in its
  lit pass and renders right. It is NOT a mesh-shader problem: a frame has 1-4 mesh draws, the room is vertex draws.
- Why the chunks vanish (`OOT_OQ_LOG=<frame>` in vk_exec_xfer.c): the game does ResetCounter / draw chunk /
  ReportCounter(SAMPLES_PASSED) about 930 times a frame in the prepass, then gates each chunk's lit draw with
  SetRenderEnableConditional(NOT_EQUAL, record). NVN evaluates that on the GPU in order; the replay evaluated it on the
  CPU at record time (`cond_enabled`), when the frame's reports are not executed, from whatever the words held: with the
  256-entry `pending` ring overflowed (~930 reports a frame) and reports from earlier frames, it read 0/0 and skipped:
  989 of 1355 conditions over three frames at the door side.
- Fix (`runtime/vk_exec_xfer.c`): a condition on an address that ever took a samples-passed report renders (an
  address set, `oq_addr_known`); timestamp conditions keep the CPU evaluation. `OOT_OQ_COND=1` restores the old
  evaluation. Diagnostics kept: `OOT_OQ_LOG=<frame>` (resets, reports, per-query samples with pass/program, every
  condition with the words it read, 3 frames), `OOT_OQ_FORCE=1` (every query counts >= 1000) / `=2` (every condition renders).

What the fix exposed (both already in the "Next" list below, now the whole picture):
1. **The lit-pass wall material reads through GPU pointers in its storage slot 1** (item 1 below, not fixed): with the
   walls drawn, the door side shows them as yellow/green striped planks (sweep27force sheet; the owner's screenshot
   of 2026-11-07 06:10Z was this run's window). Loads the translator resolves to 0 => the material's generics are zero.
2. **All-white frames**: 31 of 81 shots now, one in 4-8 before. Most likely the same material: its zeros reach the
   exposure (the exposure-apply dispatch, pass 0218 in dump20, writes 16195 NaN texels and values up to 31744 into the
   lit target). Not proven; next: dump an all-white frame (`OOT_DUMP=when` at a white shot's frame) and read the
   exposure/histogram dispatch outputs in images.txt (NaN column) before the composite.
Order of work now: (1) pointer loads through buffer device addresses (plan in item 1 below), warm-up, gate; (2) if
white frames remain, dump one; (3) Goron City (`--profile build/profile-friend`, `continue_load.txt`), untested today.
Dumps of this session were deleted (36 GB free). Shot folders: `shots-sweep27force`, `shots-sweep28`.

## Status (2026-11-07 05:25Z): the white-out has two root causes found and fixed; walls draw; first half of the sweep still blown

Gate: `tools/dev/run_oot.ps1 ... OOT_INPUT_SCRIPT=continue_sweep.txt OOT_SHOTS=4300-5500:15` + `tools/dev/sweep_score.py`.
Task draws are on by default now (`OOT_MESH_TASK=0` skips them). Gate on the default build, exactly as
the gate below (Status 2026-11-07 07:40Z and earlier) states it: sweep26 **50/81** blown (sweep24 81/81 was the first run after the fix, pipelines
building and walls skipped; sweep25 47/81); the second half of the turn (the bed side) is 0-0.5 % blown apart from single all-white frames
every 4-8 shots that the task-off baseline (sweep14/20/21: 51-54/81) has too (a separate flicker). The first half (the
door side) is still 45-98 % blown: a flat white patch of wall geometry plus the bright side of the room. FAIL, not fixed.

What was wrong (each proven with the CPU oracle `tools/dev/sm86_interp.py`, an SM86 interpreter run on the buffers
`OOT_MESH_CAPTURE` writes, against GPU records that now carry the meshlet id, payload words and every attribute line):
1. **The translator's funnel shift is wrong on AMD** (`tools/xlate/patches/0019-funnel-shift-by-32-is-not-zero-on-amd.patch`,
   translator identity now e25a47ac...): Ryujinx emits `(A >> B) | (C << (32 - B))`; NVIDIA gives 0 for a shift by 32,
   AMD masks shift amounts to 5 bits, so every funnel shift whose amount was 0 produced `A | C`. The game's bit-packed
   vertices and index bytes are read with variable-amount SHF: single components of single lanes were wrong and index
   bytes came out >= 62 (clamped to a vertex the meshlet never wrote: NaN, drawn by AMD as a point at the screen
   centre). Proof: with the Ryujinx formula under AMD masking emulated (`SM86_AMD_SHF=1`) the oracle reproduces the GPU
   output exactly (223/223 triangles, 0 position differences); with the correct semantics it differs in exactly the
   corrupted words. v1 of the patch (two shifts everywhere) broke the handle/address pattern matches of the optimizer
   passes (bloom balls in task-off frames, sweep19); v2 keeps the original shape for constant amounts.
2. **Primitive slots written past the output array** (`runtime/spv_mesh.c`): the walls have 126 primitives, the epilogue
   writes slots lane + 32 j for 4 rounds, so lanes 30/31 stored the unused slots 126/127 past the 126-entry
   PrimitiveTriangleIndicesEXT array; on the owner's AMD GPU that zeroed a vertex position in the driver's output ring and
   every triangle using it became a wedge to the screen centre (the owner's "black hole"; never visible in the records,
   which are written from shared memory). The arrays and OutputVertices/OutputPrimitivesEXT are now declared rounded
   up to whole lane rounds (128); SetMeshOutputsEXT still gets the real counts. Wedges gone (sweep24/25 sheets).
   Also: the epilogue reads every shared word first, barriers, then declares and writes the outputs.
Ruled out, with runs: the SM86 lowering (every task/mesh program lowered by Lichii's lowering as a reference,
`tools/dev/lichii_swap.py`, same result), the uniform blocks (c3/c4 captured: camera and object matrices right), the
mesh->fragment interface (locations match), front-face winding (`OOT_MESH_FRONT_FLIP`), culling triangles that straddle
the camera plane (`OOT_MESH_CULLW`), LDS aliasing of the outputs.

Next (first-half white-out), in this order:
1. **Loads through pointers held in storage blocks** (found, not fixed): the lit-pass 28-line wall material
   (`p74` family, generics g16-g18) reads its material block through five GPU addresses stored in its 64-byte storage
   slot 1 (`0x1af9c8000...`), none inside a bound range. The translator's global-load fallback checks the three bound
   storage buffers only, so those loads give 0 on the GPU (the oracle, with the pointer targets captured as
   `s7 ptr`, follows them). General fix: a per-draw table of memory pools (guest base, size, device address: the
   runtime has `has_buffer_device_address` and VMA's device-address bit already) bound to every stage, and a
   PhysicalStorageBuffer load in the fallback when no bound candidate matches. mesh-plan.md counted 628 unresolved
   stores / 516 loads in compute too. Whether this material is what whitens the door half is NOT proven: the bed half
   renders right with the 23/19/18-line materials, which have no such loads.
2. The periodic all-white frames (one every 4-8 shots, with task draws off too).
Earlier text of this item: the lit HDR target (dump19, pass 0150) shows a flat white polygon of wall geometry (saturated
at sky brightness ~50) and white slivers along wall edges while the rest of the room is right; records show no NaN,
inf or huge attribute values in any line and the 28-line material's generics g16-g18 are all zero on the GPU. Compare
those lines with the oracle (it needs F2I: added, an infinity case to handle), then the fragment side (the white patch's
material and what it samples). The periodic all-white frames are independent of mesh draws (present with task draws off).

Tooling added this session (all dev-only): `OOT_MESH_CAPTURE=<vertices>,<from frame>,<draws>` (what the task/mesh stages
read, under `build/v131072/meshcap`, big ranges deduplicated as `=<n>`), `OOT_MESH_DEBUG` records gated to the captured
draw with meshlet id, payload words 0-7, every attribute line (`OOT_MESH_LINES=40` prints them), a per-record census
(centre/w<=0/NaN/zero, NDC extent, ODD flag), `OOT_MESH_CULLW=1`, `OOT_MESH_FRONT_FLIP=1`; `tools/dev/sm86_interp.py`
(--compare <log>), `tools/dev/lichii_pack.py` + `lichii_swap.py` (reference lowering from `build/lichii-ref/lichii_lower.exe`,
a harness around Lichii's GPL header kept out of the repository). Translator patches: 19 (`tools/build_xlate.py`).

## Status (2026-11-06, later): the title cinematic renders

The title cinematic now goes through the whole chain and is composited into the 2560x1440 frame (OOT_DUMP=300:
Link on Epona crossing a field): the AV1 decode, the game's neural upscaler on emulated tensor cores, its upscale pass,
the composite. Found on the way, in order; each was a hard stop for the picture, and several touched the whole game:

- Tensor ops (`runtime/sm86_lower.c`, lower_hmma / lower_ldsm / lower_movm): HMMA.16816.F16 (all 4,478 of the game's
  HMMAs are this form) as a warp-cooperative expansion, SHFL.IDX of the A and B fragments from the lanes that hold
  them, FP32 FFMA, rounding to half after K = 8 and at the end, about 200 Maxwell instructions in 15 scratch registers
  (a lean 8-register form where a busy point has no 15 free); LDSM .1/.2/.4 and .MT88 as SHFL.IDX of the row addresses
  + LDS; MOVM.16.MT88 as two SHFL + PRMT. Also HFMA2 (0x231, 0x831, with .RELU), HMNMX2 (0x240), F2FP.PACK_AB (0x23e)
  and BAR.SYNC (0xb1d). `tests/sm86_tensor_check.py` runs the output on a 32-lane model decoded with Ryujinx's own
  InstTable patterns: HMMA bit-exact to the FP32 sequence in both forms, LDSM/MOVM/F2FP/HFMA2/HMNMX2 to their
  definitions; two planted bugs (wrong A row, wrong LDSM lane) fail it. All 19 programs of the network lower.
- SUST read its data register as 6 bits (32-37) and the handle's offset from bit 38: wrong for data registers R64 and
  up. The data is a byte at 32-39, the handle a dword index at 40-53 (insn_regs had the same 6-bit read).
- The uniform-predicate swap (lower_one, programs using all 7 predicates) restored every swapped predicate after the
  instruction and merged them all back: PLOP3 P0 = UP0 lost its write, the upscaler's first layer looped forever
  (device lost). Now only uniform ops merge back, and predicates the instruction writes are not restored. 167 corpus
  programs lower differently (identical semantics where there was no conflict). KEY_VERSION 6.
- The translator (`tools/xlate`): its Accessor now logs Ryujinx's warnings into the notes (ILogger.Log was a no-op,
  which hid both of the following). Patch 0012: PRMT was a stub in the vendored Ryujinx (all four forms "not
  implemented"): 1,664 of the 14,363 corpus programs use it (12,031 PRMT, all immediate selectors, index mode). Patch
  0013: a global access whose base the search cannot find checks every 16-byte-aligned c0 word the program reads, as
  the multi-target path does for the ones it finds (before: the load read 0, the store vanished).
- NVN2 compute c0 is packed per program (Sm86Control c0_*: workgroup counts at 0x4/0x8/0xc, push constants at 0x10,
  then +0xb2 storage entries of 16 bytes, +0xb3 texture and +0xb6 image handles of 8, +0xba bytes in all; checked on
  all 270 compute programs: every bank-0 access of 269 lies in its region). vk_c0_fill wrote Switch 1's tables only,
  so every compute program read zero storage descriptors and had no textures or images. `vk_exec_draw.c` c0_fill_nvn2
  writes the packed bank; gfx_shader_nvn2_c0_fixup rewrites the program's resources to the Switch 1 slots the binding
  code knows, once, before its first dispatch. The game binds the network's activations, weights and biases at storage
  slots 0-4 (seen per dispatch).
- nvnCommandBufferPushComputeConstants (NVN2; was a stub): (cmd, 0, offset, size, data) copied at record time into
  OP_PUSH_CS, placed at c0 0x10. The video conversion pushes its YUV matrix, the upscale pass the sizes 1919/1079 and
  2559/1439.
- Compute shared memory: nothing set the job's size, and the translator declared its default 4 KiB; the network's
  layers use up to 21 KiB (control +0x64) and their output came out in striped tiles. Sm86Control.shared, KEY_VERSION 7.
- sRGB storage images: an RGBA8_SRGB image had no STORAGE usage (no GPU stores sRGB), so the upscaler's stores through
  its RGBA8 view went to a dummy image. Such images now take STORAGE with EXTENDED_USAGE; get_view makes storage views
  in the UNORM twin and every view in the sRGB format names its usage without STORAGE (VkTexImage.storage_alias).
- Graphics layout (the largest): Ryujinx's Decoder counts scheduling words from where the instructions start (0x50 for
  graphics), the lowering put them at absolute multiples of 32. Every vertex and fragment program lost instructions 0,
  3, 6, ... and executed the zero scheduling words; the composite's full-screen copy lost its TEX and drew black.
  addr_of now starts graphics groups at 0x50. KEY_VERSION 8.

Open: the cinematic runs at about 5 fps (the network's HMMAs in software: RDNA4 has cooperative matrices,
VK_KHR_cooperative_matrix, which could take HMMA directly); the start-up crash right after `fs mount _npln` (about one
run in three, with OOT_ASYNC_PREEMPT 0, 1 or 2 alike: a wild guest pointer, or the host dying while logging the SDK
abort); 628 stores / 519 loads in some compute programs still find no candidate (addresses loaded from memory); 12
fragment programs with "Failed to find targets for BRX"; one compute program reads c0 0x22c, past its bank.
`--probe-nvn` fails 3 checks that predate this (the NVN2 ABI 231.40 and two pool-window cases), `--selftest` and
`--probe-kernel` one each (expectations from before Phase 2's 12 GB pool and WaitIfEqual64).

## Status (2026-11-05, later the same day)

Steps 1 to 3 of the order of work below are done; step 4 is at 86% of the corpus; step 5 is where the work is.

- The translator builds (`build/xlate/oot_xlate.exe`, `python tools/build_xlate.py` from the Windows venv, 20 s).
  `tools/xlate/Program.cs` now echoes the job's `c1` into the `.res.json` (the host binds the lowered jump tables).
- `tools/shader_census.py` (NVN2 version; `python3.14`, Linux) reads the archives and writes
  `build/v131072/shaders/{ctl,code}/<key>`, `programs.json` and `opcodes.txt`: 14,363 programs (10,994 pixel, 3,106
  vertex, 251 compute, 9 mesh, 3 task) from the four `Shader/*.bfsha.zs`, the `Layout2Shader` bfsha and the two agl
  `.sharcb` of the ApplicationPackage sarc; the ExeFS modules hold none. `--lower` builds `runtime/sm86_lower.c` with
  gcc (`tools/sm86_lower_main.c`) and lowers every program, grouping the failures with the failing instruction in
  `lowered.txt`; `--translate` runs the translator over the lowered ones through WSL interop (`failures.txt`).
  `tools/sm86_dis.py <key>` lists a program's fields. The corpus is regenerated from the RomFS in 2 min; later runs
  reuse `programs.json` (`--extract` reads the archives again).
- The control section's fields are settled (the table below, columns "Reading", and `sm86_control_parse`): size at
  `+0xd4` (the BNSH archives repeat it at `+0x30` and `+0x98`, the agl packages leave those 0), instruction end at
  `+0x38`, jump-table constants at `+0x40` with their length at `+0x3c`, code size at `+0x44`, the stage at byte
  `+0x56` (NVN's enum plus 6 task, 7 mesh), the compute workgroup size at `+0x58`; the tail past `+0xe0` holds a u32
  count and 16-byte records `{BRX instruction offset, entry count, 0x10000, byte offset of the table in c1}`.
  Graphics code starts with the 0x80-byte Turing+ header, whose first 0x50 bytes are the Maxwell header the
  translator reads. The early-fragment-tests flag is not identified (candidates: `+0xa4` bits 0 and 8).
- `runtime/sm86_lower.c` lowers 13,444 of 14,363 programs (93.6%). Registers: the lowering's uniform registers
  and scratch live above the program's own; a uniform register loaded by a single direct `ULDC` (or `UMOV`), or by
  one earlier in the same basic block, is forwarded as that constant and needs no register; when the program leaves
  no room, a liveness analysis over the basic blocks (`insn_regs`, exact destination widths, reads over-stated only
  for texture coordinates) finds scratch among the registers dead at each instruction and a uniform register's
  register among those dead over its span. Uniform predicates take free predicates, or are swapped through a
  register around each instruction naming them. What is left: 656 vertex shaders (skinning: up to 247 registers
  live at once, no adjacent pair free where a load needs one), the surface stores (`SUST`, 86 compute programs),
  `ISBERD`/`ISBEWR` (task and mesh stages), a few rare forms (`lowered.txt`).
- The host (`runtime/gfx_shader.c`, `vk_exec_draw.c`, `vk_dump.c`, the shader probe and the pipeline self-test)
  reads the NVN2 control through `sm86_control_parse`, hashes the control section (its own size) and the whole code
  block for the cache key (`KEY_VERSION` 5, `IN_VERSION` 2, `PRG_VERSION` 2), lowers before translating, and binds
  the lowered program's c1 (`gfx_shader_c1`, uploaded once per shader) as uniform slot 1 instead of the game's, since
  the jump tables' targets moved with the code.

## Status (2026-11-06)

The game plays its title cinematic through a host decoder, and the picture is still black for a reason outside the
shaders lowered so far: the game's video path runs on the tensor cores.

- `runtime/ov_movie22.c` decodes the cinematics with dav1d (linked statically from MSYS2, BSD-2, row in
  `THIRD_PARTY_NOTICES.md`): the title is "video/av01" 1920x1080 at 60 fps, a 64 s stream with a key frame every
  321 samples (`nn::movie::Buffer` flag 4 marks those, not the end of the stream, which is an empty flagged buffer).
  Frames come back to the game as NV12 in the buffer it registered (it reads planes 0 and 1 only), with the
  sample's time stamp. Audio is still dropped. The decode runs on the game's decode thread; the game then runs
  at about 14 fps there, to be profiled (dav1d on 4 threads, the NV12 interleave, or the game waiting on frames).
- The lowering grew: SUST (bindless store, the handle loaded from the constant word), LEA and IMAD.X carries
  through comparisons and predicate adds (the translator ignores Maxwell LEA's carry predicate and X), the
  uniform register budget counts registers read before any definition, uniform pairs are allocated adjacently
  in both the pool and the liveness mode, the swap register for uniform predicates comes from the liveness
  allocator when the pool is full, and only the predicate fields an op has are rewritten. Corpus: 13,514 of 14,363
  lower (94.1%), all translate.
- What blocks the picture: every shader of the video path (19 compute programs, dumped by the host to
  `shader-cache/<key>.fail.{ctl,code}` for `tools/sm86_dis.py`) uses the tensor-core instructions: HMMA (0x03c,
  4,478 instructions), LDSM (0x03b, 1,670) and MOVM (0x03a, 24). They are the game's neural upscaler (the SDK
  list names "SMAA_For_NDSR"; the swapchain is 2560x1440 while the movie is 1080p), and they are not in the RomFS
  archives the census reads (nor in the ExeFS modules; the raw `romfs.bin` has no match either, so they sit in a
  compressed file the census does not visit, or the game builds them). NVN2 device property `0x67`, kept by the
  game as a capability flag and required by its video surface setup, is most likely "supports tensor cores".
  Next: lower HMMA.16816 / 1688 f16 (LDSM's matrix loads into per-lane fragments, the warp-cooperative product
  through SHFL and FFMA, MOVM's transpose) in front of the translator, or find the non-tensor path if the game has
  one (with `0x67` = 0 the player aborted in its surface setup; the shader's decision may lie elsewhere).
- Reaching the game proper: `OOT_INPUT_SCRIPT` (lines "<ticks> A 10") passes the title; the file select and the
  opening are cinematics too, so they need the same path.
- The intermittent death right after the first frame (one run in three, exit 40 or 5): the crash handler's dump
  (`main.c`) caught two. One executed from the thread's own stack while logging a guest `Break` from
  `sdk+0x1a5560` (the SDK abort), the log line itself garbled; the other executed from the heap region of the
  `Cpu` structures with `rt_svc+0x59` (after `svc_body`: the host-depth bookkeeping, `kern_preempt_pending` /
  `kern_host_left`) as the return address and lifted `main+0x3779bc0` below it. Both point at the asynchronous
  preemption (`kernel.c`, the interrupt trampoline that pushes the interrupted thread's registers on its stack).
  Test: `OOT_ASYNC_PREEMPT=0` over several runs; then read the trampoline against a thread suspended inside host
  code.

## Where the port stands

`oot.exe --seconds 15` runs the whole start-up of OoT 1.1.1 without a crash: 82 threads, 10,988 IPC messages, 1,485
file opens, saves mounted, NPLN offline, the audio renderer at 48 kHz, 3,661 shader programs set up through NVN2's
program builder, three frames presented at 2560x1440 (`build/logs/run-45.log`). Then it stalls in the third frame
(51.7 s) because no shader translates:

```
[host] gfx_shader: translation failed (-): bad control section
[host] vk: a stage 0 shader of program 4136 failed to translate: draws with it are skipped
```

Draws with failed programs are skipped, the game waits on GPU work that never completes, nothing is drawn. Everything
below the graphics API is believed to work; every remaining known problem is in this one area or is minor (see "After
the shaders").

## Why every shader fails

The host's shader path (`runtime/gfx_shader.c`, `runtime/vk_exec_draw.c`, `tools/xlate/`) is TotK's, for Switch 1:

- `gfx_shader.c` `req_init()` reads the c1 offset at `control+0x700` and the code length at `control+0x704`, and
  `CONTROL_SIZE` is `0x880`: the cache key, the `.in` files, `build_code_hash()` and the async job copies all take
  0x880 bytes from the control pointer. `vk_dump.c:585` reads `+0x700` too.
- The NVN2 control section is 224, 248 or 280 bytes (`0xe0`, `0xf8`, `0x118`). Reading `+0x700` reads whatever lies
  after it, so the length check fails and every program is rejected before translation.
- The code is Ampere SM86 (the Switch 2's T239 GPU): 128-bit instructions, opcode in the low 12 bits of word 0, no
  Maxwell scheduling words. The vendored translator (`tools/vendor/ryujinx`, Ryujinx 1.3.2, MIT) decodes Maxwell and
  Pascal 64-bit forms only (`src/Ryujinx.Graphics.Shader/Decoders/InstDecoders.cs`).
- The translator itself is not built here yet: the log says `translator identity 0000000000000000 (none: no
  translator)`. `.NET 10.0.403` is installed; `python tools/build_xlate.py` (Windows venv) builds
  `build/xlate/oot_xlate.exe`. MP4's `mp4_xlate.exe` next door is the same source and would do for a first smoke test.

So there are two separate jobs: the control-section layout (small, data-driven) and an SM86 front end (large).

## What is measured about the NVN2 control section

From the decompressed `Shader/Model.Product.110.product.Nin_Ounce_NVN2.bfsha` (320 MB; zstd with the `zs.zsdic`
dictionary from `Pack/ZsDic.pack.zs`): 8,517 blocks found by the magic `0x98761234`, 20,000 of them surveyed by
per-dword variance. The first 0xe0 bytes are the fixed part; the block's own size sits at `+0x30` and is repeated at
`+0x98` and `+0xd4`; the tail after `+0xe0` is 0, 0x18 or 0x38 bytes of extra records.

| Offset | Seen | Reading |
|---|---|---|
| `+0x00` | `0x98761234` | magic, as on Switch 1 |
| `+0x04`, `+0x08`, `+0x0c` | `0xc9`, `0x14`, `0xe0170` in every block | constants (version words) |
| `+0x18`, `+0x1c` | `0x220014`, `0x288040` in every block | constants (SDK 22.x / NVN2 version?) |
| `+0x20..+0x27` | unique per block | a 64-bit hash |
| `+0x28..+0x2f` | 7,832 distinct over 8,517 | a second 64-bit hash, shared by a few blocks |
| `+0x30` | `0xf8` (5,900), `0xe0` (2,177), `0x118` (315) | **control section size**; repeated at `+0x98`, `+0xd4` |
| `+0x38` | 402 values, `0x6e80..0x7000` typical | code size candidate (multiple of 0x80) |
| `+0x3c` | `0x14` (5,900), `0` (1,906), `0x20` (394) | tracks `+0x30`; `+0xdc` has the same classes |
| `+0x40`, `+0x44` | rise with `+0x38` (`0x9e80, 0x9f00, 0xa000`) | sizes with headers, or offsets of c1 and end |
| `+0x4c` | 150 values, `0x5a..0x60` common | register count or similar small per-shader number |
| `+0x50..+0x60` | small flags, `0x10000` common | stage/feature flags |
| `+0x64..+0x70` | `0x3f800000` in a few blocks | floats (1.0f) |
| `+0xa4` | `0x1000001` or `0x1000101` | flags |
| `+0xbc`, `+0xc4`, `+0xc8`, `+0xd8` | `0xe0` always | size of the fixed part |

The survey script is a few lines: find each magic, measure to the next, tabulate each dword. Not yet known and needed
first: which fields are the **code length**, the **c1 offset** (the constant bank the translator needs for `BRX`
jump tables) and the **stage**, and whether an early-fragment-tests flag exists (Switch 1: `control[0x718] & 1`).
Two ways to pin them, both cheap:

1. The archive's own tables. BNSH's shader code section records per program the code and control offsets and sizes
   (TotK's `refs/totk-pc-recomp/tools/shader_census.py`, from MacNativeGames' `prepare_shader_seed.py`, MIT, parses
   Switch 1 BNSH; the container is unchanged here). Match those sizes against `+0x38`/`+0x40`/`+0x44`.
2. The game at run time. `prog_set_shaders()` (`nvn_api_obj.c`) logs `data0`; add a once-only dump of the 0x120 bytes
   behind `control` and the size of the pool mapping behind `data`, and compare with the bytes that follow the code
   (the program's own end is where the code size says).

Note the game hands over the Switch 1 `NVNshaderData` form, `{data, control}` of 16 bytes
(`nvnProgramBuilderSetShaders`, built on the stack at `main+0x14e2468`), so the host side needs no ABI change.

## What is known about the code

- Every stage ends in a 128-bit `EXIT`, opcode `0x94d`; none has Maxwell's. Instruction words are 16 bytes, the opcode
  is `word0 & 0xfff` (Lichii's `instruction.h` uses exactly this `Bits(0, 12)`).
- Phase 0 measured a 0x60-byte program header before the instructions, against Maxwell's 0x50-byte SPH. Lichii's
  environment reads `sizeof(sph)` (0x50) from the program base for Switch 2 titles too, so settle the header size on
  the corpus before anything else: the first instruction is where a plausible opcode stream begins.
- The translator job format (`tools/xlate/Program.cs`) is "graphics: 0x50-byte SPH + instructions, compute:
  instructions"; `gfx_shader.c` skips `CODE_SKIP 0x30` of NVN code-block header before that for graphics stages.
  Both constants must be checked against the NVN2 layout.

## Approach: lower SM86 to Maxwell in front of the existing translator

Recommended. Lichii (`src/shader_recompiler/frontend/ampere/lower_to_maxwell.h`, GPL-3.0, read for behaviour only) runs
four Switch 2 titles this way: it does not reinterpret the bytes, it emits new Maxwell instructions for each SM86 one,
inserts the scheduling word every three instructions, maps the uniform registers (`UR0..`) onto high general registers
(R216 and up), keeps an address map so branch targets and `BRX` tables survive the re-layout, and hands the result to
the unchanged yuzu Maxwell front end. The alternative, a native SM86 decoder inside Ryujinx's translator, would mean
new decoder structs and emitter signatures for every instruction (Ryujinx's emitters are bound to the 64-bit field
layout), many times the work for the same output.

Ours would be a C module with no dependency on the translator, usable both in `runtime/gfx_shader.c` (lower, then
request the translation of the Maxwell code; cache keys stay over the original bytes) and in an offline tool over the
whole corpus. Sources of truth that may be copied, with a header and a `THIRD_PARTY_NOTICES.md` row:

- Mesa's NAK compiler, `src/nouveau/compiler/nak/sm70_encode.rs` (MIT): the Volta/Turing/Ampere encoding, field by
  field, and the reference Lichii itself names for its encoding knowledge.
- Ryujinx's `Decoders/InstDecoders.cs` and `InstTable.cs` (MIT, already vendored): the Maxwell target encoding.

From Lichii, only behaviour: which SM86 forms Nintendo's compiler emits for these engines, the pitfalls it records in
`NX2_NOTES.md` (row 54: widen until the whole dump lowers; row 93: save and restore P6 around uniform guards; row 121:
translation time), and its test cases under `src/tests/video_core/ampere_*.cpp` as a checklist of forms (uniform
datapath `R2UR`/`UP2UR`/`ULDC`, `LDSM`, `IPA` modes, formatted surfaces, `CALL.REL.NOINC`, 64-bit `UIADD3` carry,
`TMML`, attribute `0x5c`).

## Order of work

1. **Build the translator** (`python tools/build_xlate.py` from the Windows venv), so a lowered shader can be checked
   at once. Confirm with MP4's census shaders if needed.
2. **Corpus tool**: port `refs/totk-pc-recomp/tools/shader_census.py` to the NVN2 archives (`Model.bfsha`, the
   `Layout2Shader`, `ApplicationPackage` sarc, `.vsetb`, fonts; `tools/shader_inventory.py` is MP4's Retro-format
   tool and is replaced, not extended). Output under `build/v131072/shaders/`: control and code per program, plus an
   opcode histogram (`word0 & 0xfff`) over all code. The histogram is the work list for step 4.
3. **Control section** in `gfx_shader.c`: one `ctl_parse()` giving size, code length, c1 offset, stage and flags, used
   by `req_init`, `build_code_hash`, the `.in` writer/reader, the async job copies and `vk_dump.c`; `CONTROL_SIZE`
   becomes the block's own size. Bump `KEY_VERSION`/`IN_VERSION` (the formats change). The `--probe-gfx`-style census
   self-test in `vk_pipeline.c` (`build/v121/shaders`) and `gfx_shader_probe.c` need the new corpus path.
4. **SM86 lowering**, corpus-driven: implement by opcode frequency, with the offline tool reporting the share of
   programs that lower and translate; stop at every unknown form rather than guessing. Hard parts in order: the
   address map (sched words every three instructions change every branch target; `BRX` tables live in c1), the
   uniform datapath, predicates and the P6 guard, then texture and surface forms.
5. **Run the game** with `OOT_TRACE=gfx`. Expect the first pixels, then new failures: NVN2 procs still stubbed
   (`nvn_procs_stub.inc` logs the first three calls of each; 160 return 0, only three were reached so far), state
   objects whose NVN2 layout differs, and whatever frame 3 was waiting for.

## After the shaders

Known and parked, none blocking: the C++ unwinder reads past `subsdk0` into the gap before `sdk` (`QueryMemory`
should report it free); `--probe-ipc`'s sdk heap constructor address is MP4's (`ipc.c` `ctors_143`); the
`--probe-services` override list; installer tables (`installer/layout.py`, `tools/make_release.py`,
`tools/prepare_shaders.py`); `tests/diff/func.py` and `tests/jumptables_check.py` not run; MP4's stale `fnprof_hit`
stub and `"\mp4"` escape to report upstream.

## Working facts

- Build: `/mnt/c/dev/metroid-prime-4-recomp/.venv-win/Scripts/python.exe tools/build_game.py` (257 s), output
  `build/v131072/oot.exe`. Lift: `recomp/gen.py --exefs build/extracted/v131072/exefs --out generated/v131072
  --modules main,subsdk0,sdk`.
- Run, always with WSLENV or every `OOT_*` variable is silently dropped:
  `export WSLENV=OOT_TRACE:OOT_VOLUME:OOT_APP_POOL:OOT_CRASH_PEEK:OOT_BREAK_AT:OOT_BREAK_BRIEF:OOT_BREAK_WATCH;
  OOT_VOLUME=0 OOT_TRACE=gfx build/v131072/oot.exe --seconds 15 > build/logs/run-N.log 2>&1`.
- Debug: `OOT_BREAK_AT=main+0x...`, `OOT_BREAK_WATCH`, `OOT_CRASH_PEEK`, `build/logs/nvn-proc-got.json` (GOT slot to
  proc name), `OOT_TRACE=svc,host,ipc,fs,gfx`.
- Extraction: `python3.14 tools/extract_nup.py --verify` (needs `toolchain/zbic/nso_unzbic` from `tools/build_zbic.py`).
- Lichii (a local checkout, GPL-3.0): `NX2_NOTES.md`, `src/shader_recompiler/frontend/ampere/`,
  `src/video_core/shader_environment.cpp`. Behaviour only, never code.
- Nothing from the game is committed; everything extracted or decompressed stays under `build/` (the decompressed
  bfsha of this session lived in the scratchpad and is gone; regenerate it from the RomFS image).

## Status (2026-11-06 21:59Z): lowering fixes land (14340/14363 programs lower); white-out still NOT fixed

- Sweep gate after this round: default build (task draws off) 53/81 blown (was 55), `OOT_MESH_TASK=1` 81/81. FAIL.
- The wall meshes (64 vertices / 126 primitives, `meshprog/p74_s7v`, `p82_s7v`, 253 registers) failed to lower at
  run time, so their draws were dropped. They lower now, through these sm86_lower.c changes (census of all 14363
  programs, offline: 120 failures -> 23; `tools/dev/mesh_check.py` 72/72 task/mesh modules valid):
  - liveness retry when the static pools run out; split uniform pairs; uniform registers and the uniform predicate
    word spilled to shared memory (task/mesh, one warp: `Sm86Isbe.spill`, 256 bytes after the ISBE regions);
    uniform spans widened over loops and held only over their span (`span_busy`); scattered scratch registers for
    ALU / uniform ops at busy points.
  - Semantics found by comparing with Lichii's lowering (behaviour only): `ur_reads` missed form 2's register at 64
    (USHF 0x499: spans too short); UIMAD.WIDE read its addend as c, c + 1 (wrong for forwarded or split pairs);
    UISETP dropped the signed bit; SHF MaxShift was inverted (U64 as the 32-bit form); ULDC.64 high half and its
    @UPn guard.
  - KEY_VERSION 11: every shader translated again (warm-up run first).
- Still wrong: GPU readback of the 64/126 meshes in the camera pass gives y ~ 115-165 for every vertex (x and z/w
  plausible): the G-buffer depth still shows walls as spikes to the centre. Suspect next: the task payload (per-meshlet
  instance data at 0x418 / 0x818) giving meshes another instance's transform, or the position decode.

## Status (2026-11-06 21:26Z): the camera white-out is task-launched mesh geometry; NOT fixed yet (sweep FAIL)

Gate: `build/logs/continue_sweep.txt` + `OOT_SHOTS='4300-5500:15'` + `tools/dev/sweep_score.py` (no shot over 10 %
blown out). Every run below FAILS it; nothing here is claimed fixed on screen.

- What the white-out is (dumps at frame 4700, Link's house, `OOT_DUMP=when`): with task draws off (default) the house
  walls are not drawn at all and the HDR sky shows through (sweep6: 55/81 shots blown); with `OOT_MESH_TASK=1` the
  walls are drawn as spikes converging on the screen centre (G-buffer depth) and bloom washes the frame (81/81). In
  Link's house only two mesh kinds draw, both task-launched (`vk_mesh: census`): 128 vertices / 64 primitives (quads,
  positions right) and 64 / 126 (the walls: wrong).
- Fixed on the way (offline: `tools/dev/mesh_check.py` 60/60 modules lower, translate, convert and validate):
  - `lower_call`: Maxwell CAL was emitted with predicate bits PT; the translator's decoder then counts it as an
    unconditional branch and drops everything after the first call (p30: the second scan, the primitive count,
    most vertex/index writes). Now bits 16-19 are 0. Programs with a CALL get new cache keys (code hash tag "CAL1").
  - UP2UR (0x883, uniform predicates to a uniform register) lowered (4 task-paired meshes and a task failed on it).
  - Mesh prologue: unwritten triangles are (0,0,0) and unwritten vertices NaN (stale shared memory never draws).
- Ruled out with runs: the VRAM mirror (OOT_MEMORY=import: 50/81), storage buffers cut at their bound size
  (`OOT_SSBO_WHOLE=1`: no change), storage slot binding (all four task/mesh slots bound at draw 3000; `nvn: storage
  buffer bound at stage` log), ISBE layout, SHFL, I2F, the 32-lane pin.
- Readback (`OOT_MESH_DEBUG=1|<vertices>[w]`, spv_mesh.h SPV_MESH_DEBUG_*: mesh records in the pool feedback buffer,
  logged as `vk_mesh: debug record`): the 64/126 meshes in the camera pass output x ~ 70, y ~ 150 for every vertex
  while z = w - 0.2 (the projection of the correct quads): their x/y inputs are wrong. They are the only meshes that use
  the uniform datapath (USHF x31, ULOP3 x25, UIADD3, USEL, UISETP, ULEA, UIMAD); saved as `build/logs/meshprog/p74_s7v`
  etc. Next: the uniform lowering of those ops (and scan_uniform forwarding) against Lichii's behaviour.
- Indirect mesh draws (`DrawMeshTasksIndirect`, ~11 a frame) are still not drawn; Lichii does not draw them either.

## Status (2026-11-06 19:07Z): mesh shading runs (mesh-only programs); Link's house whole from every side

- Route as `docs/mesh-plan.md`; the owner's GPU is an AMD Radeon RX 9070 (VK_EXT_mesh_shader only: no NV
  extension). `sm86_lower.c`: stages 6/7 lower as compute (input header skipped), ISBERD/ISBEWR -> LDS/STS into the
  ISBE regions (`sm86_isbe_layout`), FCHK source modifiers accepted; mesh sizes from control +0x60 (128 vertices, 64
  primitives: each of 32 threads writes 4 vertices, 2 triangles). `gfx_shader.c`: GFX_STAGE_TASK / GFX_STAGE_MESH
  translate as "cs" with the layout echoed back (`Program.cs` "mesh") and converted at load by `spv_mesh.c`
  (GLCompute -> TaskEXT / MeshEXT, SPIR-V 1.4: every global in the interface, BufferBlock -> Block + StorageBuffer,
  prologue / call / epilogue, SetMeshOutputsEXT, PrimitiveTriangleIndicesEXT, EmitMeshTasksEXT; indices clamped to the
  vertices, task counts to 4096). Offline: `tools/dev/mesh_check.py` (lower, translate, convert, spirv-val): 35 of 36
  saved modules valid. `vk_pipeline.c`: mesh pipelines (task bindings after the mesh's in set 0, no vertex input,
  filtered dynamic states, 32-lane subgroups), never in the keys file (a raw task pointer there crashed prewarm).
  `vk_exec*`: 8 NVN stages throughout, task/mesh c0 storage tables at 0x670 / 0x770, `op_draw_mesh` ->
  vkCmdDrawMeshTasksEXT.
- Programs with a task stage hang the GPU (run mesh7: task code alone, mesh not called, loses the device; mesh-only
  programs run: mesh6). Their main loop's exit (P2R of the loop-end predicate) or their data is the suspect. They are
  skipped unless `OOT_MESH_TASK=1`. Indirect mesh draws (`DrawMeshTasksIndirect`, ~11 a frame) are not drawn yet:
  their argument layout is still unread (GPU-only buffer, zeros while the tasks do not run).
- Run mesh8 (the owner's view, turned toward the exit): walls, bed alcove, lanterns, floor, Link, Navi; no white-out;
  GPU 8.7 ms. Switches: `OOT_MESH=0` (no mesh draws), `OOT_MESH_NOCALL=1|6|7` (diagnostic), `OOT_MESH_PROBE=1`.

## Status (2026-11-06 18:3xZ): the remaining white-out is missing mesh-shader geometry

- The owner's screenshots facing away from the curtain in Link's house: the HDR scene before post (run house2, pass
  189) shows the sky where the house's walls belong; exposure set for the dim interior blows the sky to white. The
  scene itself peaks at ~51 (sun); nothing in post is wrong.
- Those walls are mesh-shader draws: per ~5800 frames 65536 `nvnCommandBufferDrawMeshTasksIndirect` (~11 a frame),
  `SetGeometryProcessingMode` ~22 a frame, all stubs. No device property gates them (every `DeviceGetInteger` the game
  reads is logged once now: none is a mesh capability), so there is no fallback path to select.
- Programs: control `+0x56` = 6 task, 7 mesh; the game pairs fragment+mesh and fragment+task+mesh (diagnostic in
  `nvn_api_obj.c prog_set_shaders` saves the first 24 under `build/logs/meshprog`). Their code is ordinary SM86 plus
  `ISBERD` / `ISBEWR` (1850 ISBEWR in 24 programs).
- ISBE layout, from Lichii's NX2_NOTES.md row 89 (GPL: behaviour only): task: `ISBERD.ATTR.SKEW [RZ]` = workgroup
  index, payload `ISBEWR.O.ATTR [rank*stride + 0x10]`, mesh workgroup count `ISBEWR.O.ATTR [0x4]`; mesh: input `[0]` =
  its index, payload from 0x10; vertices in groups of 32, one 128-byte line per output component (mesh header omap
  order), then the per-primitive groups; primitive count `ISBEWR.O.32 [0x3]`, U8 indices from 4; linear byte
  addresses. Their route: VK_EXT_mesh_shader, ISBE ops kept as a pseudo instruction through the lowering, an emulated
  stage buffer (shared memory), every EXIT to one epilogue that emits EmitMeshTasks or SetMeshOutputs + the copy out.
- Next: the same route here (sm86_lower keeps ISBE ops; translator mesh/task stages; runtime mesh pipelines and
  `vkCmdDrawMeshTasksIndirectEXT`).

## Status (2026-11-06 17:41Z): the world is lit and textured (bindless texture pool); Link's house and Goron City right

The owner's screenshot of Link's house (lanterns and roots blown out white, walls dark) had two causes under it:

- **Depth EQUAL** (`vk_pipeline.c vk_pipeline_depth_eq_bias`): the world's colour pass draws with func EQUAL against a
  copy of the prepass depth, from other vertex programs than the prepass; after lowering, translation and a PC
  compile their depths differ by a rounding and every pixel failed. Such draws (EQUAL, no depth write) test LEQUAL
  with a constant bias of `OOT_DEPTH_EQUAL_BIAS` units (default 64; 0 = exact).
- **Bindless textures, the real one.** The world's shaders read 64-bit texture/sampler handles from a table in a
  storage buffer (`(c4[8].z & 0xfffff) + c5[46].w`, 32-byte records, handles at +16/+24: a descriptor heap) and
  Ryujinx's BindlessElimination indexes "pool arrays" with texture id = handle & 0xfffff, sampler id = handle >> 20.
  `Program.cs` sized them 32 (every id clamped to 31) and the runtime bound dummies (`slot -1 ... no handle`): every
  material, shadow and light texture of the world read a dummy. 955 of 6082 cached shaders use pool arrays (samplers,
  2D, 2D array, 2D array shadow; fragment and vertex). Now:
  - `Program.cs`: pool arrays 65536 textures / 4096 samplers, one binding number each (counting the array length
    wrapped the host's 16-bit binding field onto earlier bindings: duplicate bindings, layouts refused).
  - `gfx_shader.c` `pool_rebind`: marks them `GFX_RES_POOL` and moves their SPIR-V variables to set 2
    (`GFX_POOL_SET`) at binding 1 + VkImageViewType (0 samplers); `pool_feedback` adds after every pool texture lookup
    a store of 1 at `feedback[id]` (set 2 binding 8, Uniform + BufferBlock).
  - `vk_pipeline.c`: every pipeline layout has set 2 (an update-after-bind, partly bound layout; an empty set 1 for
    single-set layouts); pool arrays leave the per-draw sets; `spirv_set` / `spirv_rebind` leave set-2 variables.
  - `vk_exec_draw.c` `pool_get` / `pool_sync` / `pool_bind`: one persistent set per (texture pool, sampler pool) pair
    (ids are pool-relative); samplers all; textures only the ids the feedback buffer named (`pool_scan`, a frame of
    latency), and never a slot whose storage the game has unmapped (`mem_guest_mapped`). Resolving every registered
    slot made 12.5 GB of textures resident, 26 GB of commit, garbled glyphs and an out-of-memory stop; resolving freed
    memory made the game fault (runs pool3, glow4).
  Result (runs pool8, house1): Goron City's shop and Link's house fully textured and lit, Link and NPCs right, text
  right; 57-60 fps, GPU 5-7.5 ms, VRAM 5.3-7.9 GB, pool sync 0.001 ms. The feedback stores cost ~1 ms of GPU (early
  depth tests are off in shaders with stores).
- Test saves: `build/profile-user-copy` (the owner's, Link's house) and `build/profile-friend` (Goron City), both with
  `build/logs/continue_load.txt`. The scripted opening (`togame_turn.txt`) does not land reliably (one run dumped the
  pause menu); use the saves.
- Open: the haze in Link's house is thick (game look or too strong fog: compare with a reference); magenta stripes
  seen before the pool work not re-checked; mesh-task draws still stubs; FS-store early-Z cost.

## Status (2026-11-06 15:43Z): fog NaN fixed at its root (FSEL immediates); 09:30 tree merged in part

- **Continue works** with the friend's save (`--profile build/profile-friend`, `build/logs/continue_load.txt`: A at
  1500 / 1800 Continue, 2400 / 2700 the top Load slot, DUMP at 5400): Goron City gameplay at ~74 s, 60 fps, GPU 4.5-5.2 ms.
- **The fog kernel's NaN** (cs e6a61aebc51ff06a) was found on the GPU with `tools/dev/nanbisect.py` (new): the
  cached .spv decompiled by spirv-cross, a NaN check after every float assignment, every imageStore writing the line
  of the first NaN as a code, the .spv swapped under each live cache key (find them by content: the key in older
  notes was stale), one dump. Two causes:
  1. Locals the structurizer leaves unset on one path (the do/while `_136` loop: slice-0 threads skip the block that
     sets `_138.._172`) are OpUndef; zero-initialising them removed 4608 of the marks (a translator change for all
     shaders is still open: give every Function variable a zero initialiser).
  2. The rest: weights `w * (cond ? 1e-5 : 1.0)` came out as `cond ? 1e-5 : 3.6e-40`: 0x0003f800 is 1.0 >> 12.
     `lower_sel` gave FSEL's immediate the *float* 20-bit rule, but Maxwell has only the integer SEL, whose 20-bit
     immediate is sign-extended. Every FSEL of a round constant (1.0, 0.5, 2.0, ...) in every shader produced a
     denormal. Fixed (integer rule, so such immediates go through scratch); `KEY_VERSION` 10.
  Run `fsel1` (Kokiri, fog **not** skipped): fog volumes NaN 0 (the only NaN left in the dump is the packed RGB9E5
  image the dump misreads), Link's house lit correctly with the light shaft, no white-out, 60 fps. `OOT_SKIP_SHADERS`
  is no longer needed.
- From the 09:30 parallel tree (a working copy of 09:30, not public): `tex_deswizzle.c`
  / `tex.h` / `tex_probe.c` (the Switch 2 GOB order for 8- and 16-byte elements, `OOT_GOB_SWITCH2_MIN`; originals in
  `build/merge930/orig`) and translator patch 0017 (the AST optimizer keeps memory reads in place; 17 patches,
  identity 350f575d). Not taken: its tensor-core HMMA (patch 0016 there + vk_caps; we bypass the upscaler), its movie
  clock (ours paces already), `OOT_TEX_RAW`. It still has the FSEL bug and lacks our REDUX/SULD/PLOP3/HMUL2 lowerings.
- A separate copy of 09:48 is HotDevilSauce's status website (ocarina-pc-native-build),
  no code. Its open issues (overexposure on an RX 7900 XTX, the crash at frame ~23,250 at `main+0x1661690`) are ones
  this tree fixed (FSEL immediates; guest setjmp/longjmp). Nothing to take.
- Open: dark interiors (Goron City shop walls black; the sky-view pass 0051/0052 and the environment cube filter
  0054-0069 wrote 0 in the pre-fix dump: re-check after the fix), magenta stripes on some surfaces (Link's house),
  `nvnCommandBufferDrawMeshTasks` / `...Indirect` are stubs and are called in gameplay (mesh stages unimplemented).

## Status (2026-11-06 14:50Z): IN GAMEPLAY

Run `togame21` (New Game, A every 3 s after 200 s, fog kernel skipped): no crash in 480 s, 60 fps throughout, and from
~395 s the gameplay HUD is on screen (three hearts, "Move / Change View" stick prompt): the player has control after
the opening. The picture is overexposed to near white (auto-exposure; the luminance chain held one constant value in
earlier dumps): the next item, with the fog kernel's NaN.

## Status (2026-11-06 14:40Z): guest setjmp / longjmp

- Run `togame20` (480 s, A every 3 s after 200 s): all three tries died at 435-450 s with `rt_dispatch: no function at
  guest pc main+0x1661690: not a function start`. That address follows `bl setjmp` in FreeType's smooth rasterizer
  (`gray_convert_glyph_inner`: the FT_Outline_Funcs table at main+0x48810d0, tag 'outl', error 0x62 Raster_Overflow),
  which longjmps out on every cell-buffer overflow and retries the band smaller: normal operation at a large glyph.
- `recomp/gen.py` now finds the PLT stubs bound to `setjmp` (`import_stubs`) and emits `RT_SETJMP_CALL` there (a host
  `_setjmp(buf, NULL)` in the lifted caller's frame, then a reload of every register local); `runtime/cpu.h` defines
  it; `runtime/guest_setjmp.c` keeps the host jmp_bufs by guest jmp_buf address and replaces `longjmp`: guest
  registers from the SDK layout (x19-x28, x29, x30 ^ 1, sp, d8-d15), x0 = value or 1, host depth restored, host
  longjmp. Three sites in main (two in chunk 0198, one in 0322). Lift: `recomp/gen.py --exefs
  build/extracted/v131072/exefs --out generated/v131072 --modules main` (86 s; unchanged chunks keep their mtime).

## Status (2026-11-06 14:09Z): the in-engine cutscene is on screen (Kokiri Forest, Navi, the Great Deku Tree)

Run `togame19` (fog kernel still skipped): the presented frames show the opening's in-engine scene with its
subtitles: Navi over the forest, the Great Deku Tree. Defects visible: black patches (skipped draws), pink seams,
one shot blown out to white (Navi's glow close-up). What it took, after the 13:37Z items:
- The gameplay upscaler's output stage (`1a18b8a2e00d`, tensor, 2 images) failed to lower on HMUL2 (0x232 / 0x832),
  so its dispatch was dropped before the bypass and the 2560x1440 output never written. HMUL2 now lowers to Maxwell
  HMUL2 R (abs a 44, abs b 30; immediate pair through scratch); 122 of 141 dumped failures lower offline.
- Its only input is the post pass's packed frame: RGB9E5 words (9-bit mantissas at 0, 9, 18, the shared exponent at
  27) stored through the R32_UINT view into an R11G11B10F texture; the network decodes them itself (no view format).
  `get_view` notes such an image (R11G11B10F written through R32_UINT), and the bypass copies it into an E5B9G9R9
  scratch image (same texel size) and blits that, linear, into the 2560x1440 output (`net_decoded`).
- Bypass log per output and input set ("upscaler bypass: ..."), at most 16 lines.

## Status (2026-11-06 13:37Z): three things blacked out the frame; two fixed, the fog kernel isolated

- With the fog kernel skipped (`OOT_SKIP_SHADERS=e6a61aebc51ff06a`, run `togame13`) the whole frame is finite up to
  the post chain: the fog kernel is the only NaN source before it. Still open (see 13:06Z).
- Post pass `cs 59c1968e042e4eec` stores its packed colour as R32_UINT words through a texture view of another
  format into the R11G11B10F target (`view-nvn-fmt 0xb`). `get_view` made every storage view in the image's own
  format; the translator, answered from the view's format, declared a uint image: a type mismatch, NaN and 9
  distinct values. `get_view` now makes a storage view in the view's format when it has the same texel size and
  storage support (the image is MUTABLE_FORMAT; the view's usage is STORAGE only). Run `togame14`: 39,633 values.
- The final black: the game's neural upscaler runs on every gameplay frame too (1920x1080 to 2560x1440). With
  `OOT_MOVIE_NET=0` its layers are skipped, and its last dispatch, storing an image larger than any it samples, ran
  on nothing ("stores a 2560x1440 image without a texture of that size: it runs"). `net_bypass` now fills that
  output with a linear scaled blit of the largest non-integer colour input of the output's aspect ratio (the
  candidates are logged once): a plain bilinear upscale until the network itself is fast enough
  (`VK_KHR_cooperative_matrix`, handoff step 5).
- More lowering: indexed IPA (0x526, Maxwell IPA.IDX), TLD4 with per-tap offsets and fp16 results, STG of 64/128-bit
  zeros, LEA with a negated addend (shift + IADD), swap mode when uniform predicates outnumber the free ones.
  Offline 114 of 141 dumped failures lower; tensor check PASS; KEY_VERSION 9.

## Status (2026-11-06 13:06Z): the start-up crash explained (fiber migration), black surfaces = skipped draws

- The start-up crash (every crash today: `nn::async::detail::SynchronousContinuationList::ExecuteContinuations` with
  zeroed registers, or a host jump to a guest address at the end of `MultiWaitImpl::InternalWaitAnyImpl`; three of
  three tries in run `togame9`): nn::async runs its tasks on guest fibers and a fiber may finish on another host
  thread than the one that started it (`OOT_FIBER_LOG=1`, new, traced it: init on 9432, first run on 17492, done on
  34200, crash on 34200). `kernel.c` read `tcur` (a `_Thread_local`) after a switch or after the fiber's guest code
  through an address clang computed before (no fiber-safe TLS in clang): the finishing fiber switched its new host
  thread into the old thread's own context. Fiber code now reads the thread through `tcur_now()` (noinline);
  `fiber_switch_to` gives the restored host depth to the thread running the context now, not the one it left.
- Black surfaces: the main geometry pass is a depth pre-pass (`chmask 0`); a draw skipped in the colour pass leaves a
  black hole of its own shape. Causes: the 10 programs that still fail to lower, and fragment shaders whose
  pipelines were refused ("uses binding N, which its reflection and layout lack"): the GlobalToStorage fallback reads
  bank 0 from a helper function the structured IR's use marking never sees, so `fp_c0` is declared and bound but not
  reflected. `tools/xlate/Program.cs` now adds every `<stage>_c<slot>` the module declares and binds to `cbufs`
  (translator identity 7a4f21e9a19282bb).
- Depth output register: Maxwell / Ryujinx read it at R(n + 1) (n colour components); the census's 13 depth-writing
  pixel programs finalise R(n + 1). The tile-depth pass's program (omap 0x10f) writes R5 and never R6; copying R(n)
  to R(n + 1) for it (tried in run `togame10`) wrote 2500, a far distance, as depth, and the fog stayed NaN:
  reverted (KEY_VERSION 9). The fog kernel's outputs are 0 or NaN in every cell (systematic: a uniform quantity
  infinite, then times 0?); next check: its uniform blocks (`OOT_DUMP_UBO=1`, c5 read first) and storage inputs.
- Run `togame11` (fiber fix, reflection fix, layers skipped untranslated, full re-translation): no crash on the first
  try, 0 pipeline refusals, 60 fps from New Game to 300 s, GPU 7.1 ms; pass 212 holds the scene with Link drawn (blown
  out) and 864,797 colours; the fog NaN still blacks out the screen from pass 213.
- `TLD` with a constant offset (AOFFI, level zero) lowers (offline 97 of 131 dumped failures).
- Movie network layers (tensor programs with no image) are now skipped before translation while `OOT_MOVIE_NET=0`
  (`net_layer_untranslated`): a full re-translation no longer runs them on the movie's first frames.
- Test profile from a friend's save (6 slots): `build/profile-friend` (a copy; use `--profile`), for Continue runs.

## Status (2026-11-06 12:42Z): Kokiri Forest renders inside the frame; NaN from the fog volumes blacks out the screen

- Run `togame5` hung on black at the opening's first frames: with patch 0014 the movie network's kernels are 9-15 MB
  of SPIR-V and their pipelines took 0.6-7.7 s each on the frame path; the game's movie worker then never resumed
  (main thread in WaitEvent from main+0x11e7a6c, GPU idle). `vk_exec_draw.c` `compute_pipe` now asks
  `vk_pipeline_compute_layout` (new: layout and bindings, pipeline deferred) for tensor programs while
  `OOT_MOVIE_NET=0`, and builds the pipeline only when net_bypass lets the dispatch run. Run `togame6`: no stall.
- More lowering (`sm86_lower.c`): any PLOP3 table (PSETP when it fits, else `plop3_regs`: P2R, shifts, LOP3, R2P),
  FCHK (0x243: always the exact division path), IABS 0xa13, 128/64-bit LDG through a uniform pair alone using its
  own destination registers when no scratch is free (`ur_pair_at`); the liveness scan knows FCHK writes no
  register. Offline 90 of 117 dumped failures lower; in-game failing programs 94 -> 35 -> 21 -> 10. Tensor check PASS.
- The black screen, traced in frame dumps (`images.txt`'s last column counts NaN per image): the fog compute dispatch
  (cs e6a61aebc51ff06a, cache key 124d975d.., 8x9x12 groups, the first NaN of the frame) fills its 3D volumes
  (64x36x96 / 128x72x96 r11g11b10f) with NaN; the fog composite (fs a4910fdd5d21af13) spreads it over the frame, and
  the exposure step averages NaN, so the presented picture is black. Present before the translator merge too
  (togame4's dump). All 208 global loads of that kernel are the translator's run-time range checks against its 6
  bound storage buffers (patch 0013's fallback): a load outside them reads 0. Prime suspect, not yet proven: the
  kernel follows pointers into memory no bound buffer covers (phase3's open "addresses loaded from memory").
- `OOT_SKIP_SHADERS=<dump id>[,..]` (diagnostic, `vk_exec_draw.c` skip_listed; ids are the dump's FNV-1a of the
  SPIR-V): with the composite skipped (run `togame8`), pass 211 holds Kokiri Forest (Link, trees, signpost, grass,
  sky; many surfaces black, magenta seams), then other fogged passes add NaN and the screen is still black.
- Next: count the fallback misses of the fog kernel in a run (or bind the pool as a buffer-device-address range and
  let untraced loads use it); then the black surfaces (G-buffer colour 0 in the geometry passes).

## Status (2026-11-06 12:10Z): past the opening, in the engine's first cutscene; the 3D scene renders, then is wiped

- End of stream fixed (flag 8, see below): run `togame2` left the opening and died 2 s later in
  `nn::prepo::AsyncContext::GetCompletionEvent`: SDK 22's `PlayReport::Send` returns an AsyncContext through x8 and
  our override left it unbuilt. `ov_oot.c` now builds it with the SDK's own `AsyncContext(Result)` (success, no
  service object: the SDK then creates the completion event signalled). Run `togame3`: no abort; the Deku Tree's
  in-engine cutscene runs (subtitles on screen) with the 3D scene black.
- 94 shader programs of that scene failed to lower, and the synchronous path retried each on every draw (24 fps).
  `gfx_shader.c` now remembers lowering failures per code hash for the run (`lower_failed_*`). `sm86_lower.c`:
  REDUX (0x3c4: MAX for a loop count made uniform, SUM, MIN.S32; over the active lanes from VOTE.ANY, lane by lane
  with SHFL.IDX and the identity for inactive lanes, never a butterfly), SULD.P (0xb97 -> Maxwell SULD.B), PLOP3
  (all nine and/or/xor pairs, rows a constant PT input rules out ignored: the game's `a xor b` with c = PT), UPLOP3
  (0x09c, through `up()`), PLOP3 in registers when the swap mode meets Pn and UPn of one number
  (`lower_plop3_regs`), RED with a uniform address pair at an odd register. Offline over the 94 dumped programs
  (`*.fail.code`, the standalone `tools/sm86_lower_main.c`): 0 -> 63 lower. `tests/sm86_tensor_check.py` PASS.
- Run `togame4` (REDUX, SULD and the failure cache only): 60 fps after the movie, 35 programs failing once each,
  frame dump of 193 passes. Pass 162 holds the lit scene (sky, ground, Navi, trees; solid surfaces black); pass 163
  (one full-screen draw, fs `7988f006ab730482`, reads depth, linear depth, three 3D fog volumes, a 480x270 rgba32f
  and the scene colour, writes over the scene) leaves `-inf` in every pixel, and everything after inherits it.
- The parallel tree of 06:05 (a working copy, not public) (the "night" status:
  the cinematic through the neural upscaler at 21 fps; never past it) carried three translator patches we lacked,
  now ours as 0014 (dead-node removal follows only into producers dead through every destination: a SHFL's dead
  predicate copy stripped its lane index), 0015 (plain SHFL.IDX inlined as the subgroup shuffle on 32-lane
  subgroups), 0016 (the driver cbuf base searched along the add chain; `c0_fill_nvn2` now also writes the Maxwell
  tables at 0x310 / 0x210 as that tree's runtime does), and its `vk_pipeline.c` (a declared but unreferenced binding
  is not a layout error). Our PRMT (0012) kept over its 0014. Its other runtime changes duplicate fixes this tree
  has, except diagnostics (`--probe-tensor`, `--probe-cs`, `OOT_MQ_LOG`): not taken yet. Translator identity
  be545c915840cc86 (every cache key changes).

## Status (2026-11-06 11:31Z): handoff steps 1 and 2 done

- Step 1 (`gfx: window focus %d` in `WM_ACTIVATEAPP`): run `focus`, 40 s, no multi-second stall, no focus message at
  all (the background window is never activated). But only 555 frames: 60 fps to frame ~250, then 6-10 fps with the
  GPU at 4.7 ms and the render thread never blocked. The cause was defect B again: the title movie's samples were
  fed and decoded far ahead of time, dav1d at full speed starving the game. (There is no "321-sample title loop":
  flag 4 marks key frames, one every 321 samples; `Dm_Title_0005` has 5,947 frames, `Dm_Kokiri_0095` 7,040.)
- Step 3, run `togame` (300 s): New Game at ~28 s, the opening's decoder took 9,232+ samples, 0 dropped, never
  finalized; shots show movie pictures to ~178 s, then pure black; main thread presenting normally (290 s dump in
  `nvnWindowAcquireTexture`), frame dump at 285 s = 4 passes, the decoded picture itself black (Y 0, UV 0.5). Cause:
  end of stream is flag **8** (the game's test `(GetBufferFlags() >> 3) & 1`, main+0x3b600d0); the shim read flag 4
  (key frame) on an empty buffer as the end, so the game never got an end-of-stream frame and stayed in the movie.
- Step 2 (`ov_movie22.c` pacing): the game ignores a withheld input-ready (first try, a FIFO on our side: 5,849
  samples queued). Its decode loop (`f_7003b5e600`, calls at main+0x3b5e9e4 / +0x3b5ea44) treats any non-zero
  `DecodeAsync` result as "not taken": the sample stays in its own queue and it returns to its event wait. So
  `DecodeAsync` now refuses a sample while 4 pictures are ready (`MOVIE_R_TRY_LATER`), Acquire/Release signal
  input-ready once there is room. Run `pacing2`, 60 s of title: 3,513 frames (59 fps), 3,308 samples in = 3,308
  pictures decoded, 3,304 handed back, ready never above 4, **0 dropped**. Interval spikes of 60-100 ms remain
  (frames 1100-1700, average still ~56 fps): for step 6.
- `--probe-cfg` ok. `--probe-movie` FAILS by design drift: it tests the old VP9 `movie::Decoder` (`ov_movie.c`) and
  requires `V_VP9`; this game's movies are AV1 through `ov_movie22.c`, which it never exercises.
- Stub names for any import: `tools/dev/stubnames.py` (uses `recomp/nso.py` relocations + the lifted stubs' GOT slots).

## Status (2026-11-06 11:15Z): cinematics at 60 fps; what the "black screen" is

The work order from here was an operator brief (not published). In short:

- The movie filter (tensor-core network over every decoded movie frame) is bypassed by default (`OOT_MOVIE_NET=0`,
  `vk_exec_draw.c` `net_bypass`): title and opening movie run at 60.0 fps, GPU 4.2 ms a frame (was 220 ms, 4.5 fps).
- The black screen after New Game is the movie playing without its pictures: `ov_movie22.c` drops 96% of the decoded
  pictures (`4859 buffers taken, 185 frames handed back, 4658 pictures dropped`) because input is never held back.
- The "freezes" were the game pausing while its window was out of focus (the owner clicking elsewhere). Test runs
  go through `tools/dev/run_oot.ps1`, which pins `OOT_ASSUME_FOCUS=1`.
- Gameplay has not been observed yet: the longest clean run ended at 200 s on the title card.
