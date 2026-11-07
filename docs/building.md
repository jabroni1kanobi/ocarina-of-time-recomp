# Building

From your own dump to a running `oot.exe`, on Windows 10 or 11 (x64). Nothing from the game is in this repository: you
extract your dump into the ignored `build/` folder, the lifter writes the game's code as C into the ignored
`generated/` folder, and the build links it with the runtime. Do not commit or share anything from those folders.

## What you need

- Your own dump of the game: the Switch 2 `.nup` (title id `04004860224DEC00`, version 1.1.1). The `.nup` is not
  encrypted: no keys are needed.
- A GPU with Vulkan 1.3 and `VK_EXT_mesh_shader`, 8 GB of video memory or more (tested: AMD Radeon RX 9070, 16 GB).
- About 8 GB of free disk on the drive of the dump (the RomFS is served from the dump itself through a hard link, so
  the clone must be on the same drive; otherwise it is copied, 25 GB more).
- [MSYS2](https://www.msys2.org/), with these UCRT64 packages (from an MSYS2 shell):

      pacman -S --needed git patch mingw-w64-ucrt-x86_64-{clang,lld,gcc,ninja,sdl3,opus,dav1d,openh264,vulkan-headers,vulkan-loader,vulkan-memory-allocator,shaderc,spirv-tools}

  `C:\msys64\ucrt64\bin` must be on `PATH` (the build looks for `clang`, `ld.lld` and `ninja` there).
- Python 3.12 and Python 3.14 for Windows (python.org; the `py` launcher picks the version). 3.14 is needed by
  `tools/extract_nup.py` for the standard library's `compression.zstd`; with [uv](https://docs.astral.sh/uv/),
  `uv run --python 3.14 tools\extract_nup.py ...` does instead of `py -3.14`.
- [Git for Windows](https://git-scm.com/download/win) first on `PATH`: step 2 fetches zstd with it (an MSYS2 git
  waited forever on that fetch here).
- The .NET 10 SDK, and the Visual Studio Build Tools with the C++ workload (the shader translator is published as a
  native executable).

## Steps

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

6. Run `build\v131072\oot.exe`. The first start translates the shaders it meets and compiles their pipelines (cached
   under `build\v131072`), so the first minutes and every first visit of an area stutter; later starts do not. Saves
   go to `build\v131072\profile` (`--profile <dir>` for another folder). Settings: the in-game menu, or
   `oot.ini.example` copied to `build\v131072\oot.ini`.

## Checks

- `build\v131072\oot.exe --selftest` and `--probe-astc` (the GPU texture decoder and the BC7 encoder) must pass.
- `python tools\check_content.py` must pass before every commit (the pre-commit hook runs it on staged files).
- Frame rate: `OOT_TRACE=perf` prints a line every 60 frames; `tools\dev\perf_summary.py <log>` summarises a run.
  `tools\dev\run_oot.ps1` runs the game unattended with input scripts (`tools\dev\inputs\`, which assume a save of
  your own in the right place) and screenshots; `docs\phase3.md` has the gates.
