"""Census of the shader cache's translator notes: what the translator dropped, and where the game uses it.

    py -3.12 -I tools/dev/cache_notes.py [--cache build/v131072/shader-cache] [--passes <passes.txt> ...]
                                        [--find <16-hex dump hash> ...] [--grep <text>]

Every translation leaves its warnings in <key>.res.json "notes" (tools/xlate/Program.cs Accessor.Log): a dropped
instruction, an attribute the translator does not know, a jump table it could not read. The census counts them by text
(numbers replaced by X) and stage, so a systematic defect shows as a count. 2026-11-08: "Failed to find targets for BRX"
(6 files) was the black reflection probe, "Invalid reduction type: F32FtzRn" (72) the dropped float reductions.

A frame dump (runtime/vk_dump.c) names shaders by sh_<hash>, the FNV-1a of the SPIR-V bytes; --find hashes every .spv
of the cache and prints the files of the given hashes, --passes <passes.txt> lists, for each noted shader, the dump
passes that use it (so a note maps to a pass and its targets in images.txt). --grep prints the files whose notes
contain the text, with their stage and the dump passes.
"""
import argparse
import collections
import json
import pathlib
import re
import sys


def fnv1a(data: bytes) -> str:
    h = 0xCBF29CE484222325
    for x in data:
        h = ((h ^ x) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return "%016x" % h


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=pathlib.Path, default=pathlib.Path("build/v131072/shader-cache"))
    ap.add_argument("--passes", type=pathlib.Path, nargs="*", default=[], help="passes.txt files of frame dumps")
    ap.add_argument("--find", nargs="*", default=[], help="dump hashes (sh_<hash>) to look up in the cache")
    ap.add_argument("--grep", help="print the files whose notes contain this text")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    pass_lines = []
    for p in a.passes:
        pass_lines += [line[:72] for line in p.read_text(errors="replace").splitlines() if line.startswith("pass ")]

    want = {w.lower() for w in a.find}
    need_hash = bool(want) or bool(pass_lines) or a.grep is not None
    by_text, by_stage, files = collections.Counter(), collections.Counter(), 0
    for res in sorted(a.cache.glob("*.res.json")):
        try:
            j = json.loads(res.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        files += 1
        notes = j.get("notes", [])
        stage = j.get("stage")
        for note in notes:
            key = re.sub(r"0x[0-9A-Fa-f]+|\d+", "X", note)
            by_text[key] += 1
            by_stage[(stage, key)] += 1
        if not need_hash or (not notes and not want):
            continue
        spv = res.with_name(res.name.replace(".res.json", ".spv"))
        h = fnv1a(spv.read_bytes()) if spv.exists() else "?"
        if h in want:
            print(f"{h} {res.name[:16]} {stage} notes {notes[:2]}")
        if a.grep is not None and any(a.grep in n for n in notes):
            uses = [line for line in pass_lines if h in line]
            print(f"{res.name[:16]} {stage} hash {h} {notes[:3]}")
            for u in uses[:4]:
                print("    " + u)

    print(f"{files} res.json files")
    for text, n in by_text.most_common(40):
        stages = ", ".join(f"{s} {m}" for (s, t), m in by_stage.items() if t == text)
        print(f"{n:6} {text}  [{stages}]")
    for w in want:
        print(f"{w}: looked up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
