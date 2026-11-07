"""Offline check of the task / mesh route (docs/mesh-plan.md) on programs saved under build/logs/meshprog:
lower (build/sm86_lower_main.exe), translate (build/xlate/oot_xlate.exe, batch mode), convert (build/spv_mesh_main.exe),
validate (spirv-val, Vulkan 1.3). Game data stays in build/.

    python tools/dev/mesh_check.py [name ...]      (names like p03_s7; default: every saved program)
"""
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROG = ROOT / "build/logs/meshprog"
OUT = ROOT / "build/meshlow"
VAL = Path("C:/VulkanSDK/1.4.363.0/Bin/spirv-val.exe")


def layout(ctl, code, stage):
    u32 = lambda o: struct.unpack_from("<I", ctl, o)[0]
    shared = min(max(u32(0x50), u32(0x64)), 0x4000)
    shared = (shared + 15) & ~15
    lay = {"kind": stage, "in": shared, "attr": shared + 0x1000, "local": u32(0x58) & 0xffff}
    if stage == 6:
        lay.update(map=lay["attr"] + 0x1000, maxv=0, maxp=0, lines=0, omap="00" * 17)
        lay["total"] = lay["map"] + 0x100   # + the uniform register spill area (sm86_lower.h SM86_UR_SPILL_BYTES)
        return lay
    maxv, maxp = struct.unpack_from("<HH", ctl, 0x60)
    maxv, maxp = maxv or 128, maxp or 128
    lines = bin(code[0x35]).count("1") + sum(bin(b).count("1") for b in code[0x36:0x46])
    lines = lines or 4
    lay.update(maxv=maxv, maxp=maxp, lines=lines, omap=code[0x35:0x46].hex())
    lay["map"] = lay["attr"] + (maxv + 31) // 32 * lines * 128
    lay["total"] = lay["map"] + (4 + 3 * maxp) * 4 + 0x100
    return lay


def main():
    OUT.mkdir(exist_ok=True)
    names = sys.argv[1:] or sorted(p.name[:-8] for p in PROG.glob("*_ctl.bin"))
    jobs, lays = [], {}
    for name in names:
        ctl = (PROG / f"{name}_ctl.bin").read_bytes()
        code = (PROG / f"{name}_code.bin").read_bytes()
        stage = ctl[0x56]
        r = subprocess.run([str(ROOT / "build/sm86_lower_main.exe"), str(PROG / f"{name}_ctl.bin"), str(PROG / f"{name}_code.bin"),
                            str(OUT / f"{name}.code"), str(OUT / f"{name}.c1")], capture_output=True, text=True)
        if r.returncode:
            print(f"{name}: lowering failed: {r.stdout.strip()[-120:]}")
            continue
        lay = layout(ctl, code, stage)
        lays[name] = lay
        c1 = (OUT / f"{name}.c1").read_bytes().hex() if (OUT / f"{name}.c1").exists() else ""
        jobs.append({"key": name, "stage": "cs", "debug": False, "code": str(OUT / f"{name}.code"), "c1": c1,
                     "mesh": lay, "shared": lay["total"], "local": [lay["local"], 1, 1]})
    (OUT / "jobs.json").write_text(json.dumps(jobs))
    r = subprocess.run([str(ROOT / "build/xlate/oot_xlate.exe"), str(OUT / "jobs.json"), str(OUT / "xl")], capture_output=True, text=True)
    if r.returncode:
        print("translator:", r.stdout[-400:], r.stderr[-400:])
    ok = 0
    for name, lay in lays.items():
        spv = OUT / "xl" / f"{name}.spv"
        if not spv.exists():
            print(f"{name}: no translation")
            continue
        for with_task in ([0, 1] if lay["kind"] == 7 else [0]):
            out = OUT / f"{name}_t{with_task}.spv"
            args = [str(ROOT / "build/spv_mesh_main.exe"), str(spv), str(out)] + [str(lay[k]) for k in
                    ("kind", "in", "attr", "map", "total", "maxv", "maxp", "lines", "local", "omap")] + [str(with_task)]
            r = subprocess.run(args, capture_output=True, text=True)
            if r.returncode:
                print(f"{name} t{with_task}: convert: {r.stderr.strip()}")
                continue
            v = subprocess.run([str(VAL), "--target-env", "vulkan1.3", str(out)], capture_output=True, text=True)
            if v.returncode:
                print(f"{name} t{with_task}: spirv-val: {(v.stdout + v.stderr).strip()[:300]}")
            else:
                ok += 1
    print(f"valid: {ok}")


if __name__ == "__main__":
    main()
