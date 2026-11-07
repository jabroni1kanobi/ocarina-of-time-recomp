"""White-out investigation (docs/handoff-whiteout.md): translate every task / mesh program the shader cache has seen
from a REFERENCE lowering (build/lichii-ref/lichii_lower.exe, a dev harness around Lichii's GPL header, kept out of
the repository) instead of runtime/sm86_lower.c, and put the results under the live cache keys. The runtime then
converts and runs them as usual; the sweep gate tells whether our lowering is the fault. Never shipped.

    python tools/dev/lichii_swap.py build      lower, pack, translate, convert-check everything (build/lichii-ref/)
    python tools/dev/lichii_swap.py swap [--only s6|s7|<iid prefix>,...]   put the reference .spv under the cache keys
    python tools/dev/lichii_swap.py restore    put the originals back

The inputs are the cache's in/<iid>.in files (tools/dev/lichii_swap.py build reads build/lichii-ref/in/index.json
written by the extraction in this session). Keys: sha256("TKSK", KEY_VERSION 11, translator identity (oot_xlate.id
mixed with the host options line of the last log), iid).
"""
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lichii_pack  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
REF = ROOT / "build/lichii-ref"
CACHE = ROOT / "build/v131072/shader-cache"
VAL = Path("C:/VulkanSDK/1.4.363.0/Bin/spirv-val.exe")
KEY_VERSION = 11


def layout(ctl, code, stage):
    u32 = lambda o: struct.unpack_from("<I", ctl, o)[0]
    shared = min(max(u32(0x50), u32(0x64)), 0x4000)
    shared = (shared + 15) & ~15
    lay = {"kind": stage, "in": shared, "attr": shared + 0x1000, "local": u32(0x58) & 0xffff}
    if stage == 6:
        lay.update(map=lay["attr"] + 0x1000, maxv=0, maxp=0, lines=0, omap="00" * 17)
        lay["spill"] = lay["map"]
        lay["total"] = lay["spill"] + 0x100
        return lay
    maxv, maxp = struct.unpack_from("<HH", ctl, 0x60)
    maxv, maxp = maxv or 128, maxp or 128
    lines = bin(code[0x35]).count("1") + sum(bin(b).count("1") for b in code[0x36:0x46])
    lines = lines or 4
    lay.update(maxv=maxv, maxp=maxp, lines=lines, omap=code[0x35:0x46].hex())
    lay["map"] = lay["attr"] + (maxv + 31) // 32 * lines * 128
    lay["spill"] = lay["map"] + (4 + 3 * maxp) * 4
    lay["total"] = lay["spill"] + 0x100
    return lay


def xlate_id():
    xid = bytes.fromhex((ROOT / "build/xlate/oot_xlate.id").read_text().strip()[:64])
    logs = sorted((ROOT / "build/logs").glob("*.log"), key=lambda p: p.stat().st_mtime)
    for log in reversed(logs):
        m = re.search(r"host options (.*?): own shader cache keys", log.read_text(errors="replace"))
        if m:
            return hashlib.sha256(xid + ("TKHO " + m.group(1)).encode()).digest()
    return xid


def key_of(iid, xid):
    return hashlib.sha256(b"TKSK" + struct.pack("<I", KEY_VERSION) + xid + bytes.fromhex(iid)).hexdigest()


def build():
    index = json.load(open(REF / "in/index.json"))
    (REF / "xl").mkdir(exist_ok=True)
    jobs, lays = [], {}
    for e in index:
        iid, stage = e["iid"], e["stage"]
        name = f"{iid[:12]}_s{stage}"
        ctl = (REF / f"in/{name}_ctl.bin").read_bytes()
        code = (REF / f"in/{name}_code.bin").read_bytes()
        r = subprocess.run([str(REF / "lichii_lower.exe"), str(REF / f"in/{name}_code.bin"), str(REF / f"in/{name}_ctl.bin"),
                            str(REF / f"{name}.u64"), str(REF / f"{name}.map")], capture_output=True, text=True)
        if r.returncode:
            print(f"{name}: reference lowering failed: {r.stderr.strip()[-200:]}")
            continue
        lay = layout(ctl, code, stage)
        d = (REF / f"{name}.u64").read_bytes()
        words = struct.unpack(f"<{len(d) // 8}Q", d)
        conv = lichii_pack.convert(words, lay["in"], lay["attr"], lay["map"], lay["spill"])
        packed = lichii_pack.pack(conv)
        (REF / f"{name}.code").write_bytes(struct.pack(f"<{len(packed)}Q", *packed))
        print(f"{name}: {len(code) // 16} source -> {len(words)} reference -> {len(conv)} packed; {r.stdout.strip()[-60:]}")
        lays[name] = lay
        jobs.append({"key": name, "stage": "cs", "debug": False, "code": str(REF / f"{name}.code"), "c1": "",
                     "mesh": {k: lay[k] for k in ("kind", "in", "attr", "map", "total", "maxv", "maxp", "lines", "local", "omap")},
                     "shared": lay["total"], "local": [lay["local"], 1, 1]})
    (REF / "jobs.json").write_text(json.dumps(jobs))
    r = subprocess.run([str(ROOT / "build/xlate/oot_xlate.exe"), str(REF / "jobs.json"), str(REF / "xl")], capture_output=True, text=True)
    if r.returncode:
        print("translator:", r.stdout[-400:], r.stderr[-400:])
    ok = 0
    for name, lay in lays.items():
        spv = REF / "xl" / f"{name}.spv"
        if not spv.exists():
            print(f"{name}: no translation")
            continue
        for with_task in ([0, 1] if lay["kind"] == 7 else [0]):
            out = REF / f"{name}_t{with_task}.spv"
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


def selected(only):
    index = json.load(open(REF / "in/index.json"))
    for e in index:
        if only and not any(e["iid"].startswith(o) or o == f"s{e['stage']}" for o in only):
            continue
        yield e


def swap(only):
    xid = xlate_id()
    (REF / "backup").mkdir(exist_ok=True)
    n = 0
    for e in selected(only):
        iid, stage = e["iid"], e["stage"]
        name = f"{iid[:12]}_s{stage}"
        key = key_of(iid, xid)
        src = REF / "xl" / f"{name}.spv"
        dst = CACHE / f"{key}.spv"
        if not src.exists() or not dst.exists():
            print(f"{name}: {'no reference translation' if not src.exists() else 'key ' + key[:12] + ' not in the cache'}")
            continue
        bak = REF / "backup" / f"{key}.spv"
        if not bak.exists():
            shutil.copy2(dst, bak)
            shutil.copy2(CACHE / f"{key}.res.json", REF / "backup" / f"{key}.res.json")
        shutil.copy2(src, dst)
        res = json.load(open(REF / "xl" / f"{name}.res.json"))
        res["key"] = key
        (CACHE / f"{key}.res.json").write_text(json.dumps(res, indent=2))
        print(f"{name} -> {key[:12]} ({src.stat().st_size} bytes over {bak.stat().st_size})")
        n += 1
    print(f"swapped {n}")


def restore():
    n = 0
    for bak in (REF / "backup").glob("*.spv"):
        shutil.copy2(bak, CACHE / bak.name)
        shutil.copy2(REF / "backup" / (bak.stem + ".res.json"), CACHE / (bak.stem + ".res.json"))
        n += 1
    print(f"restored {n}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    only = sys.argv[3].split(",") if len(sys.argv) > 3 and sys.argv[2] == "--only" else []
    {"build": lambda: build(), "swap": lambda: swap(only), "restore": restore}[cmd]()
