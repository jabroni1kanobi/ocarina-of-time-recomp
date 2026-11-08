"""One command from your own dump to a playable build: every step of docs/building.md, in order, with the tools of
this repository, built from source on your PC (nothing prebuilt is downloaded apart from the toolchains you install).

    py -3.12 tools\\make_game.py --nup "D:\\Games\\your-dump.nup"            # build (again: only what changed)
    py -3.12 tools\\make_game.py --nup "D:\\Games\\your-dump.nup" --check    # check the dump's version, build nothing
    py -3.12 tools\\make_game.py --nup ... --shortcut                         # also a Desktop shortcut to the game

Steps: the prerequisites (all missing ones listed at once, with what to install); the Python environment .venv-win;
config.local.toml; the ZBIC decoder (toolchain/); the executables out of the .nup, checked against
tools/data/supported.json; the RomFS (served from the .nup through a hard link when the repository is on the dump's
drive, else extracted: 26 GB more); the lift to C (generated/); the shader translator (build/xlate); oot.exe and the
launcher "Play Ocarina of Time.exe" (build/v131072); oot.exe --selftest. A step whose output is up to date is skipped.

Standard library only: it runs before anything is set up. Everything it writes stays in this folder's ignored build/,
generated/, toolchain/ and .venv-win/, and nothing from the game leaves your PC: never share those folders.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "v131072"
MSYS = Path(os.environ.get("MSYS2_ROOT", r"C:\msys64"))
UCRT = MSYS / "ucrt64"
VENV = ROOT / ".venv-win"
PY = VENV / "Scripts" / "python.exe"
EXTRACTED = ROOT / "build" / "extracted" / VERSION
GEN = ROOT / "generated" / VERSION
OUT = ROOT / "build" / VERSION
LAUNCHER = OUT / "Play Ocarina of Time.exe"   # not OoT.exe: Windows ignores case, that would be oot.exe itself
PACKAGES = ["clang", "lld", "gcc", "ninja", "sdl3", "opus", "dav1d", "openh264", "vulkan-headers", "vulkan-loader",
            "vulkan-memory-allocator"]
# what each MSYS2 package leaves that the build needs (the package name for the install line)
NEEDS = [("bin/clang.exe", "clang"), ("bin/ld.lld.exe", "lld"), ("bin/gcc.exe", "gcc"), ("bin/ninja.exe", "ninja"),
         ("include/SDL3/SDL.h", "sdl3"), ("include/opus/opus.h", "opus"), ("include/dav1d/dav1d.h", "dav1d"),
         ("include/wels/codec_api.h", "openh264"), ("include/vulkan/vulkan.h", "vulkan-headers"),
         ("lib/libvulkan-1.dll.a", "vulkan-loader"), ("include/vma/vk_mem_alloc.h", "vulkan-memory-allocator")]
GB = 1 << 30


def say(msg):
    print(msg, flush=True)


def stop(msg):
    say("\n" + msg)
    sys.exit(1)


def tool_env():
    """PATH with MSYS2 UCRT64 and Git for Windows first (an MSYS2 git hung on the zstd fetch, docs/building.md)"""
    env = dict(os.environ)
    first = [str(UCRT / "bin")]
    git = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "cmd"
    if (git / "git.exe").is_file():
        first.insert(0, str(git))
    env["PATH"] = os.pathsep.join(first + [env.get("PATH", "")])
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def run(step, cmd, **kw):
    t0 = time.time()
    say(f"\n== {step}")
    r = subprocess.run([str(c) for c in cmd], cwd=ROOT, env=tool_env(), **kw)
    if r.returncode:
        stop(f"FAILED: {step} (exit {r.returncode}). The lines above say why; run the same command again after fixing it.")
    say(f"   done in {time.time() - t0:.0f} s")


def same_drive(a, b):
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def preflight(nup):
    """Every missing prerequisite at once, with what to install"""
    missing = []
    if os.name != "nt":
        stop("This builder is for Windows 10 or 11 (x64).")
    if sys.version_info < (3, 12):
        missing.append(f"Python 3.12 or newer (this is {sys.version.split()[0]}): https://www.python.org/downloads/")
    if not nup.is_file():
        stop(f"No file at {nup}: give the path of your own .nup with --nup.")
    if nup.suffix.lower() != ".nup":
        stop(f"{nup.name} is not a .nup: this build reads the Switch 2 dump's .nup container.")
    lack = sorted({pkg for rel, pkg in NEEDS if not (UCRT / rel).is_file()})
    if not UCRT.is_dir():
        missing.append(f"MSYS2 at {MSYS} (https://www.msys2.org/; another folder: set MSYS2_ROOT)")
    if lack or not UCRT.is_dir():
        missing.append("MSYS2 UCRT64 packages, from an MSYS2 shell:\n      pacman -S --needed " +
                       " ".join("mingw-w64-ucrt-x86_64-" + p for p in (lack or PACKAGES)))
    dotnet = shutil.which("dotnet")
    sdks = subprocess.run([dotnet, "--list-sdks"], capture_output=True, text=True).stdout if dotnet else ""
    if not any(line.startswith("10.") for line in sdks.splitlines()):
        missing.append(".NET 10 SDK: https://dotnet.microsoft.com/download/dotnet/10.0 (the shader translator)")
    if not shutil.which("git", path=tool_env()["PATH"]):
        missing.append("Git for Windows: https://git-scm.com/download/win (step 2 fetches zstd's source)")
    free = shutil.disk_usage(ROOT).free
    inplace = same_drive(nup, ROOT)
    need = 8 * GB if inplace else 36 * GB
    if free < need:
        missing.append(f"{need / GB:.0f} GB free on this folder's drive ({free / GB:.1f} GB now)" +
                       ("" if inplace else ": the dump is on another drive, so its 26 GB of game files are copied. "
                        "Put this folder on the dump's drive to need only 8 GB"))
    if missing:
        stop("Missing before the build can start:\n  - " + "\n  - ".join(missing))
    say(f"prerequisites: ok (MSYS2 {MSYS}, .NET 10, Git; {free / GB:.0f} GB free; RomFS "
        f"{'served from the .nup in place' if inplace else 'copied (the dump is on another drive)'})")
    return inplace


def python_env():
    req = (ROOT / "tools" / "requirements.txt").read_bytes()
    stamp = VENV / ".make_game-requirements"
    key = hashlib.sha256(req).hexdigest()
    if PY.is_file() and stamp.is_file() and stamp.read_text() == key:
        return
    if not PY.is_file():
        run("Python environment (.venv-win)", [sys.executable, "-m", "venv", VENV])
    if subprocess.run([PY, "-m", "pip", "--version"], capture_output=True).returncode:   # (a venv made by uv has none)
        run("pip for the Python environment", [PY, "-m", "ensurepip", "--upgrade"])
    run("Python packages (tools/requirements.txt)", [PY, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                                                     "-r", ROOT / "tools" / "requirements.txt"])
    stamp.write_text(key)


def config(nup):
    """config.local.toml with [input] base = the dump; the rest from config.example.toml"""
    path = ROOT / "config.local.toml"
    if "'" in str(nup):
        stop("The dump's path contains a ' character, which the config cannot hold: rename the folder or the file.")
    line = f"base = '{nup}'"
    lines = (path if path.is_file() else ROOT / "config.example.toml").read_text(encoding="utf-8").splitlines()
    out = [line if l.strip().startswith("base") and l.split("=")[0].strip() == "base" else l for l in lines]
    if out != lines or not path.is_file():
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
        say(f"config.local.toml: base = {nup}")


def check_version():
    want = json.loads((ROOT / "tools" / "data" / "supported.json").read_text())
    man = json.loads((EXTRACTED / "extraction-manifest.json").read_text())
    have = {f["name"]: f["sha256"] for f in man["files"]}
    bad = [n for n, h in want["exefs_sha256"].items() if have.get(n) != h]
    if man["title_id"].upper() != want["title_id"] or man["version"] != want["version"] or bad:
        stop(f"This dump is title {man['title_id']} version {man.get('display_version', man['version'])}; this build "
             f"supports {want['title_id']} {want['display_version']} (with its update installed){' only: ' + ', '.join(bad) + ' differ' if bad else ''}.")
    say(f"game version: ok ({want['display_version']}, every executable's SHA-256 matches)")


def extract(nup):
    man = EXTRACTED / "extraction-manifest.json"
    if man.is_file():
        m = json.loads(man.read_text())
        if Path(m.get("source", "")) == nup and m.get("source_size") == nup.stat().st_size and \
                all((EXTRACTED / "exefs" / f["name"]).is_file() for f in m["files"]):
            say("executables: already extracted")
            return
    run("Executables out of the .nup (checked against the hashes the .nup holds)",
        [PY, ROOT / "tools" / "extract_nup.py", "--exefs-only", "--verify"])


def romfs(nup, inplace):
    image = EXTRACTED / "romfs" / "romfs.bin"
    if image.is_file() and (EXTRACTED / "romfs" / "index.json").is_file():
        if not inplace or os.path.samefile(image, nup):
            say("game files (RomFS): ready")
            return
    if inplace:
        run("Game files: served from the .nup in place (a hard link; the compressed files unpacked, about 2.8 GB)",
            [PY, ROOT / "tools" / "nup_romfs_inplace.py"])
    else:
        run("Game files: extracted (26 GB; the dump is on another drive)",
            [PY, ROOT / "tools" / "extract_nup.py", "--romfs-only", "--verify"])


def lift(jobs):
    """generated/ from the executables; skipped while the lifter and the executables are as they were"""
    h = hashlib.sha256()
    for f in sorted((ROOT / "recomp").glob("*.py")) + [EXTRACTED / "extraction-manifest.json"]:
        h.update(f.name.encode() + f.read_bytes())
    stamp = GEN / ".make_game-stamp"
    if stamp.is_file() and stamp.read_text() == h.hexdigest():
        say("game code (generated/): up to date")
        return
    run("Game code lifted to C (generated/, about 1.2 GB)",
        [PY, ROOT / "recomp" / "gen.py", "--exefs", EXTRACTED / "exefs", "--out", GEN, "--jobs", jobs])
    stamp.write_text(h.hexdigest())


def translator(portable):
    exe, ident = ROOT / "build" / "xlate" / "oot_xlate.exe", ROOT / "build" / "xlate" / "oot_xlate.id"
    if exe.is_file() and ident.is_file():
        want = subprocess.run([PY, "-c", "import sys; sys.path.insert(0, 'tools'); import build_xlate; "
                               "print(build_xlate.identity())"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        if want and ident.read_text().strip() == want:
            say("shader translator: up to date")
            return
    run("Shader translator (build/xlate)", [PY, ROOT / "tools" / "build_xlate.py"] + (["--portable"] if portable else []))


def game(jobs):
    run("The game (build/v131072/oot.exe; a few minutes the first time)",
        [PY, ROOT / "tools" / "build_game.py", "--jobs", jobs])
    src = ROOT / "tools" / "play" / "oot_play.c"
    if not LAUNCHER.is_file() or LAUNCHER.stat().st_mtime < src.stat().st_mtime:
        run("The launcher (Play Ocarina of Time.exe)",
            [UCRT / "bin" / "clang.exe", "-O2", "-mwindows", "-municode", src, "-o", LAUNCHER])
    r = subprocess.run([OUT / "oot.exe", "--selftest"], cwd=OUT, capture_output=True, text=True, env=tool_env())
    if r.returncode:
        stop("oot.exe --selftest failed:\n" + "\n".join(l for l in r.stdout.splitlines() if "FAIL" in l))
    say("self-test: passed")


def shortcut():
    desk = Path(os.environ["USERPROFILE"]) / "Desktop"
    ps = (f"$w = New-Object -ComObject WScript.Shell; $d = [Environment]::GetFolderPath('Desktop'); "
          f"$s = $w.CreateShortcut((Join-Path $d 'Ocarina of Time (Recomp).lnk')); $s.TargetPath = '{LAUNCHER}'; "
          f"$s.WorkingDirectory = '{OUT}'; $s.IconLocation = '{OUT / 'oot.exe'},0'; $s.Save()")
    if subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps]).returncode == 0:
        say(f"Desktop shortcut: Ocarina of Time (Recomp) (Desktop: {desk.parent.name}\\...)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--nup", required=True, type=Path, help="your own dump of the game (.nup)")
    ap.add_argument("--jobs", type=int, default=max(2, min(8, (os.cpu_count() or 4) // 2)),
                    help="files compiled at once (default: half the cores, at most 8: the compile needs memory)")
    ap.add_argument("--check", action="store_true", help="check the prerequisites and the dump's version only")
    ap.add_argument("--shortcut", action="store_true", help="also put a shortcut to the game on the Desktop")
    ap.add_argument("--portable-translator", action="store_true",
                    help="build the shader translator without Visual Studio even where it is installed (it is used "
                         "anyway when Visual Studio is missing)")
    a = ap.parse_args()
    nup = a.nup.expanduser().resolve()
    t0 = time.time()
    inplace = preflight(nup)
    python_env()
    config(nup)
    if not (ROOT / "toolchain" / "zbic" / "nso_unzbic.exe").is_file():
        run("ZBIC decoder (zstd 1.5.7 with this project's patch, into toolchain/)", [PY, ROOT / "tools" / "build_zbic.py"])
    extract(nup)
    check_version()
    if a.check:
        say("\nThe dump is the supported version. Run again without --check to build.")
        return
    romfs(nup, inplace)
    lift(str(a.jobs))
    translator(a.portable_translator)
    game(str(a.jobs))
    if a.shortcut:
        shortcut()
    say(f"\nDone in {(time.time() - t0) / 60:.0f} min. Play: {LAUNCHER}\n"
        "The window takes about a minute to appear; the first visit of each area compiles its shaders (brief stutter,\n"
        f"once). Saves: {ROOT / 'build' / 'profile'}. Never share build/, generated/ or toolchain/: they hold your game.")


if __name__ == "__main__":
    main()
