# Ocarina of Time PC Recomp

Research project: static recompilation of *The Legend of Zelda: Ocarina of Time* (Switch 2, title id
`04004860224DEC00`) into a native Windows program, by the method of
[totk-pc-recomp](https://github.com/valentderah/totk-pc-recomp) as carried to further games by
[fe3h-pc-recomp](https://github.com/jaigner-hub/fe3h-pc-recomp) and
[metroid-prime-4-recomp](https://github.com/jaigner-hub/metroid-prime-4-recomp). The game's code is lifted to C,
compiled for x86-64 and linked with a host runtime that stands in for the console's OS and graphics API on Vulkan.

This is the fourth game on that code and the first Switch 2 title: its shaders are SM86 (Ampere) programs, lowered to
Maxwell in front of the Ryujinx-based translator (`runtime/sm86_lower.c`), and its world is partly drawn with task and
mesh shaders (`runtime/spv_mesh.c`, `VK_EXT_mesh_shader`).

## Status

As of 2026-11-07 (details, measurements and dates in [docs/phase3.md](docs/phase3.md), the earlier phases in
[docs/plan.md](docs/plan.md)):

- The game boots, plays its title cinematics, loads saves and plays. Tested on an AMD Radeon RX 9070: Link's house
  (two full camera turns) at 59.5 fps, the Goron City shop at 60 fps, Dodongo's Cavern (the blue warp room) at 60 fps,
  no freezes; the screenshot gates pass (Link's house through a full camera turn, Goron City); 10 scripted starts
  without a crash.
- Only those scenes have been checked. Kokiri Forest, Hyrule Field, the pause menu, saving, in-engine cutscenes and
  fights have not been played through yet.
- Known: the movie upscaler network is skipped (the cinematics play unfiltered: its tensor-core layers are emulated
  and too slow); a dip to about 52-57 fps for a few seconds facing the door of Link's house in some runs; the first
  visit of an area compiles shaders and pipelines (brief pop-in, once per PC); the window takes about a minute to
  appear after starting.

## Build your own

You need your own dump of the game (the `.nup`) and a Windows PC with a Vulkan 1.3 graphics card. Install MSYS2,
Python 3.12, the .NET 10 SDK and Git for Windows ([docs/building.md](docs/building.md) lists the exact packages), then
in this folder:

    py -3.12 tools\make_game.py --nup "D:\Games\your-dump.nup" --shortcut

One command checks the prerequisites and your dump's version, builds everything from the source in this repository
on your PC (nothing prebuilt is downloaded) and leaves a "Ocarina of Time (Recomp)" shortcut on your Desktop.
[docs/building.md](docs/building.md) has the details and the steps by hand.

This repository contains no game content: no game files, no keys, no generated code, no shader caches. The content
check (`tools/check_content.py`, run by the pre-commit hook and CI) keeps it that way. Do not share what the tools
extract or generate from your dump.

## License

MIT, see [LICENSE](LICENSE). Code from other projects is listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
Reference emulators under the GPL (yuzu, Lichii) were read for behaviour only; no code of theirs is in this tree.
