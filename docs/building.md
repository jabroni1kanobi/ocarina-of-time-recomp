# Building

From your own dump to a running `oot.exe`, on Windows 10 or 11 (x64). Nothing from the game is in this repository: you
extract your dump into the ignored `build/` folder, the lifter writes the game's code as C into the ignored
`generated/` folder, and the build links it with the runtime. Do not commit or share anything from those folders.

## What you need

- Your own dump of the game: the Switch 2 `.nup` (title id `04004860224DEC00`, version 1.1.1 with its update). The
  `.nup` is not encrypted: no keys are needed. The builder checks every executable against `tools/data/supported.json`.
- A GPU with Vulkan 1.3 and `VK_EXT_mesh_shader`, 8 GB of video memory or more (tested: AMD Radeon RX 9070, 16 GB).
- About 8 GB of free disk if this folder is on the same drive as the dump (the game files are served from the dump
  itself through a hard link), 36 GB if not (they are copied). While the game runs, Windows may also grow its page
  file by up to about 20 GB.
- [MSYS2](https://www.msys2.org/) in `C:\msys64`, with these packages (from the "MSYS2 UCRT64" shell):

      pacman -S --needed mingw-w64-ucrt-x86_64-{clang,lld,gcc,ninja,sdl3,opus,dav1d,openh264,vulkan-headers,vulkan-loader,vulkan-memory-allocator}

- [Python](https://www.python.org/downloads/) 3.12 or newer for Windows (from python.org, not the Microsoft Store).
- The [.NET 10 SDK](https://dotnet.microsoft.com/download/dotnet/10.0) (the shader translator). Visual Studio is not
  needed; with its C++ tools installed the translator is built as a native executable, otherwise as a self-contained
  .NET program (the same output).
- [Git for Windows](https://git-scm.com/download/win) (the builder fetches zstd's source with it).

## The one command

Download or clone this repository (no game data in it), open PowerShell in its folder and run:

    py -3.12 tools\make_game.py --nup "D:\Games\your-dump.nup" --shortcut

It checks everything above first and lists whatever is missing, with what to install. Then it checks that your dump
is the supported version, extracts it, translates the game's code to C, builds the shader translator, the game and a
launcher, runs the game's self-test, and puts "Ocarina of Time (Recomp)" on your Desktop (`--shortcut`). It takes about
15 minutes the first time and can be run again at any time: only what changed is redone. `--check` stops after the
version check. Everything it writes stays in this folder (`build\`, `generated\`, `toolchain\`, `.venv-win\`): that
is your game, never share it.

To play: the Desktop shortcut, or `build\v131072\Play Ocarina of Time.exe`. The window takes about a minute to appear,
and the first visit of each area compiles its shaders once (a brief stutter). Saves go to `build\profile`.

The steps below are what the builder runs, for building by hand or finding out where something failed.

## The steps by hand

In a Windows shell (PowerShell or cmd), from the repository root:

1. Set up once:

       git config core.hooksPath .githooks
       py -3.12 -m venv .venv-win
       .venv-win\Scripts\python -m pip install -r tools\requirements.txt
       copy config.example.toml config.local.toml

   Edit `config.local.toml`: `base` = the full path of your `.nup`. Leave `keys` as it is (not used for a `.nup`).

2. Build the decoder for the Switch 2's compressed executables (fetches zstd 1.5.7 into the ignored `toolchain/`):

       .venv-win\Scripts\python tools\build_zbic.py

3. Extract the executables and check them against the hashes in the dump, then serve the RomFS from the dump in
   place (a hard link plus the compressed files unpacked, about 2.8 GB):

       py -3.14 tools\extract_nup.py --exefs-only --verify
       py -3.14 tools\nup_romfs_inplace.py

   `build\extracted\v131072\romfs\romfs.bin` is then a hard link to your dump: never open it for writing.

4. Lift the game's code to C (about 1.2 GB under `generated\v131072`, some minutes):

       .venv-win\Scripts\python recomp\gen.py --exefs build\extracted\v131072\exefs --out generated\v131072 --jobs 8

5. Build the shader translator (once; `build\xlate`), then the game (`build\v131072\oot.exe`, a few minutes the first
   time, seconds after a runtime change):

       .venv-win\Scripts\python tools\build_xlate.py
       .venv-win\Scripts\python tools\build_game.py --jobs 8

6. Run `build\v131072\oot.exe`, or build the windowless launcher (no console window beside the game; the log goes
   to `build\v131072\logs\oot.log`) and start that:

       clang -O2 -mwindows -municode tools\play\oot_play.c -o "build\v131072\Play Ocarina of Time.exe"

   The first minute after starting shows no window while the RomFS overlay's 40,588 files are checked. The first start translates the shaders it meets and compiles their pipelines (cached
   under `build\v131072`), so the first minutes and every first visit of an area stutter; later starts do not. Saves
   go to `build\v131072\profile` (`--profile <dir>` for another folder). Settings: the in-game menu, or
   `oot.ini.example` copied to `build\v131072\oot.ini`.

## Checks

- `build\v131072\oot.exe --selftest` and `--probe-astc` (the GPU texture decoder and the BC7 encoder) must pass.
- `python tools\check_content.py` must pass before every commit (the pre-commit hook runs it on staged files).
- Frame rate: `OOT_TRACE=perf` prints a line every 60 frames; `tools\dev\perf_summary.py <log>` summarises a run.
  `tools\dev\run_oot.ps1` runs the game unattended with input scripts (`tools\dev\inputs\`, which assume a save of
  your own in the right place) and screenshots; `docs\phase3.md` has the gates.
